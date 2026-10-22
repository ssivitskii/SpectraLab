from __future__ import annotations

import base64
import errno
import fcntl
import hashlib
import json
import os
import re
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SAFE_SPECTRUM_ID = re.compile(r"^[a-f0-9]{32}$")
SAFE_RUN_ID = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}$")


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def atomic_bytes(path: Path, content: bytes) -> None:
    """Publish a complete record; temporary names are unique across processes."""
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_bytes(content)
        with temporary.open("rb") as stream:
            os.fsync(stream.fileno())
        temporary.replace(path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        temporary.unlink(missing_ok=True)


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    atomic_bytes(path, json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8"))


@contextmanager
def directory_lock(directory: Path, name: str = ".storage.lock"):
    directory.mkdir(parents=True, exist_ok=True)
    # POSIX advisory lock coordinates API, worker and maintenance processes on one host.
    with (directory / name).open("a+b") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def paginate(
    records: list[dict[str, Any]],
    *,
    id_field: str,
    limit: int,
    cursor: str | None,
    filters: dict[str, Any],
) -> dict[str, Any]:
    fingerprint = hashlib.sha256(json.dumps(filters, sort_keys=True).encode()).hexdigest()
    boundary = None
    if cursor:
        try:
            payload = json.loads(base64.urlsafe_b64decode(cursor.encode()).decode())
            if payload["filters"] != fingerprint or payload["version"] != 1:
                raise ValueError("Cursor belongs to different filters; restart from the first page")
            boundary = (payload["created_at"], payload["id"])
            if not all(isinstance(item, str) for item in boundary):
                raise ValueError("Invalid cursor boundary")
        except (KeyError, TypeError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
            raise ValueError(
                "Invalid cursor or changed filters; restart from the first page"
            ) from exc
    ordered = sorted(records, key=lambda item: (item["created_at"], item[id_field]), reverse=True)
    if boundary is not None:
        ordered = [item for item in ordered if (item["created_at"], item[id_field]) < boundary]
    page = ordered[:limit]
    next_cursor = None
    if len(ordered) > limit and page:
        next_cursor = base64.urlsafe_b64encode(
            json.dumps(
                {
                    "version": 1,
                    "filters": fingerprint,
                    "created_at": page[-1]["created_at"],
                    "id": page[-1][id_field],
                }
            ).encode()
        ).decode()
    return {"items": page, "next_cursor": next_cursor}


class SpectrumStorage:
    def __init__(self, directory: Path, max_files: int = 500, max_bytes: int = 500_000_000):
        self.directory = directory.resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.max_files = max_files
        self.max_bytes = max_bytes

    def _path(self, spectrum_id: str) -> Path:
        if not SAFE_SPECTRUM_ID.fullmatch(spectrum_id):
            raise ValueError("Invalid spectrum_id")
        path = self.directory / f"{spectrum_id}.json"
        if path.is_symlink() or path.resolve().parent != self.directory or not path.is_file():
            raise FileNotFoundError(f"Spectrum '{spectrum_id}' was not found")
        return path

    def save(self, payload: dict[str, Any], *, record_id: str | None = None) -> dict[str, Any]:
        spectrum_id = record_id or uuid.uuid4().hex
        if not SAFE_SPECTRUM_ID.fullmatch(spectrum_id):
            raise ValueError("Invalid spectrum_id")
        wavelengths = payload.get("wavelength_nm", [])
        record = {
            **payload,
            "spectrum_id": spectrum_id,
            "schema_version": 1,
            "created_at": now_iso(),
            "measurement_metadata": payload.get("measurement_metadata", {}),
            "computed": {
                "point_count": len(wavelengths),
                "wavelength_min_nm": min(wavelengths) if wavelengths else None,
                "wavelength_max_nm": max(wavelengths) if wavelengths else None,
                "snr_db": payload.get("parameters", {}).get("snr_db"),
                "snr_kind": (
                    "configured"
                    if payload.get("origin") == "generated"
                    and payload.get("parameters", {}).get("snr_db") is not None
                    else "unknown"
                ),
            },
        }
        encoded = json.dumps(record, ensure_ascii=False, allow_nan=False).encode()
        with directory_lock(self.directory):
            if record_id and (self.directory / f"{record_id}.json").exists():
                existing = self.get(record_id)
                if existing.get("parameters", {}).get("original_sha256") != payload.get(
                    "parameters", {}
                ).get("original_sha256"):
                    raise ValueError("Stored spectrum idempotency identity mismatch")
                return existing
            files = [p for p in self.directory.glob("*.json") if not p.is_symlink()]
            if (
                len(files) >= self.max_files
                or sum(p.stat().st_size for p in files) + len(encoded) > self.max_bytes
            ):
                raise OSError(errno.ENOSPC, "Configured spectrum storage quota exceeded")
            atomic_json(self.directory / f"{spectrum_id}.json", record)
        return record

    def get(self, spectrum_id: str) -> dict[str, Any]:
        path = self._path(spectrum_id)
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(record, dict) or record.get("spectrum_id") != spectrum_id:
                raise ValueError("Invalid stored spectrum")
            if not isinstance(record.get("wavelength_nm"), list) or not isinstance(
                record.get("intensity"), list
            ):
                raise ValueError("Invalid stored spectrum arrays")
        except (json.JSONDecodeError, UnicodeError, ValueError) as exc:
            raise ValueError("Stored spectrum is damaged; restore this record from backup") from exc
        record.setdefault(
            "created_at", datetime.fromtimestamp(path.stat().st_mtime, UTC).isoformat()
        )
        record.setdefault("measurement_metadata", {})
        record.setdefault("computed", {"point_count": len(record.get("wavelength_nm", []))})
        record.setdefault("schema_version", 1)
        return record

    def list(self, limit: int = 20, cursor: str | None = None) -> dict[str, Any]:
        # Rebuildable sidecar avoids parsing up to 500 MB of arrays on every history request.
        # A missing, damaged or outdated index is repaired from independent record files.
        index_path = self.directory / ".history-index"
        with directory_lock(self.directory):
            try:
                index = json.loads(index_path.read_text())
                if not isinstance(index, dict):
                    index = {}
            except (OSError, ValueError):
                index = {}
            updated = {}
            for path in self.directory.glob("*.json"):
                try:
                    stat = path.stat()
                    signature = [stat.st_size, stat.st_mtime_ns]
                    cached = index.get(path.stem)
                    if (
                        not path.is_symlink()
                        and isinstance(cached, dict)
                        and isinstance(cached.get("summary"), dict)
                        and {
                            "spectrum_id",
                            "created_at",
                            "origin",
                            "reference_source",
                            "measurement_metadata",
                            "computed",
                        }
                        <= cached["summary"].keys()
                        and cached.get("signature") == signature
                    ):
                        updated[path.stem] = cached
                        continue
                    record = self.get(path.stem)
                    summary = {
                        key: record[key]
                        for key in (
                            "spectrum_id",
                            "created_at",
                            "origin",
                            "reference_source",
                            "measurement_metadata",
                            "computed",
                        )
                    }
                    updated[path.stem] = {"signature": signature, "summary": summary}
                except (KeyError, ValueError, OSError):
                    continue
            if updated != index:
                atomic_json(index_path, updated)
        records = [entry["summary"] for entry in updated.values()]
        return paginate(records, id_field="spectrum_id", limit=limit, cursor=cursor, filters={})

    def update_metadata(self, spectrum_id: str, metadata: dict[str, Any]) -> dict[str, Any]:
        with directory_lock(self.directory):
            record = self.get(spectrum_id)
            record["measurement_metadata"] = {**record["measurement_metadata"], **metadata}
            encoded = json.dumps(record, ensure_ascii=False, allow_nan=False).encode()
            current = self._path(spectrum_id)
            size = sum(
                p.stat().st_size for p in self.directory.glob("*.json") if not p.is_symlink()
            )
            if size - current.stat().st_size + len(encoded) > self.max_bytes:
                raise OSError(errno.ENOSPC, "Configured spectrum storage quota exceeded")
            atomic_json(current, record)
        return record

    def delete(self, spectrum_id: str) -> None:
        with directory_lock(self.directory):
            self._path(spectrum_id).unlink()


class ExperimentStorage:
    def __init__(self, directory: Path):
        self.directory = directory.resolve()

    def list(self) -> list[dict[str, Any]]:
        if not self.directory.exists():
            return []
        records = []
        paths = [
            *self.directory.glob("*/result.json"),
            *self.directory.glob("*/runs/*/result.json"),
        ]
        for path in paths:
            try:
                payload = self.get(path.parent.name)
                records.append(
                    {
                        "run_id": payload["run_id"],
                        "status": payload["status"],
                        "created_at": payload["created_at"],
                        "reference_source": payload["reference_source"],
                        "protocol": payload["protocol"],
                        "demo": payload.get("demo", False),
                        "artifact_type": payload.get("artifact_type", "experiment"),
                        "error": payload.get("error"),
                    }
                )
            except (KeyError, ValueError, OSError):
                continue
        return sorted(records, key=lambda item: (item["created_at"], item["run_id"]), reverse=True)

    def page(
        self, limit: int = 20, cursor: str | None = None, **filters: str | None
    ) -> dict[str, Any]:
        records = [
            record
            for record in self.list()
            if all(value is None or record.get(key) == value for key, value in filters.items())
        ]
        return paginate(records, id_field="run_id", limit=limit, cursor=cursor, filters=filters)

    def get(self, run_id: str) -> dict[str, Any]:
        if not SAFE_RUN_ID.fullmatch(run_id):
            raise ValueError("Invalid experiment id")
        path = self.directory / run_id / "result.json"
        if not path.is_file():
            nested = list(self.directory.glob(f"*/runs/{run_id}/result.json"))
            if len(nested) == 1:
                path = nested[0]
            elif len(nested) > 1:
                raise ValueError("Experiment identity is ambiguous")
        if (
            path.is_symlink()
            or any(parent.is_symlink() for parent in path.parents if parent != self.directory)
            or not path.resolve().is_relative_to(self.directory)
            or not path.is_file()
        ):
            raise FileNotFoundError(f"Experiment '{run_id}' was not found")
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(record, dict) or record.get("run_id") != run_id:
                raise ValueError("Invalid stored experiment")
        except (json.JSONDecodeError, UnicodeError, ValueError) as exc:
            raise ValueError("Stored experiment is damaged") from exc
        record.setdefault("demo", False)
        record.setdefault("configuration", {})
        return record


class PredictionStorage:
    def __init__(self, directory: Path, max_files: int = 1000, max_bytes: int = 100_000_000):
        self.directory = directory.resolve()
        self.max_files = max_files
        self.max_bytes = max_bytes

    def save(
        self, result: dict[str, Any], input_record: dict[str, Any], *, record_id: str | None = None
    ) -> dict[str, Any]:
        prediction_id = record_id or uuid.uuid4().hex
        if not SAFE_SPECTRUM_ID.fullmatch(prediction_id):
            raise ValueError("Invalid prediction_id")
        record = {
            "schema_version": 1,
            "prediction_id": prediction_id,
            "created_at": now_iso(),
            "spectrum_id": input_record.get("spectrum_id"),
            **result,
            "input": input_record,
        }
        with directory_lock(self.directory):
            if record_id and (self.directory / f"{record_id}.json").exists():
                existing = self.get(record_id)
                if existing.get("model_identity") != result.get("model_identity") or existing.get(
                    "spectrum_id"
                ) != input_record.get("spectrum_id"):
                    raise ValueError("Stored prediction idempotency identity mismatch")
                return existing
            files = [path for path in self.directory.glob("*.json") if not path.is_symlink()]
            encoded_size = len(json.dumps(record, ensure_ascii=False, allow_nan=False).encode())
            if len(files) >= self.max_files or (
                sum(path.stat().st_size for path in files) + encoded_size > self.max_bytes
            ):
                raise OSError(errno.ENOSPC, "Configured prediction storage quota exceeded")
            atomic_json(self.directory / f"{prediction_id}.json", record)
        return record

    def get(self, prediction_id: str) -> dict[str, Any]:
        if not SAFE_SPECTRUM_ID.fullmatch(prediction_id):
            raise ValueError("Invalid prediction_id")
        path = self.directory / f"{prediction_id}.json"
        if path.is_symlink() or not path.is_file():
            raise FileNotFoundError("Prediction not found")
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeError) as exc:
            raise ValueError("Stored prediction is damaged") from exc


class MeasuredPredictionStorage:
    def __init__(self, directory: Path, max_files: int = 1000, max_bytes: int = 100_000_000):
        self.directory = directory.resolve()
        self.max_files = max_files
        self.max_bytes = max_bytes
        self.directory.mkdir(parents=True, exist_ok=True)

    def save(
        self,
        result: dict[str, Any],
        profile_id: str,
        input_provenance: dict[str, Any],
        raw_csv_bytes: bytes | None = None,
        *,
        record_id: str | None = None,
    ) -> dict[str, Any]:
        prediction_id = record_id or uuid.uuid4().hex
        if not SAFE_SPECTRUM_ID.fullmatch(prediction_id):
            raise ValueError("Invalid prediction_id")
        record = {
            "prediction_id": prediction_id,
            "profile_id": profile_id,
            "created_at": now_iso(),
            **result,
            "input_provenance": input_provenance,
        }
        with directory_lock(self.directory):
            if record_id and (self.directory / f"{record_id}.json").exists():
                return self.get(record_id)

            inputs_dir = self.directory / "inputs"
            inputs_dir.mkdir(parents=True, exist_ok=True)

            # Count all stored JSON records and all raw input CSVs
            json_files = [
                path
                for path in self.directory.glob("*.json")
                if not path.is_symlink() and path.is_file()
            ]
            csv_files = [
                path
                for path in inputs_dir.glob("*.csv")
                if not path.is_symlink() and path.is_file()
            ]

            encoded_bytes = json.dumps(record, ensure_ascii=False, allow_nan=False).encode("utf-8")
            raw_len = len(raw_csv_bytes) if raw_csv_bytes is not None else 0
            new_bytes = len(encoded_bytes) + raw_len
            current_bytes = sum(path.stat().st_size for path in json_files) + sum(
                path.stat().st_size for path in csv_files
            )

            if len(json_files) >= self.max_files or (current_bytes + new_bytes > self.max_bytes):
                raise OSError(errno.ENOSPC, "Configured measured prediction storage quota exceeded")

            final_json = self.directory / f"{prediction_id}.json"
            final_csv = inputs_dir / f"{prediction_id}.csv" if raw_csv_bytes is not None else None

            tmp_json = self.directory / f".{prediction_id}.{uuid.uuid4().hex}.tmp"
            tmp_csv = (
                inputs_dir / f".{prediction_id}.{uuid.uuid4().hex}.tmp"
                if raw_csv_bytes is not None
                else None
            )

            published_csv = False
            published_json = False
            try:
                # 1. Write and publish raw CSV first if provided
                if raw_csv_bytes is not None and tmp_csv is not None and final_csv is not None:
                    with tmp_csv.open("wb") as f:
                        f.write(raw_csv_bytes)
                        f.flush()
                        os.fsync(f.fileno())
                    os.replace(tmp_csv, final_csv)
                    published_csv = True

                # 2. Write JSON
                with tmp_json.open("wb") as f:
                    f.write(encoded_bytes)
                    f.flush()
                    os.fsync(f.fileno())

                # 3. Publish JSON as final readiness marker
                os.replace(tmp_json, final_json)
                published_json = True
            except BaseException:
                if tmp_csv is not None:
                    tmp_csv.unlink(missing_ok=True)
                tmp_json.unlink(missing_ok=True)
                if published_csv and final_csv is not None:
                    final_csv.unlink(missing_ok=True)
                if published_json:
                    final_json.unlink(missing_ok=True)
                raise
        return record

    def get(self, prediction_id: str) -> dict[str, Any]:
        if not SAFE_SPECTRUM_ID.fullmatch(prediction_id):
            raise ValueError("Invalid prediction_id")
        path = self.directory / f"{prediction_id}.json"
        if path.is_symlink() or not path.is_file():
            raise FileNotFoundError(f"Measured prediction '{prediction_id}' was not found")
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeError) as exc:
            raise ValueError("Stored measured prediction is damaged") from exc

        prov = record.get("input_provenance", {})
        if prov.get("format") == "csv" or prov.get("raw_csv_sha256") or prov.get("input_sha256"):
            csv_path = self.directory / "inputs" / f"{prediction_id}.csv"
            if not csv_path.is_file() or csv_path.is_symlink():
                raise ValueError("Stored measured prediction is damaged: missing raw CSV")
            expected_sha = prov.get("raw_csv_sha256") or prov.get("input_sha256")
            if expected_sha and hashlib.sha256(csv_path.read_bytes()).hexdigest() != expected_sha:
                raise ValueError("Stored measured prediction is damaged: raw CSV checksum mismatch")
        return record

    def get_raw_csv(self, prediction_id: str) -> bytes | None:
        if not SAFE_SPECTRUM_ID.fullmatch(prediction_id):
            return None
        path = self.directory / "inputs" / f"{prediction_id}.csv"
        if path.is_file() and not path.is_symlink():
            return path.read_bytes()
        return None

    def list(self) -> list[dict[str, Any]]:
        records = []
        for path in self.directory.glob("*.json"):
            try:
                records.append(self.get(path.stem))
            except (ValueError, OSError):
                continue
        return sorted(
            records,
            key=lambda row: (row.get("created_at", ""), row.get("prediction_id", "")),
            reverse=True,
        )
