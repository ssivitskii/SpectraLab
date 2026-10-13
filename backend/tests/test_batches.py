from __future__ import annotations

import hashlib
import io
import json
import subprocess
import sys
import zipfile

import pytest
from app.core.config import Settings
from app.main import app
from app.maintenance import backup, restore
from app.services.batches import process_batch
from app.services.container import build_services
from app.worker import worker_environment
from fastapi.testclient import TestClient

CSV = b"wavelength_nm,intensity\n350,1\n800,2\n"


@pytest.fixture
def client(tmp_path):
    with TestClient(app) as client:
        app.state.services = build_services(
            Settings(artifacts_dir=tmp_path / "artifacts", reports_dir=tmp_path / "reports")
        )
        yield client


def descriptor(name="folder/sample.csv", raw=CSV):
    return {
        "relative_path": name,
        "sha256": hashlib.sha256(raw).hexdigest(),
        "size_bytes": len(raw),
    }


def create(client, files=None, key="batch-request-1", metadata=None):
    response = client.post(
        "/api/v1/batches",
        json={
            "name": "Folder analysis",
            "model_ids": ["builtin-nnls"],
            "expected_files": files or [descriptor()],
            "idempotency_key": key,
            "measurement_metadata": metadata or {},
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def upload(client, batch, name="folder/sample.csv", raw=CSV):
    response = client.post(
        f"/api/v1/batches/{batch['batch_id']}/files",
        data={"relative_path": name},
        files={"file": (name, raw, "text/csv")},
    )
    assert response.status_code == 200, response.text
    return response.json()


def run(client, batch):
    response = client.post(f"/api/v1/batches/{batch['batch_id']}/start")
    assert response.status_code == 202, response.text
    services = app.state.services
    assert services.batches.claim(batch["batch_id"])
    process_batch(services, batch["batch_id"])
    response = client.get(f"/api/v1/batches/{batch['batch_id']}")
    assert response.status_code == 200, response.text
    return response.json()


def test_batch_manifest_upload_rejections_and_export(client):
    invalid = {"relative_path": "folder/too-large.csv", "sha256": None, "size_bytes": 9_000_000}
    batch = create(
        client, [descriptor("=sample.csv"), invalid], metadata={"sample_id": "@untrusted"}
    )
    assert batch["status"] == "draft"
    assert client.post(f"/api/v1/batches/{batch['batch_id']}/start").status_code == 409
    good = upload(client, batch, "=sample.csv")
    assert good["status"] == "ready" and good["point_count"] == 2
    rejected = client.post(
        f"/api/v1/batches/{batch['batch_id']}/rejections",
        json={
            **invalid,
            "error_code": "file_too_large",
            "error_message": "Upload size limit exceeded",
        },
    )
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["validation_origin"] == "client"
    finished = run(client, batch)
    assert finished["status"] == "partial"
    assert finished["progress"] == {"completed": 2, "failed": 1, "total": 2}
    assert finished["results"][0]["prediction"]["model_id"] == "builtin-nnls"
    assert finished["results"][1]["error_code"] == "file_too_large"
    exported = client.get(f"/api/v1/batches/{batch['batch_id']}/export")
    assert exported.status_code == 200
    with zipfile.ZipFile(io.BytesIO(exported.content)) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        for name, item in manifest["files"].items():
            assert hashlib.sha256(archive.read(name)).hexdigest() == item["sha256"]
        assert archive.read(f"inputs/{good['file_id']}.csv") == CSV
        report = json.loads(archive.read("report.json"))
        assert report["files"][0]["relative_path"] == "=sample.csv"
        assert report["files"][0]["measurement_metadata"]["sample_id"] == "@untrusted"
        assert (
            str(app.state.services.settings.project_root)
            not in archive.read("report.json").decode()
        )
        assert "'=sample.csv" in archive.read("summary.csv").decode()
        assert "'@untrusted" in archive.read("summary.csv").decode()


def test_batch_intake_idempotency_and_server_validation(client):
    malformed = b"wavelength_nm,intensity\n350,nan\n800,2\n"
    batch = create(client, [descriptor(), descriptor("bad.csv", malformed)])
    repeated = create(client, [descriptor(), descriptor("bad.csv", malformed)])
    assert repeated["batch_id"] == batch["batch_id"]
    first = upload(client, batch)
    assert upload(client, batch)["spectrum_id"] == first["spectrum_id"]
    bad = upload(client, batch, "bad.csv", malformed)
    assert bad["status"] == "invalid" and bad["validation_origin"] == "server"
    altered = client.post(
        f"/api/v1/batches/{batch['batch_id']}/files",
        data={"relative_path": "folder/sample.csv"},
        files={"file": ("sample.csv", CSV + b"x", "text/csv")},
    )
    assert altered.status_code == 409
    assert len(list(app.state.services.spectra.directory.glob("*.json"))) == 1
    assert client.get("/api/v1/batches").json()[0]["batch_id"] == batch["batch_id"]


def test_batch_cache_reuses_inference_but_preserves_file_provenance(client, monkeypatch):
    first = create(client, metadata={"session_id": "first"})
    upload(client, first)
    completed = run(client, first)
    second = create(client, key="batch-request-2", metadata={"session_id": "second"})
    upload(client, second)

    def forbidden(*args, **kwargs):
        raise AssertionError("Identical bytes and model identity must reuse cached inference")

    monkeypatch.setattr(app.state.services.registry, "predict", forbidden)
    # Batch snapshots remain available even after the separate history entry was deleted.
    second_file = app.state.services.batches.get(second["batch_id"])["files"][0]
    app.state.services.spectra.delete(second_file["spectrum_id"])
    reused = run(client, second)
    assert reused["status"] == "completed"
    assert reused["results"][0]["cache_hit"] is True
    assert reused["files"][0]["measurement_metadata"]["session_id"] == "second"
    assert reused["model_identities"] == completed["model_identities"]
    prediction_id = reused["results"][0]["prediction_id"]
    saved = app.state.services.predictions.get(prediction_id)
    assert saved["input"]["measurement_metadata"]["session_id"] == "second"
    # Simulate a crash after prediction persistence but before the batch row is committed.
    storage = app.state.services.batches
    storage.result(
        reused["batch_id"], reused["files"][0]["file_id"], "builtin-nnls", status="failed"
    )
    storage.finish(reused["batch_id"], "failed", "worker_interrupted")
    count = len(list(app.state.services.predictions.directory.glob("*.json")))
    assert client.post(f"/api/v1/batches/{reused['batch_id']}/resume").status_code == 202
    storage.claim(reused["batch_id"])
    process_batch(app.state.services, reused["batch_id"])
    recovered = storage.get(reused["batch_id"])
    assert recovered["results"][0]["prediction_id"] == prediction_id
    assert len(list(app.state.services.predictions.directory.glob("*.json"))) == count


def test_resume_preserves_completed_rows_and_model_identity(client, monkeypatch):
    raw_two = CSV.replace(b"800,2", b"800,3")
    batch = create(client, [descriptor(), descriptor("second.csv", raw_two)])
    upload(client, batch)
    upload(client, batch, "second.csv", raw_two)
    original = app.state.services.registry.predict
    count = 0

    def flaky(*args, **kwargs):
        nonlocal count
        count += 1
        if count == 2:
            raise ValueError("Temporary local inference failure")
        return original(*args, **kwargs)

    monkeypatch.setattr(app.state.services.registry, "predict", flaky)
    partial = run(client, batch)
    assert partial["status"] == "partial"
    first_prediction_id = partial["results"][0]["prediction_id"]
    monkeypatch.setattr(app.state.services.registry, "predict", original)
    assert client.post(f"/api/v1/batches/{batch['batch_id']}/resume").status_code == 202
    app.state.services.batches.claim(batch["batch_id"])
    completed = process_batch(app.state.services, batch["batch_id"])
    assert completed["status"] == "completed"
    assert completed["results"][0]["prediction_id"] == first_prediction_id
    pinned = create(client, key="batch-pinned-model")
    upload(client, pinned)
    app.state.services.catalog.metadata["source"] = "changed_reference"
    changed = run(client, pinned)
    assert changed["status"] == "failed"
    assert "identity changed" in changed["results"][0]["error_message"]


def test_cancel_reselect_recovery_and_no_arbitrary_server_path(client):
    batch = create(client)
    cancelled = client.post(f"/api/v1/batches/{batch['batch_id']}/cancel").json()
    assert cancelled["status"] == "cancelled"
    resumed = client.post(f"/api/v1/batches/{batch['batch_id']}/resume").json()
    assert resumed["status"] == "draft"
    upload(client, batch)
    app.state.services.batches.start(batch["batch_id"])
    claimed = app.state.services.batches.claim(batch["batch_id"])
    file_id = claimed["files"][0]["file_id"]
    app.state.services.batches.result(batch["batch_id"], file_id, "builtin-nnls", status="running")
    restarted = build_services(app.state.services.settings)
    restarted.batches.recover_interrupted()
    failed = restarted.batches.get(batch["batch_id"])
    assert failed["status"] == "failed" and failed["results"][0]["status"] == "failed"
    for name in ("../secret.csv", "/tmp/secret.csv", "C:\\secret.csv"):
        response = client.post(
            "/api/v1/batches",
            json={
                "model_ids": ["builtin-nnls"],
                "expected_files": [descriptor(name)],
                "idempotency_key": "unsafe-name-request",
            },
        )
        assert response.status_code == 422
    assert client.post("/api/v1/batches", json={"directory": "/tmp"}).status_code == 422


def test_separate_batch_worker_leaves_research_jobs_queued(client):
    batch = create(client)
    upload(client, batch)
    services = app.state.services
    services.batches.start(batch["batch_id"])
    job = services.jobs.create(
        {"seeds": [11], "config_name": "demo", "idempotency_key": "research-job-waits"}
    )
    worker = subprocess.run(
        [sys.executable, "-m", "app.worker", "--batches-only", "--once"],
        env=worker_environment(services.settings),
        capture_output=True,
        timeout=60,
    )
    assert worker.returncode == 0, worker.stderr.decode()
    assert services.batches.get(batch["batch_id"])["status"] == "completed"
    assert services.jobs.get(job["job_id"])["status"] == "queued"
    assert client.get("/api/v1/health").status_code == 200


def test_local_folder_cli_writes_report_and_repeated_run_is_idempotent(client, tmp_path):
    folder = tmp_path / "measurements"
    folder.mkdir()
    (folder / "sample.csv").write_bytes(CSV)
    settings = app.state.services.settings
    env = worker_environment(settings)
    report = tmp_path / "report.zip"
    command = [sys.executable, "-m", "app.batch", str(folder), "--output", str(report)]
    first = subprocess.run(command, env=env, capture_output=True, timeout=60)
    assert first.returncode == 0, first.stderr.decode()
    assert report.is_file()
    stored = app.state.services.batches.list()
    assert len(stored) == 1 and stored[0]["status"] == "completed"
    prediction_id = stored[0]["results"][0]["prediction_id"]
    second = subprocess.run(
        [*command[:-1], str(tmp_path / "again.json")], env=env, capture_output=True, timeout=60
    )
    assert second.returncode == 0, second.stderr.decode()
    assert len(app.state.services.batches.list()) == 1
    assert app.state.services.batches.list()[0]["results"][0]["prediction_id"] == prediction_id


def test_cli_resumes_orphaned_running_batch_and_rejects_changed_intake(client, tmp_path):
    batch = create(client)
    upload(client, batch)
    services = app.state.services
    services.batches.start(batch["batch_id"])
    services.batches.claim(batch["batch_id"])
    services.batches.result(
        batch["batch_id"], batch["files"][0]["file_id"], "builtin-nnls", status="running"
    )
    resumed = subprocess.run(
        [sys.executable, "-m", "app.batch", "--resume", batch["batch_id"]],
        env=worker_environment(services.settings),
        capture_output=True,
        timeout=30,
    )
    assert resumed.returncode == 0, resumed.stderr.decode()
    assert services.batches.get(batch["batch_id"])["status"] == "completed"
    pending = create(client, [descriptor("sample.csv")], key="changed-intake-cli")
    folder = tmp_path / "changed"
    folder.mkdir()
    (folder / "sample.csv").write_bytes(b"x" * (services.settings.max_upload_bytes + 1))
    changed = subprocess.run(
        [
            sys.executable,
            "-m",
            "app.batch",
            str(folder),
            "--resume",
            pending["batch_id"],
            "--enqueue",
        ],
        env=worker_environment(services.settings),
        capture_output=True,
        timeout=30,
    )
    assert changed.returncode == 2
    assert b"differs from the saved batch" in changed.stderr
    assert services.batches.get(pending["batch_id"])["files"][0]["status"] == "pending_upload"


def test_oversized_csv_field_is_durable_rejection_and_cache_quota_is_optional(client):
    malformed = b"wavelength_nm,intensity\n" + b"9" * 150_000 + b",1\n800,2\n"
    batch = create(client, [descriptor("large-field.csv", malformed), descriptor()])
    rejected = upload(client, batch, "large-field.csv", malformed)
    assert rejected["status"] == "invalid" and rejected["error_code"] == "invalid_csv"
    upload(client, batch)
    app.state.services.settings.max_batch_cache_bytes = 1
    completed = run(client, batch)
    assert completed["status"] == "partial"
    assert completed["results"][1]["status"] == "completed"
    assert not list((app.state.services.batches.directory / "cache").glob("*.json"))


def test_backup_restores_batch_manifest_raw_inputs_and_interrupted_status(client, tmp_path):
    batch = create(client)
    file = upload(client, batch)
    services = app.state.services
    services.batches.start(batch["batch_id"])
    services.batches.claim(batch["batch_id"])
    services.batches.result(batch["batch_id"], file["file_id"], "builtin-nnls", status="running")
    archive = tmp_path / "batch-backup.zip"
    backup(services.settings, archive)
    restored_settings = Settings(
        artifacts_dir=tmp_path / "restored", reports_dir=tmp_path / "restored-reports"
    )
    assert restore(restored_settings, archive) == 3
    restored = build_services(restored_settings)
    record = restored.batches.get(batch["batch_id"])
    assert record["status"] == "failed" and record["error_code"] == "restored_interrupted_batch"
    assert record["progress"] == {"completed": 1, "failed": 1, "total": 1}
    assert restored.batches.input_bytes(record["files"][0]) == CSV
    restored.batches.start(batch["batch_id"], resume=True)
    restored.batches.claim(batch["batch_id"])
    assert process_batch(restored, batch["batch_id"])["status"] == "completed"
