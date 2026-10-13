from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import zipfile
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import pytest
from app.core.config import Settings
from app.maintenance import backup, restore
from app.services.jobs import JobStorage
from app.services.storage import SpectrumStorage


def _concurrent_save(directory: str) -> str:
    storage = SpectrumStorage(Path(directory), max_files=20)
    return storage.save(
        {
            "origin": "uploaded",
            "reference_source": "user_upload",
            "wavelength_nm": [350, 800],
            "intensity": [1, 2],
        }
    )["spectrum_id"]


def test_concurrent_process_writes_and_backup_roundtrip(tmp_path):
    settings = Settings(artifacts_dir=tmp_path / "source", reports_dir=tmp_path / "reports")
    directory = settings.resolved_artifacts_dir() / "spectra"
    with ProcessPoolExecutor(max_workers=2) as pool:
        ids = list(pool.map(_concurrent_save, [str(directory)] * 4))
    assert len(set(ids)) == 4
    source = SpectrumStorage(directory)
    assert len(source.list()["items"]) == 4
    jobs = JobStorage(settings.resolved_artifacts_dir() / "jobs")
    job = jobs.create({"seeds": [1], "config_name": "demo", "idempotency_key": "backup-test"})
    jobs.claim_next()
    archive = tmp_path / "backup.zip"
    backup(settings, archive)
    restored_settings = Settings(
        artifacts_dir=tmp_path / "restored", reports_dir=tmp_path / "restored-reports"
    )
    assert restore(restored_settings, archive) == 5
    restored = SpectrumStorage(restored_settings.resolved_artifacts_dir() / "spectra")
    assert {item["spectrum_id"] for item in restored.list()["items"]} == set(ids)
    for spectrum_id in ids:
        assert restored.get(spectrum_id) == source.get(spectrum_id)
    restored_job = JobStorage(restored_settings.resolved_artifacts_dir() / "jobs").get(
        job["job_id"]
    )
    assert restored_job["status"] == "failed"
    assert restored_job["error_code"] == "restored_interrupted_job"
    with pytest.raises(ValueError, match="empty record directories"):
        restore(restored_settings, archive)


def test_restore_rejects_checksum_and_traversal_before_writes(tmp_path):
    settings = Settings(artifacts_dir=tmp_path / "artifacts", reports_dir=tmp_path / "reports")
    for filename, checksum in [
        ("spectra/" + "a" * 32 + ".json", "bad"),
        ("../outside.json", hashlib.sha256(b"{}").hexdigest()),
    ]:
        archive_path = tmp_path / "bad.zip"
        with zipfile.ZipFile(archive_path, "w") as archive:
            archive.writestr(filename, b"{}")
            archive.writestr(
                "manifest.json", json.dumps({"schema_version": 1, "files": {filename: checksum}})
            )
        with pytest.raises(ValueError):
            restore(settings, archive_path)
        assert not settings.resolved_artifacts_dir().exists()


def test_worker_interrupt_recovery_in_separate_process(tmp_path, monkeypatch):
    settings = Settings(artifacts_dir=tmp_path / "artifacts", reports_dir=tmp_path / "reports")
    jobs = JobStorage(settings.resolved_artifacts_dir() / "jobs")
    job = jobs.create({"seeds": [1], "config_name": "demo", "idempotency_key": "worker-test"})
    jobs.claim_next()
    report_directory = settings.resolved_reports_dir() / "experiments" / f"job-{job['job_id']}"
    report_directory.mkdir(parents=True)
    (report_directory / "result.json").write_text(json.dumps({"status": "running"}))
    child = report_directory / "runs" / "child"
    child.mkdir(parents=True)
    (child / "result.json").write_text(json.dumps({"status": "running"}))
    monkeypatch.setenv("SPECTRALAB_ARTIFACTS_DIR", str(settings.resolved_artifacts_dir()))
    monkeypatch.setenv("SPECTRALAB_REPORTS_DIR", str(settings.resolved_reports_dir()))
    result = subprocess.run(
        [sys.executable, "-m", "app.worker", "--once"], capture_output=True, timeout=30
    )
    assert result.returncode == 0, result.stderr.decode()
    assert jobs.get(job["job_id"])["status"] == "failed"
    assert json.loads((report_directory / "result.json").read_text())["status"] == "failed"
    assert json.loads((child / "result.json").read_text())["status"] == "failed"


@pytest.mark.parametrize("cancel", [True, False])
def test_worker_cancels_subprocess_and_enforces_timeout(tmp_path, monkeypatch, cancel):
    import threading

    from app import worker

    settings = Settings(
        artifacts_dir=tmp_path / "artifacts",
        reports_dir=tmp_path / "reports",
        job_timeout_seconds=1,
    )
    jobs = JobStorage(settings.resolved_artifacts_dir() / "jobs")
    jobs.create({"seeds": [1], "config_name": "demo", "idempotency_key": "cancel-request"})
    job = jobs.claim_next()
    real_popen = subprocess.Popen
    started = threading.Event()

    def slow_child(*args, **kwargs):
        child = real_popen([sys.executable, "-c", "import time; time.sleep(30)"], **kwargs)
        started.set()
        return child

    monkeypatch.setattr(worker.subprocess, "Popen", slow_child)
    thread = threading.Thread(target=worker.run_job, args=(settings, jobs, job))
    thread.start()
    assert started.wait(5)
    if cancel:
        jobs.cancel(job["job_id"])
    thread.join(timeout=10)
    assert not thread.is_alive()
    result = jobs.get(job["job_id"])
    assert result["status"] == ("cancelled" if cancel else "failed")
    if not cancel:
        assert result["error_code"] == "resource_timeout"


def test_cancel_race_with_already_exited_child_keeps_worker_healthy(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import Mock

    from app import worker

    settings = Settings(artifacts_dir=tmp_path / "artifacts", reports_dir=tmp_path / "reports")
    jobs = JobStorage(settings.resolved_artifacts_dir() / "jobs")
    jobs.create({"seeds": [1], "config_name": "demo", "idempotency_key": "cancel-exit-race"})
    claimed = jobs.claim_next()
    jobs.cancel(claimed["job_id"])
    child = SimpleNamespace(pid=999999, poll=lambda: None, wait=Mock(return_value=0))
    monkeypatch.setattr(worker.subprocess, "Popen", lambda *args, **kwargs: child)
    monkeypatch.setattr(worker.os, "killpg", Mock(side_effect=ProcessLookupError))
    worker.run_job(settings, jobs, claimed)
    assert jobs.get(claimed["job_id"])["status"] == "cancelled"
    assert jobs.get(claimed["job_id"])["error_code"] is None
    child.wait.assert_called_once_with(timeout=5)
    # The same worker remains able to claim its next job after the raced cancellation.
    next_job = jobs.create(
        {"seeds": [2], "config_name": "demo", "idempotency_key": "after-cancel-race"}
    )
    assert jobs.claim_next()["job_id"] == next_job["job_id"]


def test_cancel_race_during_kill_escalation_reaps_child(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import Mock

    from app import worker

    child = SimpleNamespace(
        pid=999999,
        poll=lambda: None,
        wait=Mock(side_effect=[subprocess.TimeoutExpired("test-child", 5), 0]),
    )
    monkeypatch.setattr(worker.os, "killpg", Mock(side_effect=[None, ProcessLookupError]))
    worker.stop_child(child)
    assert child.wait.call_count == 2
