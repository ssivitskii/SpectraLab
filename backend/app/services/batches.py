"""Durable local CSV batches. HTTP names are labels, never filesystem locations."""

from __future__ import annotations

import csv
import errno
import hashlib
import importlib
import io
import json
import re
import uuid
from dataclasses import asdict
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any

import numpy as np
from spectralab_ml.preprocessing import validate_spectrum_arrays

from app.services.jobs import IdempotencyConflict
from app.services.storage import (
    SAFE_SPECTRUM_ID,
    atomic_bytes,
    atomic_json,
    directory_lock,
    now_iso,
)

if TYPE_CHECKING:
    from app.core.config import Settings
    from app.services.container import Services

BATCH_TERMINAL = {"completed", "partial", "failed", "cancelled"}


def digest_json(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


def safe_relative_name(name: str) -> str:
    name = name.replace("\\", "/")
    parts = PurePosixPath(name)
    if (
        not name
        or len(name) > 1024
        or name.startswith("/")
        or re.match(r"^[a-zA-Z]:", name)
        or ".." in parts.parts
        or any(ord(char) < 32 for char in name)
        or not name.lower().endswith(".csv")
    ):
        raise ValueError("Expected a relative CSV filename without parent traversal")
    return parts.as_posix()


def model_snapshot(services: Services, model_id: str) -> dict[str, Any]:
    registry = services.registry
    estimator, processor, metadata = registry._resolve(model_id)
    source_hash = digest_json(
        {
            "metadata": {
                key: value
                for key, value in services.catalog.metadata.items()
                if key != "imported_at"
            },
            "lines": [asdict(line) for line in services.catalog.lines],
        }
    )
    manifest_hash = model_hash = None
    if model_id != "builtin-nnls":
        _, _, manifest = registry._load(model_id)
        manifest_hash = digest_json(asdict(manifest))
        model_hash = manifest.model_sha256
    # Development code changes must invalidate the cache even when the package version is unchanged.
    code_hashes = {}
    for module_name in (
        "spectralab_ml.inference.predictor",
        "spectralab_ml.preprocessing.pipeline",
        "spectralab_ml.models.estimators",
        "spectralab_ml.models.calibration",
        "spectralab_ml.models.cnn",
        "spectralab_ml.training",
        "spectralab_ml.simulation.generator",
    ):
        module = importlib.import_module(module_name)
        code_hashes[module_name] = hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest()
    snapshot = {
        "model_id": model_id,
        "model_version": metadata["version"],
        "model_sha256": model_hash,
        "manifest_sha256": manifest_hash,
        "reference_source": services.catalog.source_name,
        "reference_sha256": source_hash,
        "preprocessing": processor.to_dict(),
        "class_order": list(registry.class_order),
        "ion_stage": registry.ion_stage,
        "score_kind": estimator.score_kind,
        "calibrated": estimator.calibrated,
    }
    runtime_versions = {}
    for package in ("numpy", "scipy", "scikit-learn", "torch"):
        try:
            runtime_versions[package] = version(package)
        except PackageNotFoundError:
            runtime_versions[package] = None
    snapshot["identity"] = digest_json(
        {
            **snapshot,
            "thresholds": metadata["thresholds"],
            "inference_code": code_hashes,
            "runtime_versions": runtime_versions,
        }
    )
    return snapshot


def parse_csv(raw: bytes, services: Services) -> tuple[list[float], list[float]]:
    if len(raw) > services.settings.max_upload_bytes:
        raise ValueError("CSV file exceeds the upload size limit")
    try:
        reader = csv.DictReader(io.StringIO(raw.decode("utf-8-sig")))
        if sorted(reader.fieldnames or []) != ["intensity", "wavelength_nm"]:
            raise ValueError("CSV columns must be exactly wavelength_nm,intensity")
        wavelengths, intensities = [], []
        for index, row in enumerate(reader):
            if index >= services.settings.max_spectrum_points:
                raise ValueError("CSV contains too many points")
            if None in row or any(value is None for value in row.values()):
                raise ValueError("Unexpected CSV field count")
            wavelengths.append(float(row["wavelength_nm"]))
            intensities.append(float(row["intensity"]))
        validate_spectrum_arrays(
            np.asarray(wavelengths),
            np.asarray(intensities),
            max_points=services.settings.max_spectrum_points,
        )
        services.registry.preprocessor.transform(np.asarray(wavelengths), np.asarray(intensities))
        return wavelengths, intensities
    except (UnicodeError, TypeError, KeyError, csv.Error) as exc:
        raise ValueError("CSV must contain UTF-8 text with finite numeric measurements") from exc


class BatchStorage:
    def __init__(self, directory: Path, settings: Settings):
        self.directory = directory.resolve()
        self.settings = settings
        self.directory.mkdir(parents=True, exist_ok=True)

    def get(self, batch_id: str) -> dict[str, Any]:
        if not SAFE_SPECTRUM_ID.fullmatch(batch_id):
            raise ValueError("Invalid batch_id")
        path = self.directory / f"{batch_id}.json"
        if path.is_symlink() or not path.is_file():
            raise FileNotFoundError("Batch not found")
        try:
            record = json.loads(path.read_text())
            if not isinstance(record, dict) or record.get("batch_id") != batch_id:
                raise ValueError("Invalid batch record")
            return record
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError("Stored batch is damaged") from exc

    def list(self) -> list[dict[str, Any]]:
        records = []
        for path in self.directory.glob("*.json"):
            try:
                records.append(self.get(path.stem))
            except (OSError, ValueError):
                continue
        return sorted(records, key=lambda row: (row["created_at"], row["batch_id"]), reverse=True)

    def _write(self, record: dict[str, Any]) -> dict[str, Any]:
        record["updated_at"] = now_iso()
        record["progress"] = {
            "completed": sum(row["status"] in {"completed", "failed"} for row in record["results"]),
            "failed": sum(row["status"] == "failed" for row in record["results"]),
            "total": len(record["results"]),
        }
        atomic_json(self.directory / f"{record['batch_id']}.json", record)
        return record

    def create(
        self, request: dict[str, Any], snapshots: dict[str, dict[str, Any]]
    ) -> dict[str, Any]:
        descriptors = [
            dict(item, relative_path=safe_relative_name(item["relative_path"]))
            for item in request["expected_files"]
        ]
        if len(descriptors) > self.settings.max_batch_files:
            raise ValueError("Batch exceeds the configured file count limit")
        if len({item["relative_path"] for item in descriptors}) != len(descriptors):
            raise ValueError("Relative CSV paths must be unique")
        normalized = {**request, "expected_files": descriptors}
        request_hash = digest_json(normalized)
        with directory_lock(self.directory):
            records = self.list()
            for record in records:
                if record["idempotency_key"] == request["idempotency_key"]:
                    if record["request_hash"] != request_hash:
                        raise IdempotencyConflict(
                            "Batch request key already belongs to another request"
                        )
                    return record
            if len(records) >= self.settings.max_batch_records:
                raise OSError(errno.ENOSPC, "Batch record quota exceeded")
            timestamp = now_iso()
            files = [
                {
                    **item,
                    "file_id": uuid.uuid4().hex,
                    "status": "pending_upload",
                    "validation_origin": None,
                    "point_count": None,
                    "spectrum_id": None,
                    "error_code": None,
                    "error_message": None,
                    "measurement_metadata": request.get("measurement_metadata", {}),
                }
                for item in descriptors
            ]
            record = {
                "schema_version": 1,
                "batch_id": uuid.uuid4().hex,
                "name": request["name"],
                "status": "draft",
                "created_at": timestamp,
                "updated_at": timestamp,
                "model_ids": request["model_ids"],
                "model_snapshots": snapshots,
                "model_identities": {key: value["identity"] for key, value in snapshots.items()},
                "files": files,
                "results": [
                    {
                        "file_id": file["file_id"],
                        "model_id": model_id,
                        "model_identity": snapshots[model_id]["identity"],
                        "status": "pending",
                        "prediction_id": None,
                        "prediction": None,
                        "cache_hit": False,
                        "error_code": None,
                        "error_message": None,
                    }
                    for file in files
                    for model_id in request["model_ids"]
                ],
                "cancellation_requested": False,
                "error_code": None,
                "error_message": None,
                "idempotency_key": request["idempotency_key"],
                "request_hash": request_hash,
            }
            return self._write(record)

    def _file(self, record: dict, name: str) -> dict:
        name = safe_relative_name(name)
        for file in record["files"]:
            if file["relative_path"] == name:
                return file
        raise ValueError("File was not declared in this batch manifest")

    def reject(self, batch_id: str, rejection: dict[str, Any]) -> dict[str, Any]:
        with directory_lock(self.directory):
            record = self.get(batch_id)
            file = self._file(record, rejection["relative_path"])
            if (
                file["sha256"] != rejection["sha256"]
                or file["size_bytes"] != rejection["size_bytes"]
            ):
                raise IdempotencyConflict(
                    "Rejected file descriptor differs from the batch manifest"
                )
            if file["status"] == "invalid":
                return file
            if record["status"] != "draft" or file["status"] != "pending_upload":
                raise IdempotencyConflict(
                    "Batch intake is closed or this file was already uploaded"
                )
            file.update(
                status="invalid",
                validation_origin="client",
                error_code=rejection["error_code"],
                error_message=rejection["error_message"],
            )
            self._write(record)
            return file

    def upload(
        self, batch_id: str, relative_path: str, raw: bytes, services: Services
    ) -> dict[str, Any]:
        checksum = hashlib.sha256(raw).hexdigest()
        with directory_lock(self.directory):
            record = self.get(batch_id)
            file = self._file(record, relative_path)
            if file["sha256"] != checksum or file["size_bytes"] != len(raw):
                raise IdempotencyConflict("CSV bytes differ from the declared checksum or size")
            if file["status"] != "pending_upload":
                return file
            if record["status"] != "draft":
                raise IdempotencyConflict("Batch intake is closed")
            try:
                wavelengths, intensities = parse_csv(raw, services)
            except ValueError as exc:
                file.update(
                    status="invalid",
                    validation_origin="server",
                    error_code="invalid_csv",
                    error_message=str(exc),
                )
                self._write(record)
                return file
            uploaded_bytes = sum(
                item["size_bytes"] for item in record["files"] if item["status"] == "ready"
            )
            if uploaded_bytes + len(raw) > self.settings.max_batch_upload_bytes:
                raise OSError(errno.ENOSPC, "Batch upload quota exceeded")
            inputs = self.directory / "inputs"
            inputs.mkdir(exist_ok=True)
            destination = inputs / f"{checksum}.csv"
            if destination.is_symlink():
                raise ValueError("Untrusted batch input")
            if not destination.exists():
                size = sum(
                    path.stat().st_size for path in inputs.glob("*.csv") if not path.is_symlink()
                )
                if size + len(raw) > self.settings.max_batch_input_storage_bytes:
                    raise OSError(errno.ENOSPC, "Batch input storage quota exceeded")
                atomic_bytes(destination, raw)
            elif hashlib.sha256(destination.read_bytes()).hexdigest() != checksum:
                raise ValueError("Stored batch input checksum mismatch")
            spectrum = services.spectra.save(
                {
                    "wavelength_nm": wavelengths,
                    "intensity": intensities,
                    "clean_signal": None,
                    "true_labels": None,
                    "base_sample_id": None,
                    "origin": "uploaded",
                    "reference_source": "user_upload",
                    "reference_metadata": services.catalog.metadata,
                    "measurement_metadata": file["measurement_metadata"],
                    "parameters": {
                        "units": "nm",
                        "wavelength_medium": "vacuum",
                        "snr_db": None,
                        "snr_kind": "unknown",
                        "original_filename": file["relative_path"],
                        "original_sha256": checksum,
                        "batch_id": batch_id,
                    },
                },
                record_id=digest_json({"batch_id": batch_id, "file_id": file["file_id"]})[:32],
            )
            file.update(
                status="ready",
                validation_origin="server",
                point_count=len(wavelengths),
                spectrum_id=spectrum["spectrum_id"],
            )
            self._write(record)
            return file

    def start(self, batch_id: str, *, resume: bool = False) -> dict[str, Any]:
        with directory_lock(self.directory):
            record = self.get(batch_id)
            if record["status"] in {"queued", "running", "completed"}:
                return record
            missing = any(file["status"] == "pending_upload" for file in record["files"])
            if missing:
                if resume and record["status"] == "cancelled":
                    record.update(status="draft", cancellation_requested=False)
                    return self._write(record)
                raise IdempotencyConflict(
                    "Batch has missing uploads; reselect the source folder first"
                )
            if record["status"] != "draft" and not resume:
                raise IdempotencyConflict("Use resume to retry an existing batch")
            if (
                sum(item["status"] in {"queued", "running"} for item in self.list())
                >= self.settings.max_pending_jobs
            ):
                raise OSError(errno.ENOSPC, "Batch queue quota exceeded")
            for row in record["results"]:
                if row["status"] != "completed":
                    row.update(status="pending", error_code=None, error_message=None)
            record.update(
                status="queued", cancellation_requested=False, error_code=None, error_message=None
            )
            return self._write(record)

    def cancel(self, batch_id: str) -> dict[str, Any]:
        with directory_lock(self.directory):
            record = self.get(batch_id)
            if record["status"] not in BATCH_TERMINAL:
                record["cancellation_requested"] = True
                if record["status"] != "running":
                    record["status"] = "cancelled"
                return self._write(record)
            return record

    def claim(self, batch_id: str | None = None) -> dict[str, Any] | None:
        with directory_lock(self.directory):
            queued = [
                item
                for item in self.list()
                if item["status"] == "queued" and (batch_id is None or item["batch_id"] == batch_id)
            ]
            if not queued:
                return None
            record = queued[-1]
            record["status"] = "running"
            return self._write(record)

    def result(self, batch_id: str, file_id: str, model_id: str, **updates: Any) -> dict:
        with directory_lock(self.directory):
            record = self.get(batch_id)
            for row in record["results"]:
                if row["file_id"] == file_id and row["model_id"] == model_id:
                    row.update(updates)
                    return self._write(record)
            raise ValueError("Unknown batch result cell")

    def finish(
        self, batch_id: str, status: str | None = None, error_code: str | None = None
    ) -> dict:
        with directory_lock(self.directory):
            record = self.get(batch_id)
            if status is None:
                failed = sum(row["status"] == "failed" for row in record["results"])
                status = (
                    "cancelled"
                    if record["cancellation_requested"]
                    else (
                        "completed"
                        if not failed
                        else ("failed" if failed == len(record["results"]) else "partial")
                    )
                )
            record.update(status=status, error_code=error_code)
            for row in record["results"]:
                if row["status"] == "running":
                    row.update(
                        status="pending" if status == "cancelled" else "failed",
                        error_code=error_code,
                        error_message="Processing interrupted before completion",
                    )
            return self._write(record)

    def recover_interrupted(self) -> None:
        for record in self.list():
            if record["status"] == "running":
                self.finish(
                    record["batch_id"],
                    "cancelled" if record["cancellation_requested"] else "failed",
                    "worker_interrupted",
                )

    def input_bytes(self, file: dict) -> bytes:
        checksum = file["sha256"]
        if not checksum or not re.fullmatch(r"[a-f0-9]{64}", checksum):
            raise ValueError("Batch file has no uploaded content checksum")
        path = self.directory / "inputs" / f"{checksum}.csv"
        if path.is_symlink() or not path.is_file():
            raise ValueError("Batch input is missing or untrusted")
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != checksum:
            raise ValueError("Stored batch input checksum mismatch")
        return raw

    def cache_get(self, key: str) -> dict | None:
        path = self.directory / "cache" / f"{key}.json"
        if path.is_symlink() or not path.is_file():
            return None
        try:
            payload = json.loads(path.read_text())
            if (
                payload["key"] != key
                or digest_json(payload["prediction"]) != payload["prediction_sha256"]
            ):
                return None
            return payload["prediction"]
        except (KeyError, ValueError, OSError):
            return None

    def cache_save(self, key: str, prediction: dict) -> None:
        directory = self.directory / "cache"
        with directory_lock(directory):
            payload = {
                "key": key,
                "prediction": prediction,
                "prediction_sha256": digest_json(prediction),
            }
            destination = directory / f"{key}.json"
            existing_size = destination.stat().st_size if destination.is_file() else 0
            current_size = sum(
                path.stat().st_size for path in directory.glob("*.json") if not path.is_symlink()
            )
            if (
                current_size - existing_size + len(json.dumps(payload).encode())
                > self.settings.max_batch_cache_bytes
            ):
                return  # Cache is optional; a full cache must not discard a valid analysis.
            atomic_json(destination, payload)


def process_batch(services: Services, batch_id: str) -> dict[str, Any]:
    batches = services.batches
    record = batches.get(batch_id)
    files = {file["file_id"]: file for file in record["files"]}
    for row in record["results"]:
        if row["status"] == "completed":
            continue
        if batches.get(batch_id)["cancellation_requested"]:
            return batches.finish(batch_id, "cancelled")
        file = files[row["file_id"]]
        batches.result(batch_id, file["file_id"], row["model_id"], status="running")
        try:
            if file["status"] != "ready":
                raise ValueError(file["error_message"] or "Input was not uploaded")
            raw = batches.input_bytes(file)
            current = model_snapshot(services, row["model_id"])
            if current["identity"] != row["model_identity"]:
                raise ValueError("Pinned model or reference identity changed; create a new batch")
            cache_key = digest_json(
                {"input_sha256": file["sha256"], "model_identity": row["model_identity"]}
            )
            prediction = batches.cache_get(cache_key)
            hit = prediction is not None
            # Use the original bytes; editing history metadata must not alter batch inference.
            wavelengths, intensities = parse_csv(raw, services)
            spectrum = {
                "spectrum_id": file["spectrum_id"],
                "schema_version": 1,
                "origin": "uploaded",
                "reference_source": "user_upload",
                "wavelength_nm": wavelengths,
                "intensity": intensities,
                "measurement_metadata": file["measurement_metadata"],
                "parameters": {
                    "units": "nm",
                    "wavelength_medium": "vacuum",
                    "snr_db": None,
                    "snr_kind": "unknown",
                    "original_filename": file["relative_path"],
                    "original_sha256": file["sha256"],
                    "batch_id": batch_id,
                },
            }
            if prediction is None:
                prediction = services.registry.predict(
                    row["model_id"], np.asarray(wavelengths), np.asarray(intensities)
                ).to_dict()
                batches.cache_save(cache_key, prediction)
            stored = services.predictions.save(
                {
                    **prediction,
                    "reference_metadata": services.catalog.metadata,
                    "model_identity": row["model_identity"],
                    "batch_id": batch_id,
                    "input_sha256": file["sha256"],
                    "file_id": file["file_id"],
                },
                spectrum,
                record_id=digest_json(
                    {
                        "batch_id": batch_id,
                        "file_id": file["file_id"],
                        "model_identity": row["model_identity"],
                    }
                )[:32],
            )
            public_prediction = {key: value for key, value in stored.items() if key != "input"}
            batches.result(
                batch_id,
                file["file_id"],
                row["model_id"],
                status="completed",
                prediction_id=stored["prediction_id"],
                prediction=public_prediction,
                cache_hit=hit,
                error_code=None,
                error_message=None,
            )
        except (ValueError, OSError, ImportError) as exc:
            from app.services.exports import public_record

            code = (
                file["error_code"]
                if file["status"] == "invalid"
                else "storage_quota"
                if isinstance(exc, OSError) and exc.errno == errno.ENOSPC
                else "analysis_failed"
            )
            batches.result(
                batch_id,
                file["file_id"],
                row["model_id"],
                status="failed",
                error_code=code,
                error_message=public_record(str(exc)),
            )
    return batches.finish(batch_id)
