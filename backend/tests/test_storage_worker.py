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


def test_backup_atomic_streaming_and_no_partial_on_failure(tmp_path, monkeypatch):
    from unittest.mock import Mock

    from app import maintenance

    settings = Settings(artifacts_dir=tmp_path / "artifacts", reports_dir=tmp_path / "reports")
    spectra_dir = settings.resolved_artifacts_dir() / "spectra"
    SpectrumStorage(spectra_dir).save(
        {
            "origin": "uploaded",
            "reference_source": "user_upload",
            "wavelength_nm": [350, 800],
            "intensity": [1, 2],
        }
    )

    archive = tmp_path / "final_backup.zip"

    # Simulate verification failure during backup
    monkeypatch.setattr(
        maintenance,
        "verify_backup_archive",
        Mock(side_effect=ValueError("Simulated archive corruption")),
    )

    with pytest.raises(ValueError, match="Simulated archive corruption"):
        backup(settings, archive)

    # Ensure no partial archive was published at destination
    assert not archive.exists()

    # Ensure no leftover temporary files in destination directory
    temp_files = list(archive.parent.glob(".*.tmp"))
    assert len(temp_files) == 0

    # Ensure no fake verified backup status record was written
    status_file = settings.resolved_artifacts_dir() / ".backup_status.json"
    assert not status_file.exists()


def test_backup_destination_already_exists_and_concurrent_creation(tmp_path):
    settings = Settings(artifacts_dir=tmp_path / "artifacts", reports_dir=tmp_path / "reports")
    spectra_dir = settings.resolved_artifacts_dir() / "spectra"
    SpectrumStorage(spectra_dir).save(
        {
            "origin": "uploaded",
            "reference_source": "user_upload",
            "wavelength_nm": [350, 800],
            "intensity": [1, 2],
        }
    )

    archive = tmp_path / "my_backup.zip"
    archive.write_bytes(b"existing content")

    with pytest.raises(ValueError, match="already exists"):
        backup(settings, archive)

    # Content unchanged
    assert archive.read_bytes() == b"existing content"


def test_backup_unsupported_link_fails_safely_and_crash_leaves_no_archive(tmp_path, monkeypatch):
    import errno
    import subprocess
    import sys

    from app import maintenance

    settings = Settings(artifacts_dir=tmp_path / "artifacts", reports_dir=tmp_path / "reports")
    (settings.resolved_artifacts_dir() / "spectra").mkdir(parents=True, exist_ok=True)
    archive = tmp_path / "test_unsupported.zip"

    def fail_link(src, dst):
        raise OSError(errno.ENOTSUP, "Operation not supported")

    monkeypatch.setattr(maintenance.os, "link", fail_link)
    with pytest.raises(OSError) as exc_info:
        maintenance.backup(settings, archive)
    assert exc_info.value.errno == errno.ENOTSUP
    assert not archive.exists()

    # Process crash during backup must never leave a partial archive at destination
    child_code = r"""
import os, sys, zipfile
from pathlib import Path
from app import maintenance
from app.core.config import Settings
root = Path(sys.argv[1])
(root / "artifacts" / "spectra").mkdir(parents=True, exist_ok=True)
def crash_writestr(self, *args, **kwargs):
    os._exit(95)
zipfile.ZipFile.writestr = crash_writestr
stg = Settings(artifacts_dir=root / "artifacts", reports_dir=root / "reports")
maintenance.backup(stg, root / "crashed.zip")
"""
    res = subprocess.run([sys.executable, "-c", child_code, str(tmp_path)], capture_output=True)
    assert res.returncode == 95
    assert not (tmp_path / "crashed.zip").exists()


def test_backup_restore_measured_predictions_and_no_worker_heartbeat_restored(tmp_path):
    from app.services.storage import MeasuredPredictionStorage

    settings = Settings(artifacts_dir=tmp_path / "artifacts", reports_dir=tmp_path / "reports")
    mp_storage = MeasuredPredictionStorage(
        settings.resolved_artifacts_dir() / "measured_predictions"
    )
    saved_pred = mp_storage.save(
        result={
            "schema_version": 3,
            "predictions": [{"element": "Na", "detected": True, "accepted": True}],
            "instrument_id": "chemcam-lanl-testbed",
        },
        profile_id="calcium-v4",
        input_provenance={"format": "csv", "input_points": 10},
        raw_csv_bytes=b"wavelength_nm,intensity\n400,1\n",
    )

    # Write a live worker heartbeat
    jobs_dir = settings.resolved_artifacts_dir() / "jobs"
    jobs_dir.mkdir(parents=True, exist_ok=True)
    heartbeat_file = jobs_dir / ".worker_heartbeat.json"
    heartbeat_file.write_text(json.dumps({"status": "running", "worker_id": "live-worker-1"}))

    archive = tmp_path / "backup_with_measured.zip"
    backup(settings, archive)
    assert archive.exists()

    # Check status record recorded after backup
    status_file = settings.resolved_artifacts_dir() / ".backup_status.json"
    assert status_file.exists()
    status_data = json.loads(status_file.read_text())
    assert status_data["last_archive_verified"]["archive_name"] == archive.name
    assert status_data["last_archive_verified"]["verification_passed"] is True

    # Restore to a fresh location
    restored_settings = Settings(
        artifacts_dir=tmp_path / "restored", reports_dir=tmp_path / "restored_reports"
    )
    count = restore(restored_settings, archive)
    assert count >= 1

    # Measured prediction is restored
    restored_mp = MeasuredPredictionStorage(
        restored_settings.resolved_artifacts_dir() / "measured_predictions"
    )
    restored_record = restored_mp.get(saved_pred["prediction_id"])
    assert restored_record["prediction_id"] == saved_pred["prediction_id"]
    assert restored_record["profile_id"] == "calcium-v4"
    assert (
        restored_mp.get_raw_csv(saved_pred["prediction_id"]) == b"wavelength_nm,intensity\n400,1\n"
    )

    # Worker heartbeat was NOT restored (temporary heartbeat must never restore as alive worker)
    restored_heartbeat = (
        restored_settings.resolved_artifacts_dir() / "jobs" / ".worker_heartbeat.json"
    )
    assert not restored_heartbeat.exists()

    # Check restore verification recorded in restored location
    restored_status_file = restored_settings.resolved_artifacts_dir() / ".backup_status.json"
    assert restored_status_file.exists()
    restored_status_data = json.loads(restored_status_file.read_text())
    assert restored_status_data["last_restore_verified"]["verification_passed"] is True
    assert restored_status_data["last_restore_verified"]["restored_records_count"] == count


def test_measured_storage_quota_enforces_csv_and_json(tmp_path):
    from app.services.storage import MeasuredPredictionStorage

    mp_dir = tmp_path / "measured_predictions"
    storage = MeasuredPredictionStorage(mp_dir, max_files=10, max_bytes=1000)

    # 1. Attempt to save a record where CSV alone exceeds 1000 bytes
    large_csv = b"wavelength_nm,intensity\n" + (b"400.0,1.0\n" * 120)
    with pytest.raises(OSError) as exc:
        storage.save(
            result={"schema_version": 3, "predictions": []},
            profile_id="calcium-v4",
            input_provenance={"format": "csv"},
            raw_csv_bytes=large_csv,
        )
    assert exc.value.errno == 28  # ENOSPC
    assert not list(mp_dir.glob("*.json"))
    assert not list((mp_dir / "inputs").glob("*.csv"))

    # 2. Save a small record within quota
    small_csv = b"wavelength_nm,intensity\n400.0,1.0\n"
    rec1 = storage.save(
        result={"schema_version": 3, "predictions": []},
        profile_id="calcium-v4",
        input_provenance={"format": "csv"},
        raw_csv_bytes=small_csv,
    )
    assert rec1["prediction_id"]
    assert (mp_dir / f"{rec1['prediction_id']}.json").exists()
    assert (mp_dir / "inputs" / f"{rec1['prediction_id']}.csv").exists()

    # 3. Next record with CSV size exceeding remaining headroom must fail
    with pytest.raises(OSError) as exc:
        storage.save(
            result={"schema_version": 3, "predictions": []},
            profile_id="calcium-v4",
            input_provenance={"format": "csv"},
            raw_csv_bytes=b"wavelength_nm,intensity\n" + (b"500.0,2.0\n" * 80),
        )
    assert exc.value.errno == 28
    # Only 1 record exists
    assert len(list(mp_dir.glob("*.json"))) == 1
    assert len(list((mp_dir / "inputs").glob("*.csv"))) == 1


def test_measured_storage_rollback_on_csv_error(tmp_path, monkeypatch):
    import os

    from app.services.storage import MeasuredPredictionStorage

    mp_dir = tmp_path / "measured_predictions"
    storage = MeasuredPredictionStorage(mp_dir, max_files=10, max_bytes=100_000)

    # Simulate an error while publishing CSV
    original_replace = os.replace

    def failing_replace(src, dst):
        if str(dst).endswith(".csv"):
            raise OSError(28, "Simulated disk full during CSV publish")
        return original_replace(src, dst)

    monkeypatch.setattr(os, "replace", failing_replace)

    with pytest.raises(OSError, match="Simulated disk full"):
        storage.save(
            result={"schema_version": 3, "predictions": []},
            profile_id="calcium-v4",
            input_provenance={"format": "csv"},
            raw_csv_bytes=b"wavelength_nm,intensity\n400,1\n",
        )

    # Verify no published files and no temp files remain
    assert not list(mp_dir.glob("*.json"))
    assert not list(mp_dir.glob(".*.tmp"))
    if (mp_dir / "inputs").exists():
        assert not list((mp_dir / "inputs").glob("*.csv"))
        assert not list((mp_dir / "inputs").glob(".*.tmp"))


def test_backup_restore_strictly_validates_raw_csv_and_sha256(tmp_path):
    from app.services.storage import MeasuredPredictionStorage

    settings = Settings(artifacts_dir=tmp_path / "artifacts", reports_dir=tmp_path / "reports")
    mp_root = settings.resolved_artifacts_dir() / "measured_predictions"
    mp_storage = MeasuredPredictionStorage(mp_root)
    csv_bytes = b"wavelength_nm,intensity\n400,1\n"
    csv_sha = hashlib.sha256(csv_bytes).hexdigest()
    pred = mp_storage.save(
        result={"schema_version": 3, "predictions": []},
        profile_id="calcium-v4",
        input_provenance={"format": "csv", "raw_csv_sha256": csv_sha, "input_sha256": csv_sha},
        raw_csv_bytes=csv_bytes,
    )

    pred_id = pred["prediction_id"]
    csv_file = mp_root / "inputs" / f"{pred_id}.csv"

    # 1. Missing CSV fails backup
    csv_file.unlink()
    archive = tmp_path / "backup_missing_csv.zip"
    with pytest.raises(ValueError, match=r"(?i)missing raw CSV"):
        backup(settings, archive)
    assert not archive.exists()

    # 2. Corrupted CSV fails backup
    csv_file.write_bytes(b"wavelength_nm,intensity\n999,999\n")
    with pytest.raises(ValueError, match="checksum mismatch"):
        backup(settings, archive)
    assert not archive.exists()

    # 3. Restore valid backup
    csv_file.write_bytes(csv_bytes)
    valid_archive = tmp_path / "valid_backup.zip"
    backup(settings, valid_archive)
    assert valid_archive.exists()

    # Tamper with CSV inside the ZIP
    tampered_archive = tmp_path / "tampered_backup.zip"
    with (
        zipfile.ZipFile(valid_archive, "r") as src_zip,
        zipfile.ZipFile(tampered_archive, "w") as dst_zip,
    ):
        for item in src_zip.infolist():
            data = src_zip.read(item.filename)
            if item.filename.endswith(".csv") and "inputs" in item.filename:
                data = b"tampered"
            dst_zip.writestr(item, data)

    restored_settings = Settings(
        artifacts_dir=tmp_path / "restored", reports_dir=tmp_path / "restored_rep"
    )
    with pytest.raises(ValueError, match=r"checksum mismatch|corrupted|checksum"):
        restore(restored_settings, tampered_archive)
    # Ensure nothing was restored
    assert not (restored_settings.resolved_artifacts_dir() / "measured_predictions").exists()


def test_backup_publication_atomic_no_overwrite_concurrency(tmp_path, monkeypatch):
    import errno
    import os

    settings = Settings(artifacts_dir=tmp_path / "artifacts", reports_dir=tmp_path / "reports")
    (settings.resolved_artifacts_dir() / "spectra").mkdir(parents=True, exist_ok=True)

    archive = tmp_path / "backup.zip"

    # Simulate link failure and a concurrent writer creating the target file
    def fake_link(src, dst):
        Path(dst).write_bytes(b"concurrently created by another process")
        raise OSError(errno.EXDEV, "Cross-device link not supported")

    monkeypatch.setattr(os, "link", fake_link)

    with pytest.raises(ValueError, match="already exists"):
        backup(settings, archive)

    # The existing archive MUST NOT be overwritten!
    assert archive.read_bytes() == b"concurrently created by another process"
