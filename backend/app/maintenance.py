"""Local, offline backup/restore. Stop API and worker before invoking these commands."""

from __future__ import annotations

import argparse
import errno
import hashlib
import json
import logging
import os
import re
import uuid
import zipfile
from contextlib import ExitStack
from pathlib import Path, PurePosixPath
from typing import Any

from app.core.config import Settings
from app.schemas.api import BatchResponse
from app.services.storage import atomic_bytes, atomic_json, directory_lock, now_iso

logger = logging.getLogger("spectralab.maintenance")

MAX_BACKUP_BYTES = 1_500_000_000
CHUNK_SIZE = 64 * 1024

ALLOWED_FILE = re.compile(
    r"^(?:spectra|predictions|measured_predictions|jobs|batches)/[a-f0-9]{32}\.json$|"
    r"^batches/inputs/[a-f0-9]{64}\.csv$|"
    r"^measured_predictions/inputs/[a-f0-9]{32}\.csv$|"
    r"^experiments/[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}/"
    r"(?:runs/[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}/)?result\.json$"
)


def hash_file_chunks(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(CHUNK_SIZE):
            hasher.update(chunk)
    return hasher.hexdigest()


def hash_stream_chunks(stream) -> str:
    hasher = hashlib.sha256()
    while chunk := stream.read(CHUNK_SIZE):
        hasher.update(chunk)
    return hasher.hexdigest()


def locations(settings: Settings) -> dict[str, Path]:
    return {
        **{
            name: settings.resolved_artifacts_dir() / name
            for name in ("batches", "spectra", "predictions", "measured_predictions", "jobs")
        },
        "experiments": settings.resolved_reports_dir() / "experiments",
    }


def save_backup_status_record(settings: Settings, record: dict[str, Any]) -> None:
    path = settings.resolved_artifacts_dir() / ".backup_status.json"
    with directory_lock(settings.resolved_artifacts_dir()):
        try:
            current = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
            if not isinstance(current, dict):
                current = {}
        except Exception:
            current = {}
        if record.get("verification_kind") == "restore_verified":
            current["last_restore_verified"] = record
        else:
            current["last_archive_verified"] = record
        atomic_json(path, current)


def verify_backup_archive(archive_path: Path) -> dict[str, Any]:
    with zipfile.ZipFile(archive_path, "r") as archive:
        names = archive.namelist()
        if len(names) != len(set(names)) or "manifest.json" not in names:
            raise ValueError("Invalid or duplicate backup entries")
        if sum(info.file_size for info in archive.infolist()) > MAX_BACKUP_BYTES:
            raise ValueError("Backup exceeds the restore quota")
        try:
            manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
        except Exception as exc:
            raise ValueError("Backup manifest is corrupted or unreadable") from exc
        if manifest.get("schema_version") != 1 or set(manifest.get("files", {})) != set(names) - {
            "manifest.json"
        }:
            raise ValueError("Backup manifest does not match its files")

        test_result = archive.testzip()
        if test_result is not None:
            raise ValueError(f"Corrupted entry in backup archive: {test_result}")

        for name, checksum in manifest["files"].items():
            if not ALLOWED_FILE.fullmatch(name):
                raise ValueError(f"Backup contains an unsupported entry: {name}")
            with archive.open(name, "r") as stream:
                if hash_stream_chunks(stream) != checksum:
                    raise ValueError(f"Backup checksum mismatch for entry: {name}")
    return manifest


def backup(settings: Settings, destination: Path) -> None:
    destination = destination.resolve()
    if destination.exists():
        raise ValueError("Backup destination already exists")

    roots = locations(settings)
    file_map: dict[str, Path] = {}
    with ExitStack() as stack:
        for root in roots.values():
            stack.enter_context(directory_lock(root))
        for name, root in roots.items():
            if not root.exists():
                continue
            paths = (
                [*root.glob("*/result.json"), *root.glob("*/runs/*/result.json")]
                if name == "experiments"
                else [*root.glob("*.json"), *root.glob("inputs/*.csv")]
                if name in ("batches", "measured_predictions")
                else root.glob("*.json")
            )
            for path in paths:
                relative = f"{name}/{path.relative_to(root).as_posix()}"
                if (
                    path.is_symlink()
                    or any(parent.is_symlink() for parent in path.parents if parent != root)
                    or not ALLOWED_FILE.fullmatch(relative)
                ):
                    continue
                file_map[relative] = path

        manifest = {
            "schema_version": 1,
            "files": {rel: hash_file_chunks(p) for rel, p in file_map.items()},
        }

        # Validate measured predictions completeness and integrity before archive creation
        measured_root = roots.get("measured_predictions")
        if measured_root and measured_root.exists():
            for json_path in measured_root.glob("*.json"):
                if json_path.is_symlink() or not json_path.is_file():
                    continue
                try:
                    rec = json.loads(json_path.read_text(encoding="utf-8"))
                except Exception as exc:
                    raise ValueError(
                        f"Corrupted measured prediction record in backup source: {json_path.name}"
                    ) from exc
                pred_id = rec.get("prediction_id") or json_path.stem
                prov = rec.get("input_provenance", {})
                expected_sha = prov.get("raw_csv_sha256") or prov.get("input_sha256")
                csv_path = measured_root / "inputs" / f"{pred_id}.csv"
                if not csv_path.exists() or csv_path.is_symlink() or not csv_path.is_file():
                    raise ValueError(
                        f"Backup is missing raw CSV for measured prediction: {pred_id}"
                    )
                if expected_sha and hash_file_chunks(csv_path) != expected_sha:
                    raise ValueError(
                        f"Backup raw CSV checksum mismatch for measured prediction: {pred_id}"
                    )

        dest_dir = destination.parent
        dest_dir.mkdir(parents=True, exist_ok=True)
        temp_destination = dest_dir / f".{destination.name}.{uuid.uuid4().hex}.tmp"

        published = False
        try:
            with zipfile.ZipFile(
                temp_destination, "w", compression=zipfile.ZIP_DEFLATED
            ) as archive:
                for rel, p in file_map.items():
                    archive.write(filename=p, arcname=rel)
                archive.writestr("manifest.json", json.dumps(manifest, sort_keys=True, indent=2))

            # Verify the newly created archive in full before publication
            verify_backup_archive(temp_destination)

            # Publish atomically without overwriting destination
            if destination.exists():
                raise ValueError("Backup destination already exists")

            # 1. Atomic publication: os.link refuses atomically if destination exists
            try:
                os.link(temp_destination, destination)
                temp_destination.unlink(missing_ok=True)
                published = True
            except FileExistsError as exc:
                raise ValueError("Backup destination already exists") from exc
            except OSError as exc:
                if exc.errno == errno.EEXIST or destination.exists():
                    raise ValueError("Backup destination already exists") from exc
                # If the filesystem does not support atomic publication without replacement,
                # safely abort backup without creating or touching destination.
                raise OSError(
                    exc.errno,
                    "Atomic backup publication without replacement is not supported on this "
                    f"filesystem: {exc.strerror or exc}",
                ) from exc
        except BaseException:
            temp_destination.unlink(missing_ok=True)
            raise

    # Record verified backup status only after successful publication
    if published:
        record = {
            "completed_at": now_iso(),
            "archive_name": destination.name,
            "archive_sha256": hash_file_chunks(destination),
            "archive_size_bytes": destination.stat().st_size,
            "records_count": len(manifest["files"]),
            "verification_kind": "archive_verified",
            "verification_passed": True,
            "details": f"Archive verified with {len(manifest['files'])} records",
        }
        try:
            save_backup_status_record(settings, record)
        except Exception as exc:
            logger.warning("Backup created at %s, but status record failed: %s", destination, exc)
            print(
                f"Warning: backup archive was created at {destination}, "
                f"but failed to update status record: {exc}"
            )


def restore(settings: Settings, source: Path) -> int:
    source = source.resolve()
    roots = locations(settings)
    records = []

    # Verify and parse archive
    manifest = verify_backup_archive(source)

    with zipfile.ZipFile(source, "r") as archive:
        for name, checksum in manifest["files"].items():
            data = archive.read(name)
            parts = PurePosixPath(name).parts
            if name.startswith("batches/inputs/") or name.startswith(
                "measured_predictions/inputs/"
            ):
                if parts[0] == "batches" and parts[-1].removesuffix(".csv") != checksum:
                    raise ValueError("Backup batch input content identity mismatch")
                records.append((roots[parts[0]].joinpath(*parts[1:]), data))
                continue
            record = json.loads(data.decode("utf-8"))
            record_id = parts[-2] if parts[0] == "experiments" else parts[1].removesuffix(".json")
            key = {
                "spectra": "spectrum_id",
                "predictions": "prediction_id",
                "measured_predictions": "prediction_id",
                "jobs": "job_id",
                "batches": "batch_id",
                "experiments": "run_id",
            }[parts[0]]
            if not isinstance(record, dict) or record.get(key) != record_id:
                raise ValueError("Backup record identity mismatch")
            if parts[0] == "jobs" and record.get("status") == "running":
                record.update(
                    status="failed",
                    error_code="restored_interrupted_job",
                    error_message="Restored running job requires an explicit retry",
                )
            if parts[0] == "batches":
                BatchResponse.model_validate(record)
                if record["status"] == "running":
                    record.update(
                        status="failed",
                        error_code="restored_interrupted_batch",
                        error_message="Restored running batch requires an explicit resume",
                    )
                    for row in record["results"]:
                        if row["status"] == "running":
                            row.update(status="failed", error_code="restored_interrupted_batch")
                    record["progress"].update(
                        completed=sum(
                            row["status"] in {"completed", "failed"} for row in record["results"]
                        ),
                        failed=sum(row["status"] == "failed" for row in record["results"]),
                    )
                for file in record["files"]:
                    if file["status"] == "ready" and (
                        f"batches/inputs/{file['sha256']}.csv" not in manifest["files"]
                    ):
                        raise ValueError("Backup is missing an uploaded batch input")
            if parts[0] == "measured_predictions":
                pred_id = record.get("prediction_id") or parts[1].removesuffix(".json")
                prov = record.get("input_provenance", {})
                expected_sha = prov.get("raw_csv_sha256") or prov.get("input_sha256")
                csv_entry = f"measured_predictions/inputs/{pred_id}.csv"
                if csv_entry not in manifest["files"]:
                    raise ValueError(
                        f"Backup is missing raw CSV for measured prediction: {pred_id}"
                    )
                if expected_sha and manifest["files"][csv_entry] != expected_sha:
                    raise ValueError(
                        f"Backup raw CSV checksum mismatch for measured prediction: {pred_id}"
                    )
            records.append((roots[parts[0]].joinpath(*parts[1:]), record))

    # Validate destination is empty and free of symlinks
    if any(root.is_symlink() for root in roots.values()):
        raise ValueError("Restore destination contains a symlink")
    with ExitStack() as stack:
        for root in roots.values():
            stack.enter_context(directory_lock(root))
        for root in roots.values():
            if any(path.is_file() and path.name != ".storage.lock" for path in root.rglob("*")):
                raise ValueError(
                    "Restore requires empty record directories; use a fresh data location"
                )
        for path, _record in records:
            root = next(root for root in roots.values() if path.is_relative_to(root))
            if any(parent.is_symlink() for parent in path.parents if parent.is_relative_to(root)):
                raise ValueError("Restore destination contains a symlink")
        for path, record in records:
            path.parent.mkdir(parents=True, exist_ok=True)
            if isinstance(record, bytes):
                atomic_bytes(path, record)
            else:
                atomic_json(path, record)

    # Record verified restore
    record = {
        "completed_at": now_iso(),
        "archive_name": source.name,
        "archive_sha256": hash_file_chunks(source),
        "restored_records_count": len(records),
        "verification_kind": "restore_verified",
        "verification_passed": True,
        "details": f"Restored and verified {len(records)} records",
    }
    try:
        save_backup_status_record(settings, record)
    except Exception as exc:
        logger.warning("Restore finished from %s, but status record failed: %s", source, exc)
        print(
            f"Warning: restore succeeded from {source}, but failed to update status record: {exc}"
        )

    return len(records)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["backup", "restore"])
    parser.add_argument("archive", type=Path)
    args = parser.parse_args()
    if args.action == "backup":
        backup(Settings(), args.archive)
        print("Backup created")
    else:
        print(f"Restored {restore(Settings(), args.archive)} records")


if __name__ == "__main__":
    main()
