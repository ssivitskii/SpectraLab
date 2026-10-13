from __future__ import annotations

import errno
import hashlib
import json
import uuid
from pathlib import Path
from typing import Any

from app.services.storage import SAFE_SPECTRUM_ID, atomic_json, directory_lock, now_iso

TERMINAL = {"completed", "failed", "cancelled"}


class IdempotencyConflict(ValueError):
    pass


class JobStorage:
    def __init__(self, directory: Path, max_pending: int = 10, max_records: int = 1000):
        self.directory = directory.resolve()
        self.max_pending = max_pending
        self.max_records = max_records
        self.directory.mkdir(parents=True, exist_ok=True)

    def get(self, job_id: str) -> dict[str, Any]:
        if not SAFE_SPECTRUM_ID.fullmatch(job_id):
            raise ValueError("Invalid job_id")
        path = self.directory / f"{job_id}.json"
        if path.is_symlink() or not path.is_file():
            raise FileNotFoundError("Job not found")
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(record, dict) or record.get("job_id") != job_id:
                raise ValueError("Invalid stored job")
            return record
        except (json.JSONDecodeError, UnicodeError) as exc:
            raise ValueError("Stored job is damaged") from exc

    def list(self) -> list[dict[str, Any]]:
        records = []
        for path in self.directory.glob("*.json"):
            try:
                records.append(self.get(path.stem))
            except (ValueError, OSError):
                continue
        return sorted(records, key=lambda item: (item["created_at"], item["job_id"]), reverse=True)

    def create(self, request: dict[str, Any]) -> dict[str, Any]:
        digest = hashlib.sha256(json.dumps(request, sort_keys=True).encode()).hexdigest()
        with directory_lock(self.directory):
            records = self.list()
            for record in records:
                if record["idempotency_key"] == request["idempotency_key"]:
                    if record["request_hash"] != digest:
                        raise IdempotencyConflict(
                            "Idempotency key was already used for another request"
                        )
                    return record
            if (
                len(records) >= self.max_records
                or sum(record["status"] not in TERMINAL for record in records) >= self.max_pending
            ):
                raise OSError(errno.ENOSPC, "Configured job queue quota exceeded")
            timestamp = now_iso()
            record = {
                **request,
                "request_hash": digest,
                "job_id": uuid.uuid4().hex,
                "created_at": timestamp,
                "updated_at": timestamp,
                "status": "queued",
                "progress": {"completed": 0, "total": len(request["seeds"])},
                "run_id": None,
                "error_code": None,
                "error_message": None,
                "cancellation_requested": False,
            }
            atomic_json(self.directory / f"{record['job_id']}.json", record)
            return record

    def update(self, job_id: str, **updates: Any) -> dict[str, Any]:
        with directory_lock(self.directory):
            record = self.get(job_id)
            record.update(updates)
            record["updated_at"] = now_iso()
            atomic_json(self.directory / f"{job_id}.json", record)
            return record

    def cancel(self, job_id: str) -> dict[str, Any]:
        with directory_lock(self.directory):
            record = self.get(job_id)
            if record["status"] not in TERMINAL:
                record["cancellation_requested"] = True
                if record["status"] == "queued":
                    record["status"] = "cancelled"
                record["updated_at"] = now_iso()
                atomic_json(self.directory / f"{job_id}.json", record)
            return record

    def claim_next(self) -> dict[str, Any] | None:
        with directory_lock(self.directory):
            queued = [item for item in self.list() if item["status"] == "queued"]
            if not queued:
                return None
            record = queued[-1]
            record["status"] = "running"
            record["updated_at"] = now_iso()
            atomic_json(self.directory / f"{record['job_id']}.json", record)
            return record

    def recover_interrupted(self) -> list[dict[str, Any]]:
        recovered = []
        for record in self.list():
            if record["status"] == "running":
                recovered.append(
                    self.update(
                        record["job_id"],
                        status="cancelled" if record["cancellation_requested"] else "failed",
                        error_code="worker_interrupted",
                        error_message="Worker restarted before completion; retry with a new key",
                    )
                )
        return recovered
