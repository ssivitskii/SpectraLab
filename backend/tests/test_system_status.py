from __future__ import annotations

import fcntl
import json
from datetime import UTC, datetime, timedelta

import pytest
from app.core.config import Settings
from app.main import app
from app.services.container import build_services
from app.services.storage import atomic_json, now_iso
from app.services.system_status import get_worker_status
from app.worker import WorkerHeartbeatManager
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path):
    artifacts = tmp_path / "artifacts"
    reports = tmp_path / "reports"
    artifacts.mkdir(parents=True, exist_ok=True)
    reports.mkdir(parents=True, exist_ok=True)
    with TestClient(app) as client:
        app.state.services = build_services(Settings(artifacts_dir=artifacts, reports_dir=reports))
        yield client


def test_health_endpoint_contract_preserved(client):
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    data = response.json()
    assert "status" in data
    assert data["status"] == "ok"
    assert "reference_ready" in data
    assert isinstance(data["reference_ready"], bool)
    assert "ready_models" in data
    assert isinstance(data["ready_models"], list)


def test_worker_status_absent_when_no_heartbeat(client):
    response = client.get("/api/v1/system/status")
    assert response.status_code == 200
    data = response.json()
    assert "worker" in data
    worker = data["worker"]
    assert worker["state"] == "absent"
    assert worker["worker_id"] is None
    assert worker["pid"] is None
    assert worker["current_task"] is None
    assert worker["heartbeat_interval_seconds"] > 0
    assert worker["stale_threshold_seconds"] > 0


def test_worker_status_running_and_stale_transitions(tmp_path):
    settings = Settings(
        artifacts_dir=tmp_path / "artifacts",
        reports_dir=tmp_path / "reports",
        worker_heartbeat_interval_seconds=1.0,
        worker_heartbeat_stale_seconds=5.0,
    )
    jobs_dir = settings.resolved_artifacts_dir() / "jobs"
    jobs_dir.mkdir(parents=True, exist_ok=True)
    lock_file = jobs_dir / ".worker.lock"

    # Worker acquires lock and writes heartbeat
    with lock_file.open("a+b") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)

        manager = WorkerHeartbeatManager(
            jobs_dir, worker_id="test-worker-1", mode="default", interval=1.0
        )
        manager.write_heartbeat(status="running")

        # Live and fresh
        status = get_worker_status(settings)
        assert status["state"] == "running"
        assert status["worker_id"] == "test-worker-1"
        assert status["current_task"] is None

        # Set active task
        manager.set_task("job", "job-123", "Demo seeds")
        status = get_worker_status(settings)
        assert status["state"] == "running"
        assert status["current_task"] == {
            "kind": "job",
            "task_id": "job-123",
            "description": "Demo seeds",
            "started_at": status["current_task"]["started_at"],
        }

        # Simulate time jump beyond stale threshold
        old_time = (datetime.now(UTC) - timedelta(seconds=10)).isoformat()
        heartbeat_file = jobs_dir / ".worker_heartbeat.json"
        atomic_json(
            heartbeat_file,
            {
                "worker_id": "test-worker-1",
                "pid": 12345,
                "mode": "default",
                "status": "running",
                "started_at": old_time,
                "updated_at": old_time,
                "current_task": {"kind": "job", "task_id": "job-123"},
                "interval_seconds": 1.0,
            },
        )
        status = get_worker_status(settings)
        assert status["state"] == "stale"
        assert "outdated" in status["message"]

        # Clean stop
        manager.stop()
        status = get_worker_status(settings)
        assert status["state"] == "stopped"
        assert status["current_task"] is None


def test_worker_status_stale_if_lock_released(tmp_path):
    settings = Settings(
        artifacts_dir=tmp_path / "artifacts",
        reports_dir=tmp_path / "reports",
        worker_heartbeat_stale_seconds=30.0,
    )
    jobs_dir = settings.resolved_artifacts_dir() / "jobs"
    jobs_dir.mkdir(parents=True, exist_ok=True)
    heartbeat_file = jobs_dir / ".worker_heartbeat.json"

    # Heartbeat file claims running with fresh timestamp, but no process holds .worker.lock
    atomic_json(
        heartbeat_file,
        {
            "worker_id": "crashed-worker",
            "pid": 99999,
            "mode": "default",
            "status": "running",
            "started_at": now_iso(),
            "updated_at": now_iso(),
            "current_task": None,
            "interval_seconds": 5.0,
        },
    )
    status = get_worker_status(settings)
    assert status["state"] == "stale"
    assert "no longer active" in status["message"]


def test_worker_status_corrupted_heartbeat_file(tmp_path):
    settings = Settings(
        artifacts_dir=tmp_path / "artifacts",
        reports_dir=tmp_path / "reports",
    )
    jobs_dir = settings.resolved_artifacts_dir() / "jobs"
    jobs_dir.mkdir(parents=True, exist_ok=True)
    heartbeat_file = jobs_dir / ".worker_heartbeat.json"
    heartbeat_file.write_text("corrupted json content")

    status = get_worker_status(settings)
    assert status["state"] == "stale"
    assert "unreadable" in status["message"]


def test_queue_and_storage_counts(client):
    services = app.state.services
    jobs = services.jobs
    jobs.create({"seeds": [11], "config_name": "demo", "idempotency_key": "q-test-1"})
    jobs.create({"seeds": [22], "config_name": "demo", "idempotency_key": "q-test-2"})
    jobs.claim_next()  # job1 becomes running

    # Create dummy spectrum to count
    services.spectra.save(
        {
            "origin": "generated",
            "reference_source": "demo_fixture",
            "wavelength_nm": [350.0, 500.0, 800.0],
            "intensity": [0.1, 0.5, 0.2],
            "parameters": {"snr_db": 20},
        }
    )

    response = client.get("/api/v1/system/status")
    assert response.status_code == 200
    data = response.json()

    # Queue counts
    queues = data["queues"]
    assert queues["jobs"]["total"] == 2
    assert queues["jobs"]["running"] == 1
    assert queues["jobs"]["queued"] == 1
    assert queues["jobs"]["remaining_pending"] == services.settings.max_pending_jobs - 2
    assert queues["batches"]["total"] == 0

    # Storage categories
    storage = data["storage"]
    categories = storage["categories"]
    assert "spectra" in categories
    assert categories["spectra"]["record_count"] == 1
    assert categories["spectra"]["size_bytes"] > 0
    assert categories["spectra"]["remaining_records"] == services.settings.max_stored_spectra - 1

    assert "predictions" in categories
    assert categories["predictions"]["record_count"] == 0

    assert "measured_predictions" in categories
    assert categories["measured_predictions"]["record_count"] == 0

    assert "jobs" in categories
    assert categories["jobs"]["record_count"] == 2

    # Disk usage
    disk = storage["disk"]
    assert disk["total_bytes"] > 0
    assert disk["free_bytes"] > 0


def test_backup_status_in_system_status(client, tmp_path):
    # Initially no backup record
    res = client.get("/api/v1/system/status")
    backup_data = res.json()["backup"]
    assert backup_data["last_archive_verified"] is None
    assert backup_data["last_restore_verified"] is None

    # Write backup status file
    status_file = app.state.services.settings.resolved_artifacts_dir() / ".backup_status.json"
    status_payload = {
        "last_archive_verified": {
            "completed_at": now_iso(),
            "archive_name": "test-backup.zip",
            "archive_sha256": "0" * 64,
            "archive_size_bytes": 1024,
            "records_count": 5,
            "verification_passed": True,
            "details": "Archive verified with 5 records",
        },
        "last_restore_verified": {
            "completed_at": now_iso(),
            "archive_name": "test-backup.zip",
            "archive_sha256": "0" * 64,
            "restored_records_count": 5,
            "verification_passed": True,
            "details": "Restored 5 records",
        },
    }
    atomic_json(status_file, status_payload)

    res = client.get("/api/v1/system/status")
    backup_data = res.json()["backup"]
    assert backup_data["last_archive_verified"]["archive_name"] == "test-backup.zip"
    assert backup_data["last_archive_verified"]["verification_passed"] is True
    assert backup_data["last_restore_verified"]["restored_records_count"] == 5


def test_system_status_survives_malformed_backup_status(client):
    # Review reproduction: .backup_status.json with partial/malformed data must not trigger HTTP 500
    status_file = app.state.services.settings.resolved_artifacts_dir() / ".backup_status.json"
    status_file.write_text(json.dumps({"last_archive_verified": {"verification_passed": True}}))

    res = client.get("/api/v1/system/status")
    assert res.status_code == 200
    data = res.json()
    assert "backup" in data
    assert data["backup"]["last_archive_verified"] is None
    assert "worker" in data
    assert "queues" in data
    assert "storage" in data


def test_system_status_survives_malformed_heartbeat(client):
    # Review reproduction: heartbeat with invalid types must not trigger HTTP 500
    jobs_dir = app.state.services.settings.resolved_artifacts_dir() / "jobs"
    jobs_dir.mkdir(parents=True, exist_ok=True)
    heartbeat_file = jobs_dir / ".worker_heartbeat.json"
    heartbeat_file.write_text(json.dumps({"status": "stopped", "worker_id": [], "pid": {}}))

    res = client.get("/api/v1/system/status")
    assert res.status_code == 200
    data = res.json()
    assert data["worker"]["state"] in ("stale", "stopped", "absent")
    assert "queues" in data
    assert "storage" in data


def test_system_status_schema_extensions(client):
    res = client.get("/api/v1/system/status")
    assert res.status_code == 200
    data = res.json()

    # R7 schema alignments
    assert "state" in data["worker"]
    assert "status" in data["worker"]
    assert data["worker"]["status"] == data["worker"]["state"]

    assert "active_total" in data["queues"]
    assert isinstance(data["queues"]["active_total"], int)

    assert "total_used_bytes" in data["storage"]
    assert "disk" in data["storage"]
    assert "path" not in data["storage"]["disk"]
