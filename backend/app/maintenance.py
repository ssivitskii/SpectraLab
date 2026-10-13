"""Local, offline backup/restore. Stop API and worker before invoking these commands."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import zipfile
from contextlib import ExitStack
from pathlib import Path, PurePosixPath

from app.core.config import Settings
from app.schemas.api import BatchResponse
from app.services.storage import atomic_bytes, atomic_json, directory_lock

MAX_BACKUP_BYTES = 1_500_000_000
ALLOWED_FILE = re.compile(
    r"^(?:spectra|predictions|jobs|batches)/[a-f0-9]{32}\.json$|"
    r"^batches/inputs/[a-f0-9]{64}\.csv$|"
    r"^experiments/[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}/"
    r"(?:runs/[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}/)?result\.json$"
)


def locations(settings: Settings) -> dict[str, Path]:
    return {
        **{
            name: settings.resolved_artifacts_dir() / name
            for name in ("batches", "spectra", "predictions", "jobs")
        },
        "experiments": settings.resolved_reports_dir() / "experiments",
    }


def backup(settings: Settings, destination: Path) -> None:
    roots = locations(settings)
    if destination.exists():
        raise ValueError("Backup destination already exists")
    files = {}
    with ExitStack() as stack:
        for root in roots.values():
            stack.enter_context(directory_lock(root))
        for name, root in roots.items():
            paths = (
                [*root.glob("*/result.json"), *root.glob("*/runs/*/result.json")]
                if name == "experiments"
                else [*root.glob("*.json"), *root.glob("inputs/*.csv")]
                if name == "batches"
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
                files[relative] = path.read_bytes()
        manifest = {
            "schema_version": 1,
            "files": {name: hashlib.sha256(data).hexdigest() for name, data in files.items()},
        }
        with zipfile.ZipFile(destination, "x", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, data in files.items():
                archive.writestr(name, data)
            archive.writestr("manifest.json", json.dumps(manifest, sort_keys=True))


def restore(settings: Settings, source: Path) -> int:
    roots = locations(settings)
    records = []
    with zipfile.ZipFile(source) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)) or "manifest.json" not in names:
            raise ValueError("Invalid or duplicate backup entries")
        if sum(info.file_size for info in archive.infolist()) > MAX_BACKUP_BYTES:
            raise ValueError("Backup exceeds the restore quota")
        manifest = json.loads(archive.read("manifest.json"))
        if manifest.get("schema_version") != 1 or set(manifest["files"]) != set(names) - {
            "manifest.json"
        }:
            raise ValueError("Backup manifest does not match its files")
        for name, checksum in manifest["files"].items():
            if not ALLOWED_FILE.fullmatch(name):
                raise ValueError("Backup contains an unsupported entry")
            data = archive.read(name)
            if hashlib.sha256(data).hexdigest() != checksum:
                raise ValueError("Backup checksum mismatch")
            parts = PurePosixPath(name).parts
            if name.startswith("batches/inputs/"):
                if parts[-1].removesuffix(".csv") != checksum:
                    raise ValueError("Backup batch input content identity mismatch")
                records.append((roots["batches"].joinpath(*parts[1:]), data))
                continue
            record = json.loads(data)
            record_id = parts[-2] if parts[0] == "experiments" else parts[1].removesuffix(".json")
            key = {
                "spectra": "spectrum_id",
                "predictions": "prediction_id",
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
            records.append((roots[parts[0]].joinpath(*parts[1:]), record))
    # Validate the complete archive before any record is written. Restores never overwrite data.
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
