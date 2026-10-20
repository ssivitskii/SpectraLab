from __future__ import annotations

import fcntl
import json
import logging
import os
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from app.core.config import Settings
from app.schemas.api import (
    BackupVerificationRecord,
    RestoreVerificationRecord,
    WorkerCurrentTask,
)

if TYPE_CHECKING:
    from app.services.batches import BatchStorage
    from app.services.container import Services
    from app.services.jobs import JobStorage

logger = logging.getLogger("spectralab.system_status")


def _worker_resp(
    state: str,
    base_response: dict[str, Any],
    worker_id: str | None,
    pid: int | None,
    updated_at: str | None,
    current_task: dict[str, Any] | None,
    message: str,
) -> dict[str, Any]:
    return {
        **base_response,
        "state": state,
        "status": state,
        "worker_id": worker_id,
        "pid": pid,
        "updated_at": updated_at,
        "current_task": current_task,
        "message": message,
    }


def get_worker_status(settings: Settings) -> dict[str, Any]:
    jobs_dir = settings.resolved_artifacts_dir() / "jobs"
    heartbeat_file = jobs_dir / ".worker_heartbeat.json"
    lock_file = jobs_dir / ".worker.lock"

    interval = settings.worker_heartbeat_interval_seconds
    stale_threshold = settings.worker_heartbeat_stale_seconds

    base_response = {
        "heartbeat_interval_seconds": interval,
        "stale_threshold_seconds": stale_threshold,
    }

    if not heartbeat_file.exists() or heartbeat_file.is_symlink():
        return _worker_resp(
            "absent",
            base_response,
            None,
            None,
            None,
            None,
            "Worker has not recorded any heartbeat",
        )

    try:
        data = json.loads(heartbeat_file.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("Invalid heartbeat JSON structure")
    except Exception as exc:
        return _worker_resp(
            "stale",
            base_response,
            None,
            None,
            None,
            None,
            f"Worker heartbeat file is unreadable: {type(exc).__name__}",
        )

    worker_id = data.get("worker_id") if isinstance(data.get("worker_id"), str) else None
    pid = (
        data.get("pid")
        if isinstance(data.get("pid"), int) and not isinstance(data.get("pid"), bool)
        else None
    )
    updated_at_str = data.get("updated_at") if isinstance(data.get("updated_at"), str) else None
    recorded_status = data.get("status") if isinstance(data.get("status"), str) else None

    current_task = None
    if isinstance(data.get("current_task"), dict):
        try:
            current_task = WorkerCurrentTask.model_validate(data["current_task"]).model_dump()
        except Exception:
            current_task = None

    if recorded_status == "stopped":
        return _worker_resp(
            "stopped",
            base_response,
            worker_id,
            pid,
            updated_at_str,
            None,
            "Worker was stopped cleanly",
        )

    is_stale = True
    if updated_at_str:
        try:
            updated_dt = datetime.fromisoformat(updated_at_str)
            age = (datetime.now(UTC) - updated_dt).total_seconds()
            if age <= stale_threshold:
                is_stale = False
        except Exception:
            is_stale = True

    if is_stale:
        return _worker_resp(
            "stale",
            base_response,
            worker_id,
            pid,
            updated_at_str,
            current_task,
            f"Worker heartbeat is outdated (last update: {updated_at_str})",
        )

    # Verify if lock is held by a live worker process
    # If we can lock the file non-blockingly, then NO worker process holds it
    lock_held = False
    if lock_file.exists():
        try:
            with lock_file.open("a+b") as lf:
                try:
                    fcntl.flock(lf, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    fcntl.flock(lf, fcntl.LOCK_UN)
                    lock_held = False
                except BlockingIOError:
                    lock_held = True
        except Exception:
            pass

    if not lock_held:
        return _worker_resp(
            "stale",
            base_response,
            worker_id,
            pid,
            updated_at_str,
            current_task,
            "Worker process is no longer active",
        )

    # Optional PID check on POSIX
    if isinstance(pid, int):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return _worker_resp(
                "stale",
                base_response,
                worker_id,
                pid,
                updated_at_str,
                current_task,
                f"Worker PID {pid} is no longer running",
            )
        except PermissionError:
            pass

    return {
        **base_response,
        "state": "running",
        "worker_id": worker_id,
        "pid": pid,
        "updated_at": updated_at_str,
        "current_task": current_task,
        "message": "Worker is active and healthy",
    }


def get_queue_status(
    jobs_storage: JobStorage, batch_storage: BatchStorage, settings: Settings
) -> dict[str, Any]:
    job_records = jobs_storage.list()
    job_counts = {"queued": 0, "running": 0, "completed": 0, "failed": 0, "cancelled": 0}
    for item in job_records:
        status = item.get("status")
        if status in job_counts:
            job_counts[status] += 1

    pending_jobs = job_counts["queued"] + job_counts["running"]
    remaining_pending = max(0, settings.max_pending_jobs - pending_jobs)

    batch_records = batch_storage.list()
    batch_counts = {
        "draft": 0,
        "queued": 0,
        "running": 0,
        "completed": 0,
        "partial": 0,
        "failed": 0,
        "cancelled": 0,
    }
    for item in batch_records:
        status = item.get("status")
        if status in batch_counts:
            batch_counts[status] += 1

    active_total = (
        job_counts["queued"]
        + job_counts["running"]
        + batch_counts["queued"]
        + batch_counts["running"]
    )
    return {
        "jobs": {
            "total": len(job_records),
            **job_counts,
            "quota_pending": settings.max_pending_jobs,
            "remaining_pending": remaining_pending,
        },
        "batches": {
            "total": len(batch_records),
            **batch_counts,
        },
        "active_total": active_total,
    }


def _dir_stats(path: Path, pattern: str = "*") -> tuple[int, int]:
    if not path.exists() or not path.is_dir():
        return 0, 0
    count = 0
    total_size = 0
    try:
        for p in path.glob(pattern):
            if not p.is_symlink() and p.is_file():
                try:
                    st = p.stat()
                    count += 1
                    total_size += st.st_size
                except OSError:
                    continue
    except OSError:
        pass
    return count, total_size


def get_storage_status(settings: Settings) -> dict[str, Any]:
    artifacts_dir = settings.resolved_artifacts_dir()
    reports_dir = settings.resolved_reports_dir()

    # Spectra
    sp_count, sp_size = _dir_stats(artifacts_dir / "spectra", "*.json")
    spectra_cat = {
        "record_count": sp_count,
        "size_bytes": sp_size,
        "quota_records": settings.max_stored_spectra,
        "remaining_records": max(0, settings.max_stored_spectra - sp_count),
        "quota_bytes": settings.max_storage_bytes,
        "remaining_bytes": max(0, settings.max_storage_bytes - sp_size),
    }

    # Standard Predictions
    pr_count, pr_size = _dir_stats(artifacts_dir / "predictions", "*.json")
    predictions_cat = {
        "record_count": pr_count,
        "size_bytes": pr_size,
        "quota_records": settings.max_stored_predictions,
        "remaining_records": max(0, settings.max_stored_predictions - pr_count),
        "quota_bytes": settings.max_prediction_storage_bytes,
        "remaining_bytes": max(0, settings.max_prediction_storage_bytes - pr_size),
    }

    # Measured Predictions
    mp_dir = artifacts_dir / "measured_predictions"
    mp_count, mp_json_size = _dir_stats(mp_dir, "*.json")
    _, mp_inputs_size = _dir_stats(mp_dir / "inputs", "*.csv")
    mp_total_size = mp_json_size + mp_inputs_size
    measured_cat = {
        "record_count": mp_count,
        "size_bytes": mp_total_size,
        "quota_records": settings.max_stored_measured_predictions,
        "remaining_records": max(0, settings.max_stored_measured_predictions - mp_count),
        "quota_bytes": settings.max_measured_prediction_storage_bytes,
        "remaining_bytes": max(0, settings.max_measured_prediction_storage_bytes - mp_total_size),
    }

    # Jobs
    jb_count, jb_size = _dir_stats(artifacts_dir / "jobs", "*.json")
    jobs_cat = {
        "record_count": jb_count,
        "size_bytes": jb_size,
        "quota_records": settings.max_job_records,
        "remaining_records": max(0, settings.max_job_records - jb_count),
        "quota_bytes": None,
        "remaining_bytes": None,
    }

    # Batches (metadata records)
    batches_dir = artifacts_dir / "batches"
    bt_count, bt_size = _dir_stats(batches_dir, "*.json")
    batches_cat = {
        "record_count": bt_count,
        "size_bytes": bt_size,
        "quota_records": settings.max_batch_records,
        "remaining_records": max(0, settings.max_batch_records - bt_count),
        "quota_bytes": None,
        "remaining_bytes": None,
    }

    # Batch Inputs
    bi_count, bi_size = _dir_stats(batches_dir / "inputs", "*.csv")
    batch_inputs_cat = {
        "record_count": bi_count,
        "size_bytes": bi_size,
        "quota_records": None,
        "remaining_records": None,
        "quota_bytes": settings.max_batch_input_storage_bytes,
        "remaining_bytes": max(0, settings.max_batch_input_storage_bytes - bi_size),
    }

    # Batch Cache
    bc_count, bc_size = _dir_stats(batches_dir / "cache", "*")
    batch_cache_cat = {
        "record_count": bc_count,
        "size_bytes": bc_size,
        "quota_records": None,
        "remaining_records": None,
        "quota_bytes": settings.max_batch_cache_bytes,
        "remaining_bytes": max(0, settings.max_batch_cache_bytes - bc_size),
    }

    # Experiments
    exp_dir = reports_dir / "experiments"
    exp_count = 0
    exp_size = 0
    if exp_dir.exists():
        for res in [*exp_dir.glob("*/result.json"), *exp_dir.glob("*/runs/*/result.json")]:
            if not res.is_symlink() and res.is_file():
                try:
                    exp_count += 1
                    exp_size += res.stat().st_size
                except OSError:
                    continue
    experiments_cat = {
        "record_count": exp_count,
        "size_bytes": exp_size,
        "quota_records": None,
        "remaining_records": None,
        "quota_bytes": None,
        "remaining_bytes": None,
    }

    # Free filesystem space
    try:
        usage = shutil.disk_usage(artifacts_dir)
        disk_status = {
            "total_bytes": usage.total,
            "used_bytes": usage.used,
            "free_bytes": usage.free,
        }
    except Exception:
        disk_status = {
            "total_bytes": 0,
            "used_bytes": 0,
            "free_bytes": 0,
        }

    categories = {
        "spectra": spectra_cat,
        "predictions": predictions_cat,
        "measured_predictions": measured_cat,
        "jobs": jobs_cat,
        "batches": batches_cat,
        "batch_inputs": batch_inputs_cat,
        "batch_cache": batch_cache_cat,
        "experiments": experiments_cat,
    }
    total_used_bytes = sum(cat["size_bytes"] for cat in categories.values())
    quota_headroom = sum(
        cat["remaining_bytes"] for cat in categories.values() if cat["remaining_bytes"] is not None
    )

    return {
        "categories": categories,
        "total_used_bytes": total_used_bytes,
        "quota_headroom_bytes": quota_headroom,
        "disk": disk_status,
    }


def get_backup_status(settings: Settings) -> dict[str, Any]:
    status_file = settings.resolved_artifacts_dir() / ".backup_status.json"
    if not status_file.exists() or status_file.is_symlink():
        return {
            "last_archive_verified": None,
            "last_restore_verified": None,
        }
    try:
        data = json.loads(status_file.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {
                "last_archive_verified": None,
                "last_restore_verified": None,
            }

        archive_rec = None
        raw_archive = data.get("last_archive_verified")
        if isinstance(raw_archive, dict):
            try:
                archive_rec = BackupVerificationRecord.model_validate(raw_archive).model_dump()
            except Exception:
                archive_rec = None

        restore_rec = None
        raw_restore = data.get("last_restore_verified")
        if isinstance(raw_restore, dict):
            try:
                restore_rec = RestoreVerificationRecord.model_validate(raw_restore).model_dump()
            except Exception:
                restore_rec = None

        return {
            "last_archive_verified": archive_rec,
            "last_restore_verified": restore_rec,
        }
    except Exception:
        return {
            "last_archive_verified": None,
            "last_restore_verified": None,
        }


def get_system_status(services: Services) -> dict[str, Any]:
    return {
        "worker": get_worker_status(services.settings),
        "queues": get_queue_status(services.jobs, services.batches, services.settings),
        "storage": get_storage_status(services.settings),
        "backup": get_backup_status(services.settings),
    }
