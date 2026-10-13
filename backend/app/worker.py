"""Local research worker. Run separately: python -m app.worker [--once].

The HTTP process only writes durable queue records. One worker holds an exclusive
host-local lock and each series runs in its own bounded subprocess. Interrupted
jobs become failed; they never silently resume or claim success.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import logging
import os
import resource
import signal
import subprocess
import sys
import threading
import time
from contextlib import suppress
from pathlib import Path

from app.core.config import Settings
from app.services.batches import BATCH_TERMINAL, BatchStorage
from app.services.jobs import JobStorage
from app.services.storage import atomic_json

logger = logging.getLogger("spectralab.worker")


def execute_series(settings: Settings, job_id: str) -> None:
    # Imported only inside the child, never in request handlers or queue management.
    from spectralab_ml.experiments import run_series

    jobs = JobStorage(settings.resolved_artifacts_dir() / "jobs")
    job = jobs.get(job_id)
    config_name = "smoke.yaml" if job["config_name"] == "demo" else "baseline_comparison.yaml"
    run_series(
        settings.project_root / "ml" / "configs" / "experiments" / config_name,
        output_root=settings.resolved_reports_dir() / "experiments",
        seeds=job["seeds"],
        reference_cache=settings.reference_cache,
        reference_sha256=settings.reference_sha256,
        run_id=f"job-{job_id}",
    )


def bound_resources(settings: Settings) -> None:
    resource.setrlimit(
        resource.RLIMIT_CPU, (settings.job_timeout_seconds, settings.job_timeout_seconds)
    )
    # macOS does not reliably implement RLIMIT_AS; CPU/wall time and seed limits still apply there.
    if sys.platform.startswith("linux"):
        memory_bytes = settings.worker_memory_mb * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (memory_bytes, memory_bytes))
    resource.setrlimit(resource.RLIMIT_FSIZE, (512 * 1024 * 1024, 512 * 1024 * 1024))


def install_parent_watchdog() -> None:
    expected_parent = int(os.environ.get("SPECTRALAB_WORKER_PARENT_PID", os.getppid()))

    def watch_parent():
        while os.getppid() == expected_parent:
            time.sleep(0.5)
        os._exit(70)

    threading.Thread(target=watch_parent, daemon=True).start()


def worker_environment(settings: Settings) -> dict[str, str]:
    environment = os.environ.copy()
    for name, value in settings.model_dump(mode="json").items():
        key = f"SPECTRALAB_{name.upper()}"
        if value is None:
            environment.pop(key, None)
        else:
            environment[key] = json.dumps(value) if isinstance(value, (list, dict)) else str(value)
    environment.update(
        {
            "SPECTRALAB_ARTIFACTS_DIR": str(settings.resolved_artifacts_dir()),
            "SPECTRALAB_REPORTS_DIR": str(settings.resolved_reports_dir()),
            "SPECTRALAB_WORKER_PARENT_PID": str(os.getpid()),
            "OMP_NUM_THREADS": "1",
            "OPENBLAS_NUM_THREADS": "1",
            "MKL_NUM_THREADS": "1",
        }
    )
    return environment


def stop_child(child: subprocess.Popen) -> None:
    if child.poll() is not None:
        return
    try:
        os.killpg(child.pid, signal.SIGTERM)
    except ProcessLookupError:
        # The process can exit after poll() but before the group receives the signal.
        # Reap it normally; cancellation must not be reported as a worker failure.
        child.wait(timeout=5)
        return
    try:
        child.wait(timeout=5)
    except subprocess.TimeoutExpired:
        # It may exit between the timeout and the escalation.
        with suppress(ProcessLookupError):
            os.killpg(child.pid, signal.SIGKILL)
        child.wait(timeout=5)


def read_series(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def finalize_interrupted_report(settings: Settings, job: dict) -> None:
    if job["status"] not in {"failed", "cancelled"}:
        return
    directory = settings.resolved_reports_dir() / "experiments" / f"job-{job['job_id']}"
    for path in [directory / "result.json", *directory.glob("runs/*/result.json")]:
        report = read_series(path)
        if report and report.get("status") == "running":
            report.update(status=job["status"], error=job.get("error_code") or "cancelled")
            atomic_json(path, report)


def run_job(settings: Settings, jobs: JobStorage, job: dict) -> None:
    job_id = job["job_id"]
    run_id = f"job-{job_id}"
    result_path = settings.resolved_reports_dir() / "experiments" / run_id / "result.json"
    jobs.update(job_id, run_id=run_id)
    environment = os.environ.copy()
    environment.update(
        {
            "SPECTRALAB_PROJECT_ROOT": str(settings.project_root),
            "SPECTRALAB_ARTIFACTS_DIR": str(settings.resolved_artifacts_dir()),
            "SPECTRALAB_REPORTS_DIR": str(settings.resolved_reports_dir()),
            "SPECTRALAB_JOB_TIMEOUT_SECONDS": str(settings.job_timeout_seconds),
            "SPECTRALAB_WORKER_MEMORY_MB": str(settings.worker_memory_mb),
            "SPECTRALAB_WORKER_PARENT_PID": str(os.getpid()),
            "OMP_NUM_THREADS": "1",
            "OPENBLAS_NUM_THREADS": "1",
            "MKL_NUM_THREADS": "1",
        }
    )
    if settings.reference_cache is not None:
        environment["SPECTRALAB_REFERENCE_CACHE"] = str(settings.reference_cache)
    if settings.reference_sha256 is not None:
        environment["SPECTRALAB_REFERENCE_SHA256"] = settings.reference_sha256
    started = time.monotonic()
    child = None
    try:
        with (jobs.directory / f"{job_id}.log").open("wb") as output:
            child = subprocess.Popen(
                [sys.executable, "-m", "app.worker", "--execute", job_id],
                cwd=settings.project_root,
                stdout=output,
                stderr=subprocess.STDOUT,
                env=environment,
                start_new_session=True,
            )
            while child.poll() is None:
                current = jobs.get(job_id)
                series = read_series(result_path)
                if series:
                    progress = {
                        "completed": len(series.get("seed_runs", [])),
                        "total": len(job["seeds"]),
                    }
                    if progress != current["progress"]:
                        jobs.update(job_id, progress=progress)
                if current["cancellation_requested"]:
                    stop_child(child)
                    jobs.update(job_id, status="cancelled")
                    return
                if time.monotonic() - started > settings.job_timeout_seconds:
                    stop_child(child)
                    jobs.update(
                        job_id,
                        status="failed",
                        error_code="resource_timeout",
                        error_message="Research job exceeded its configured wall-time limit",
                    )
                    return
                time.sleep(0.25)
        series = read_series(result_path)
        if jobs.get(job_id)["cancellation_requested"]:
            jobs.update(job_id, status="cancelled")
        elif child.returncode == 0 and series and series.get("status") == "completed":
            jobs.update(
                job_id,
                status="completed",
                progress={"completed": len(job["seeds"]), "total": len(job["seeds"])},
            )
        else:
            jobs.update(
                job_id,
                status="failed",
                error_code="series_failed",
                error_message="Series did not complete; inspect its report or local worker log",
            )
    except BaseException:
        if child is not None:
            stop_child(child)
        jobs.update(
            job_id,
            status="failed",
            error_code="worker_interrupted",
            error_message="Worker stopped before completion; retry with a new request key",
        )
        raise
    finally:
        finalize_interrupted_report(settings, jobs.get(job_id))


def run_batch(settings: Settings, batches: BatchStorage, batch: dict) -> None:
    batch_id = batch["batch_id"]
    child = None
    started = time.monotonic()
    try:
        with (batches.directory / f"{batch_id}.log").open("wb") as output:
            child = subprocess.Popen(
                [sys.executable, "-m", "app.batch", "--execute", batch_id],
                cwd=settings.project_root,
                stdout=output,
                stderr=subprocess.STDOUT,
                env=worker_environment(settings),
                start_new_session=True,
            )
            while child.poll() is None:
                if batches.get(batch_id)["cancellation_requested"]:
                    stop_child(child)
                    batches.finish(batch_id, "cancelled")
                    return
                if time.monotonic() - started > settings.job_timeout_seconds:
                    stop_child(child)
                    batches.finish(batch_id, "failed", "resource_timeout")
                    return
                time.sleep(0.25)
        final = batches.get(batch_id)
        if final["cancellation_requested"]:
            batches.finish(batch_id, "cancelled")
        elif child.returncode != 0 or final["status"] not in BATCH_TERMINAL:
            batches.finish(batch_id, "failed", "worker_failed")
    except BaseException:
        if child is not None:
            stop_child(child)
        batches.finish(batch_id, "failed", "worker_interrupted")
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true", help="Process at most one queued job")
    parser.add_argument("--execute", help=argparse.SUPPRESS)
    parser.add_argument(
        "--batches-only", action="store_true", help="Process only CSV analysis batches"
    )
    args = parser.parse_args()
    settings = Settings()
    if args.execute:
        install_parent_watchdog()
        bound_resources(settings)
        execute_series(settings, args.execute)
        return
    jobs = JobStorage(settings.resolved_artifacts_dir() / "jobs")
    batches = BatchStorage(settings.resolved_artifacts_dir() / "batches", settings)
    with (jobs.directory / ".worker.lock").open("a+b") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit("A worker is already running for this local storage") from None
        recovered = [] if args.batches_only else jobs.recover_interrupted()
        if any(batch["status"] == "running" for batch in batches.list()):
            time.sleep(1)
        batches.recover_interrupted()
        if recovered:
            # A child of a forcibly killed predecessor exits via its 0.5 s parent watchdog.
            time.sleep(1)
            for record in recovered:
                finalize_interrupted_report(settings, record)

        def interrupted(signum, frame):
            raise KeyboardInterrupt

        signal.signal(signal.SIGTERM, interrupted)
        while True:
            batch = batches.claim()
            if batch:
                run_batch(settings, batches, batch)
            elif not args.batches_only:
                job = jobs.claim_next()
                if job:
                    run_job(settings, jobs, job)
            if args.once:
                break
            time.sleep(1)


if __name__ == "__main__":
    main()
