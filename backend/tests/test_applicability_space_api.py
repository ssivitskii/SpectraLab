from __future__ import annotations

import numpy as np
import pytest
from app.main import app
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


def _generate_test_spectrum() -> tuple[list[float], list[float]]:
    wave = np.linspace(250.0, 650.0, 4000)
    flux = np.full_like(wave, 1.0)
    # Fe, Mg, Ca lines
    for wl in [373.593, 382.151, 285.296, 393.477, 422.792]:
        flux += 10.0 * np.exp(-0.5 * ((wave - wl) / 0.05) ** 2)
    return wave.tolist(), flux.tolist()


def test_applicability_space_api_success(client):
    wave, flux = _generate_test_spectrum()
    resp = client.post(
        "/api/v1/quantitative/applicability-space",
        json={
            "wavelength_nm": wave,
            "intensity": flux,
            "medium": "vacuum",
        },
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert "sample_pc1" in data
    assert "sample_pc2" in data
    assert "t_squared" in data
    assert "q_residual" in data
    assert "status" in data
    assert data["status"] in ("in_domain", "near_boundary", "out_of_domain")
    assert len(data["calibration_scores"]) >= 40
    assert len(data["ellipse_95"]) == 64
    assert len(data["ellipse_99"]) == 64
    assert len(data["top_contributions"]) > 0


def test_applicability_space_api_stored_spectrum(client):
    gen_resp = client.post(
        "/api/v1/spectra/generate",
        json={
            "elements": ["Fe", "Mg"],
            "component_weights": [1.0, 0.5],
            "seed": 42,
        },
    )
    assert gen_resp.status_code == 200, gen_resp.text
    spectrum_id = gen_resp.json()["spectrum_id"]

    resp = client.post(
        "/api/v1/quantitative/applicability-space",
        json={"spectrum_id": spectrum_id},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["t_squared"] >= 0.0
    assert len(data["variance_explained"]) == 2


def test_applicability_space_api_validation_errors(client):
    # Empty payload
    resp = client.post("/api/v1/quantitative/applicability-space", json={})
    assert resp.status_code == 422

    # Array length mismatch
    resp = client.post(
        "/api/v1/quantitative/applicability-space",
        json={"wavelength_nm": [300.0, 400.0], "intensity": [1.0]},
    )
    assert resp.status_code == 422

    # Invalid spectrum_id
    resp = client.post(
        "/api/v1/quantitative/applicability-space",
        json={"spectrum_id": "invalid_id_format"},
    )
    assert resp.status_code == 422

    # Nonexistent valid spectrum_id
    resp = client.post(
        "/api/v1/quantitative/applicability-space",
        json={"spectrum_id": "00000000000000000000000000000000"},
    )
    assert resp.status_code == 404
