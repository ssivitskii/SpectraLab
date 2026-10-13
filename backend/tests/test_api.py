import json

import numpy as np
import pytest
from app.core.config import Settings
from app.main import app
from app.services.container import build_services
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path):
    with TestClient(app) as client:
        app.state.services = build_services(
            Settings(artifacts_dir=tmp_path / "artifacts", reports_dir=tmp_path / "reports")
        )
        yield client


def generate(client):
    response = client.post(
        "/api/v1/spectra/generate",
        json={"elements": ["H", "Na"], "component_weights": [1, 0.6], "seed": 4},
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_real_vertical_and_truth_isolation(client):
    generated = generate(client)
    request = {
        "input_type": "spectrum_id",
        "spectrum_id": generated["spectrum_id"],
        "model_id": "builtin-nnls",
    }
    first = client.post("/api/v1/predict", json=request)
    assert first.status_code == 200, first.text
    assert len(first.json()["scores"]) == 6 and not first.json()["calibrated"]
    path = app.state.services.spectra.directory / f"{generated['spectrum_id']}.json"
    generated["true_labels"] = {"Fe": 1}
    generated["parameters"] = {"elements": ["Fe"], "component_weights": [100]}
    path.write_text(json.dumps(generated))
    second = client.post("/api/v1/predict", json=request)
    assert first.json()["scores"] == second.json()["scores"]
    assert client.get(f"/api/v1/spectra/{generated['spectrum_id']}").status_code == 200


@pytest.mark.parametrize(
    "payload",
    [
        {"elements": ["H"], "component_weights": []},
        {"elements": ["H"], "component_weights": [1], "sampling_step_nm": 1e-10},
        {"elements": ["H"], "component_weights": [1], "snr_db": -10000},
        {"elements": ["H"], "component_weights": [1], "amplitude_variation": 1e308},
    ],
)
def test_generate_errors_are_json(client, payload):
    response = client.post("/api/v1/spectra/generate", json=payload)
    assert response.status_code == 422 and "error" in response.json()


def test_predict_contract_errors(client):
    base = {
        "input_type": "arrays",
        "wavelength_nm": [350, 800],
        "intensity": [1, 2],
        "units": "nm",
        "wavelength_medium": "vacuum",
    }
    for patch in [
        {"intensity": [1]},
        {"wavelength_nm": [800, 350]},
        {"intensity": [0, 0]},
        {"units": "angstrom"},
        {"wavelength_medium": "air"},
        {"spectrum_id": "a" * 32},
        {"true_labels": {"H": 1}},
        {"wavelength_nm": [350.001, 800]},
    ]:
        response = client.post("/api/v1/predict", json=base | patch)
        assert response.status_code == 422, response.text
    invalid = json.dumps(base | {"intensity": [1, float("nan")]})
    assert (
        client.post(
            "/api/v1/predict", content=invalid, headers={"Content-Type": "application/json"}
        ).status_code
        == 422
    )
    assert (
        client.post("/api/v1/predict", json=base | {"model_id": "logistic-ovr"}).status_code == 404
    )
    assert (
        client.post("/api/v1/predict", json=base | {"model_id": "../../secret"}).status_code == 422
    )


def test_upload_unknown_snr_and_rejection(client):
    file = "wavelength_nm,intensity\n350,1\n800,2\n"
    uploaded = client.post(
        "/api/v1/spectra/upload",
        data={"units": "nm", "wavelength_medium": "vacuum"},
        files={"file": ("input.csv", file, "text/csv")},
    )
    assert uploaded.status_code == 200
    assert uploaded.json()["parameters"]["snr_db"] is None
    assert uploaded.json()["parameters"]["snr_kind"] == "unknown"
    for body in [
        "wavelength_nm,intensity\n350,1,unexpected\n800,2\n",
        "wavelength_nm,intensity\n400,1\n700,2\n",
        "wavelength_nm,intensity,intensity\n350,1,2\n800,2,3\n",
    ]:
        response = client.post(
            "/api/v1/spectra/upload",
            data={"units": "nm", "wavelength_medium": "vacuum"},
            files={"file": ("bad.csv", body, "text/csv")},
        )
        assert response.status_code == 422, response.text


def test_limits_and_empty_experiments(client):
    assert client.get("/api/v1/experiments").json() == []
    assert client.get("/api/v1/health").json()["ready_models"] == ["builtin-nnls"]
    assert client.get("/bad-route").json()["error"]["code"] == "http_error"
    response = client.post(
        "/api/v1/predict", content=b" " * 6_000_001, headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 413
    app.state.services.spectra.max_files = 0
    response = client.post(
        "/api/v1/spectra/generate", json={"elements": ["H"], "component_weights": [1]}
    )
    assert response.status_code == 507


def test_nonzero_no_supported_elements(client):
    grid = np.linspace(350, 800, 9001)
    response = client.post(
        "/api/v1/predict",
        json={
            "input_type": "arrays",
            "wavelength_nm": grid.tolist(),
            "intensity": (-np.ones_like(grid)).tolist(),
            "units": "nm",
            "wavelength_medium": "vacuum",
        },
    )
    assert response.status_code == 200
    assert response.json()["detected_elements"] == []


def test_capabilities_metadata_history_and_delete(client):
    caps = client.get("/api/v1/capabilities").json()
    assert caps["spectrum"]["units"] == "nm"
    assert caps["spectrum"]["max_upload_bytes"] == app.state.services.settings.max_upload_bytes
    assert caps["deployment_mode"] == "local"
    first, second = generate(client), generate(client)
    metadata = {
        "sample_id": "sample-7",
        "instrument_id": "instrument-A",
        "session_id": "session-4",
        "calibration": "lamp before acquisition",
        "notes": "reference sample",
    }
    response = client.patch(f"/api/v1/spectra/{first['spectrum_id']}/metadata", json=metadata)
    assert response.status_code == 200, response.text
    assert response.json()["measurement_metadata"] == metadata
    from app.services.storage import SpectrumStorage

    restarted = SpectrumStorage(app.state.services.spectra.directory)
    assert restarted.get(first["spectrum_id"])["measurement_metadata"] == metadata
    first_page = client.get("/api/v1/spectra?limit=1").json()
    assert first_page["next_cursor"]
    second_page = client.get(
        "/api/v1/spectra", params={"limit": 1, "cursor": first_page["next_cursor"]}
    ).json()
    assert {first_page["items"][0]["spectrum_id"], second_page["items"][0]["spectrum_id"]} == {
        first["spectrum_id"],
        second["spectrum_id"],
    }
    assert client.delete(f"/api/v1/spectra/{first['spectrum_id']}").status_code == 204
    assert client.get(f"/api/v1/spectra/{second['spectrum_id']}").status_code == 200
    assert client.get(f"/api/v1/spectra/{first['spectrum_id']}").status_code == 404


def test_damaged_spectrum_does_not_break_history(client):
    spectrum = generate(client)
    directory = app.state.services.spectra.directory
    (directory / f"{'a' * 32}.json").write_text("{bad json")
    history = client.get("/api/v1/spectra")
    assert history.status_code == 200
    assert [item["spectrum_id"] for item in history.json()["items"]] == [spectrum["spectrum_id"]]
    assert client.get(f"/api/v1/spectra/{'a' * 32}").status_code == 422
    (directory / ".history-index").write_text("{broken index")
    assert (
        client.get("/api/v1/spectra").json()["items"][0]["spectrum_id"] == spectrum["spectrum_id"]
    )


def test_cursor_same_timestamps_insert_and_changed_filters(client):
    directory = app.state.services.experiments.directory

    def save(run_id, timestamp="2026-01-01T00:00:00+00:00", status="completed"):
        path = directory / run_id
        path.mkdir(parents=True)
        (path / "result.json").write_text(
            json.dumps(
                {
                    "run_id": run_id,
                    "created_at": timestamp,
                    "status": status,
                    "reference_source": "demo_fixture",
                    "protocol": "controlled_sweep",
                    "demo": True,
                    "configuration": {},
                    "seed": 42,
                    "runs": [],
                    "limitations": "synthetic",
                }
            )
        )

    for run_id in ["a", "b", "c", "d"]:
        save(run_id)
    first = client.get(
        "/api/v1/experiments/catalog", params={"limit": 2, "status": "completed"}
    ).json()
    assert [item["run_id"] for item in first["items"]] == ["d", "c"]
    save("new", "2026-02-01T00:00:00+00:00")
    second = client.get(
        "/api/v1/experiments/catalog",
        params={"limit": 2, "status": "completed", "cursor": first["next_cursor"]},
    ).json()
    assert [item["run_id"] for item in second["items"]] == ["b", "a"]
    assert second["next_cursor"] is None
    changed = client.get(
        "/api/v1/experiments/catalog", params={"status": "failed", "cursor": first["next_cursor"]}
    )
    assert changed.status_code == 422
    refreshed = client.get("/api/v1/experiments/catalog").json()
    assert refreshed["items"][0]["run_id"] == "new"
    for status in ("running", "failed"):
        save(status, status=status)
        assert client.get(f"/api/v1/experiments/{status}").json()["status"] == status


def test_series_contract_partial_aggregates(client):
    import io
    import zipfile

    directory = app.state.services.experiments.directory / "series-test"
    directory.mkdir(parents=True)
    record = {
        "run_id": "series-test",
        "artifact_type": "experiment_series",
        "status": "incomplete",
        "created_at": "2026-01-01T00:00:00+00:00",
        "reference_source": "demo_fixture",
        "protocol": "controlled_sweep",
        "configuration": {},
        "expected_seeds": [1, 2, 3],
        "successful_seeds": [1],
        "seed_runs": [{"seed": 1, "run_id": "one", "status": "completed"}],
        "failed_runs": [{"seed": 2, "run_id": "two", "error": "training failed"}],
        "incomplete_runs": [{"seed": 3, "run_id": None, "error": "missing run"}],
        "aggregates": [
            {
                "model_id": "baseline-nnls",
                "factor": "snr_db",
                "value": 20,
                "metrics": {
                    "micro_f1": {"mean": 0.8, "std": None, "count": 1},
                    "single": {"count": {"mean": 4, "std": None, "count": 1}},
                },
            }
        ],
    }
    (directory / "result.json").write_text(json.dumps(record))
    response = client.get("/api/v1/experiments/series-test")
    assert response.status_code == 200, response.text
    assert response.json()["aggregates"][0]["metrics"]["micro_f1"]["std"] is None
    assert response.json()["failed_runs"][0]["error"] == "training failed"
    child = directory / "runs" / "one"
    child.mkdir(parents=True)
    (child / "result.json").write_text(
        json.dumps(
            {
                "run_id": "one",
                "status": "completed",
                "created_at": record["created_at"],
                "reference_source": "demo_fixture",
                "protocol": "controlled_sweep",
                "configuration": {},
                "seed": 1,
                "runs": [],
                "limitations": "synthetic",
            }
        )
    )
    assert client.get("/api/v1/experiments/one").status_code == 200
    exported = client.get("/api/v1/experiments/series-test/export")
    with zipfile.ZipFile(io.BytesIO(exported.content)) as archive:
        assert "runs/one/record.json" in archive.namelist()
        assert json.loads(archive.read("runs/one/record.json"))["run_id"] == "one"


def test_reproducible_export_checksums_and_path_redaction(client):
    import hashlib
    import io
    import zipfile

    generated = generate(client)
    path = app.state.services.spectra.directory / f"{generated['spectrum_id']}.json"
    record = json.loads(path.read_text())
    record["reference_metadata"] = {
        "source_url": "https://example.org/reference",
        "cache_path": "/Users/private/secret.json",
    }
    path.write_text(json.dumps(record))
    prediction = client.post(
        "/api/v1/predict",
        json={"input_type": "spectrum_id", "spectrum_id": generated["spectrum_id"]},
    ).json()
    response = client.get(f"/api/v1/predictions/{prediction['prediction_id']}/export")
    assert response.status_code == 200
    assert response.headers["x-content-sha256"] == hashlib.sha256(response.content).hexdigest()
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        assert set(archive.namelist()) == {"manifest.json", "record.json", "spectrum.csv"}
        for name, entry in manifest["files"].items():
            assert hashlib.sha256(archive.read(name)).hexdigest() == entry["sha256"]
        exported = json.loads(archive.read("record.json"))
        assert exported["scores"] == prediction["scores"]
        assert exported["input"]["wavelength_nm"] == generated["wavelength_nm"]
        assert "/Users/" not in archive.read("record.json").decode()
        assert (
            exported["input"]["reference_metadata"]["source_url"] == "https://example.org/reference"
        )
    csv_response = client.get(f"/api/v1/spectra/{generated['spectrum_id']}/export?format=csv")
    assert csv_response.text.splitlines()[0] == "wavelength_nm,intensity"
    assert client.get("/api/v1/predictions/not-an-id/export").status_code == 422


def test_jobs_idempotency_cancellation_and_recovery(client):
    request = {"seeds": [11, 22], "config_name": "demo", "idempotency_key": "request-one"}
    first = client.post("/api/v1/jobs", json=request)
    assert first.status_code == 202, first.text
    assert first.json()["status"] == "queued"
    again = client.post("/api/v1/jobs", json=request)
    assert again.json()["job_id"] == first.json()["job_id"]
    assert client.post("/api/v1/jobs", json={**request, "seeds": [33]}).status_code == 409
    cancelled = client.post(f"/api/v1/jobs/{first.json()['job_id']}/cancel").json()
    assert cancelled["status"] == "cancelled"
    second = client.post("/api/v1/jobs", json={**request, "idempotency_key": "request-two"}).json()
    jobs = app.state.services.jobs
    assert jobs.claim_next()["job_id"] == second["job_id"]
    from app.services.jobs import JobStorage

    restarted = JobStorage(jobs.directory)
    restarted.recover_interrupted()
    assert restarted.get(second["job_id"])["status"] == "failed"
    assert restarted.get(second["job_id"])["error_code"] == "worker_interrupted"
    assert client.get("/api/v1/health").status_code == 200
    assert client.get("/api/v1/jobs").json()[0]["status"] == "failed"


def test_request_ids(client):
    response = client.get("/api/v1/health", headers={"X-Request-ID": "trace-123"})
    assert response.headers["x-request-id"] == "trace-123"
    error = client.get("/api/v1/spectra/not-valid", headers={"X-Request-ID": "invalid id"})
    assert error.status_code == 422
    assert len(error.headers["x-request-id"]) == 32


def test_configured_reference_used_consistently(client, tmp_path):
    import hashlib

    from spectralab_ml.data.reference import ReferenceCatalog, load_demo_catalog

    original = load_demo_catalog()
    catalog = ReferenceCatalog(original.lines, {**original.metadata, "source": "test_fixture_copy"})
    cache_path = tmp_path / "reference.json"
    catalog.to_cache(cache_path)
    configured = Settings(
        artifacts_dir=tmp_path / "selected-artifacts",
        reports_dir=tmp_path / "selected-reports",
        reference_cache=cache_path,
        reference_sha256=hashlib.sha256(cache_path.read_bytes()).hexdigest(),
    )
    app.state.services = build_services(configured)
    assert client.get("/api/v1/elements").json()["reference"]["source"] == "test_fixture_copy"
    generated = generate(client)
    assert generated["reference_source"] == "test_fixture_copy"
    prediction = client.post(
        "/api/v1/predict",
        json={"input_type": "spectrum_id", "spectrum_id": generated["spectrum_id"]},
    )
    assert prediction.status_code == 200, prediction.text
    assert prediction.json()["reference_source"] == "test_fixture_copy"
    with pytest.raises(ValueError, match="checksum"):
        build_services(configured.model_copy(update={"reference_sha256": "0" * 64}))


def test_unavailable_model_diagnostics_do_not_expose_paths(client):
    model_directory = app.state.services.registry.artifacts_dir / "logistic-ovr"
    model_directory.mkdir(parents=True)
    models = client.get("/api/v1/models").json()
    unavailable = next(model for model in models if model["model_id"] == "logistic-ovr")
    assert unavailable["ready"] is False
    assert unavailable["diagnostic_code"] == "artifact_missing"
    result = client.post(
        "/api/v1/predict",
        json={
            "input_type": "arrays",
            "model_id": "logistic-ovr",
            "wavelength_nm": [350, 800],
            "intensity": [1, 2],
            "units": "nm",
            "wavelength_medium": "vacuum",
        },
    )
    assert result.status_code == 404
    assert result.json()["error"]["code"] == "model_unavailable"
    assert str(model_directory) not in result.text


def test_experiment_preserves_actual_ml_timing_contract(client):
    from spectralab_ml.benchmark import measure_inference
    from spectralab_ml.evaluation import evaluate_multilabel

    measured = measure_inference(lambda: None, 1, warmups=1, repeats=3)
    metrics = evaluate_multilabel(
        np.asarray([[1, 0], [0, 1]]),
        np.asarray([[0.9, 0.1], [0.1, 0.9]]),
        np.asarray([0.5, 0.5]),
        ("H", "Na"),
    )
    metrics.update(measured)
    record = {
        "run_id": "timing-contract",
        "status": "completed",
        "created_at": "2026-01-01T00:00:00Z",
        "reference_source": "demo_fixture",
        "protocol": "controlled_sweep",
        "configuration": {},
        "runs": [
            {
                "model_id": "baseline-nnls",
                "factor": "snr_db",
                "value": 20,
                "requested_value": 20,
                "test_base_sample_ids": ["one", "two"],
                "metrics": metrics,
            }
        ],
    }
    directory = app.state.services.experiments.directory / record["run_id"]
    directory.mkdir(parents=True)
    (directory / "result.json").write_text(json.dumps(record))
    response = client.get("/api/v1/experiments/timing-contract")
    assert response.status_code == 200, response.text
    returned = response.json()["runs"][0]["metrics"]
    assert returned["timing"] == measured["timing"]
    assert returned["inference_ms_per_spectrum"] == measured["timing"]["median"]
    schema = client.get("/openapi.json").json()["components"]["schemas"]["InferenceTiming"]
    assert {"repeats", "p95", "median", "observations"} <= set(schema["properties"])
