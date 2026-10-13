"""Analyze a local CSV folder, or resume its durable manifest. No server directory API exists."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import time
from pathlib import Path

from app.core.config import Settings
from app.schemas.api import BatchRequest
from app.services.batch_exports import export_batch
from app.services.batches import BATCH_TERMINAL, model_snapshot, process_batch
from app.services.container import build_services


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "directory", nargs="?", type=Path, help="Local CSV folder; recursive by default"
    )
    parser.add_argument("--models", nargs="+", default=["builtin-nnls"])
    parser.add_argument("--name", default="Local CSV folder")
    parser.add_argument("--resume", metavar="BATCH_ID")
    parser.add_argument(
        "--enqueue", action="store_true", help="Queue only; a separate worker produces the results"
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Report .zip, .json, or .csv; existing files are not overwritten",
    )
    parser.add_argument("--execute", help=argparse.SUPPRESS)
    args = parser.parse_args()
    settings = Settings()
    if args.execute:
        from app.worker import bound_resources, install_parent_watchdog

        install_parent_watchdog()
        bound_resources(settings)
        services = build_services(settings)
        process_batch(services, args.execute)
        return
    services = build_services(settings)
    batch = services.batches.get(args.resume) if args.resume else None
    files: dict[str, tuple[bytes | None, dict | None]] = {}
    observed: dict[str, dict] = {}
    if args.directory is not None:
        directory = args.directory.expanduser().resolve()
        if not directory.is_dir():
            parser.error("directory must be an existing local folder")
        descriptors = []
        for path in sorted(directory.rglob("*")):
            if path.suffix.lower() != ".csv" or (not path.is_file() and not path.is_symlink()):
                continue
            name = path.relative_to(directory).as_posix()
            raw, rejection = None, None
            size = 0
            try:
                if path.is_symlink():
                    raise ValueError("Symlink CSV inputs are not followed")
                size = path.stat().st_size
                if size > settings.max_upload_bytes:
                    raise ValueError("CSV exceeds the configured upload size limit")
                raw = path.read_bytes()
            except (OSError, ValueError) as exc:
                rejection = {"error_code": "local_input_rejected", "error_message": str(exc)}
            descriptor = {
                "relative_path": name,
                "sha256": hashlib.sha256(raw).hexdigest() if raw is not None else None,
                "size_bytes": size,
            }
            descriptors.append(descriptor)
            files[name] = (raw, rejection)
            observed[name] = descriptor
            if len(descriptors) > settings.max_batch_files:
                parser.error("Folder exceeds the configured file count limit")
        if not descriptors:
            parser.error("No CSV files found in the selected folder")
        if batch is None:
            # Repeating this command for unchanged files/models resumes the same logical batch.
            snapshots = {name: model_snapshot(services, name) for name in args.models}
            key_data = {
                "name": args.name,
                "files": descriptors,
                "models": {name: value["identity"] for name, value in snapshots.items()},
            }
            request = BatchRequest(
                name=args.name,
                model_ids=args.models,
                expected_files=descriptors,
                idempotency_key="cli-"
                + hashlib.sha256(json.dumps(key_data, sort_keys=True).encode()).hexdigest(),
            )
            batch = services.batches.create(request.model_dump(), snapshots)
        if batch["status"] == "cancelled" and any(
            file["status"] == "pending_upload" for file in batch["files"]
        ):
            batch = services.batches.start(batch["batch_id"], resume=True)
        for file in batch["files"]:
            if file["status"] != "pending_upload":
                continue
            if file["relative_path"] not in files:
                parser.error("Reselected folder is missing a file declared by this batch")
            selected = observed[file["relative_path"]]
            if any(selected[key] != file[key] for key in ("sha256", "size_bytes")):
                parser.error("Reselected file differs from the saved batch checksum or size")
            raw, rejection = files[file["relative_path"]]
            if rejection is not None:
                services.batches.reject(
                    batch["batch_id"],
                    {
                        **{key: file[key] for key in ("relative_path", "sha256", "size_bytes")},
                        **rejection,
                    },
                )
            else:
                services.batches.upload(batch["batch_id"], file["relative_path"], raw, services)
    elif batch is None:
        parser.error("Provide a local CSV folder or --resume BATCH_ID")
    batch = services.batches.start(batch["batch_id"], resume=batch["status"] in BATCH_TERMINAL)
    print(json.dumps({"batch_id": batch["batch_id"], "status": batch["status"]}), flush=True)
    if args.enqueue:
        return
    deadline = time.monotonic() + settings.job_timeout_seconds + 60
    lock_path = services.jobs.directory / ".worker.lock"
    with lock_path.open("a+b") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            owns_worker = False
        else:
            owns_worker = True
        if owns_worker:
            from app.worker import run_batch

            if services.batches.get(batch["batch_id"])["status"] == "running":
                # The lock proves no supervisor is alive. Let its old child's watchdog exit.
                time.sleep(1)
                services.batches.recover_interrupted()
                services.batches.start(batch["batch_id"], resume=True)
            claimed = services.batches.claim(batch["batch_id"])
            if claimed:
                run_batch(settings, services.batches, claimed)
        while services.batches.get(batch["batch_id"])["status"] not in BATCH_TERMINAL:
            if time.monotonic() > deadline:
                raise SystemExit("Batch is still queued/running; resume with its saved batch ID")
            time.sleep(0.25)
    batch = services.batches.get(batch["batch_id"])
    if args.output:
        format = args.output.suffix.removeprefix(".").lower()
        if format not in {"zip", "json", "csv"}:
            parser.error("Report extension must be .zip, .json or .csv")
        response = export_batch(services.batches, batch["batch_id"], format)
        with args.output.open("xb") as output:
            output.write(response.body)
    print(
        json.dumps(
            {
                "batch_id": batch["batch_id"],
                "status": batch["status"],
                "progress": batch["progress"],
            }
        ),
        flush=True,
    )
    if batch["status"] != "completed":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
