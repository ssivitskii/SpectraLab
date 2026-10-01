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
