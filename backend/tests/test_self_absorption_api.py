from __future__ import annotations

import numpy as np
import pytest
from app.main import app
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


def _generate_test_doublet_spectrum() -> tuple[list[float], list[float]]:
    # Generate spectrum covering Mg II (279.64/280.35), Ca II (393.48/396.96), Na I (589.16/589.76)
    wave = np.linspace(250.0, 650.0, 4000)
    flux = np.ones_like(wave) * 0.5  # flat baseline

    # Add Mg II doublet lines
    flux += 10.0 * np.exp(-0.5 * ((wave - 279.635) / 0.04) ** 2)
    flux += 6.0 * np.exp(-0.5 * ((wave - 280.353) / 0.04) ** 2)

    # Add Ca II doublet lines
    flux += 8.0 * np.exp(-0.5 * ((wave - 393.477) / 0.04) ** 2)
    flux += 5.0 * np.exp(-0.5 * ((wave - 396.959) / 0.04) ** 2)

    # Add Na I doublet lines
    flux += 12.0 * np.exp(-0.5 * ((wave - 589.158) / 0.04) ** 2)
    flux += 7.0 * np.exp(-0.5 * ((wave - 589.756) / 0.04) ** 2)

    return wave.tolist(), flux.tolist()


def test_self_absorption_api_success(client):
    wave, flux = _generate_test_doublet_spectrum()
    resp = client.post(
        "/api/v1/plasma/self-absorption",
        json={
            "wavelength_nm": wave,
            "intensity": flux,
            "medium": "vacuum",
        },
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert "doublets" in data
    assert len(data["doublets"]) == 3
    assert "mean_self_absorption_factor" in data
    assert 0.0 < data["mean_self_absorption_factor"] <= 1.0

    # Verify Mg II was detected
    mg = next(d for d in data["doublets"] if d["element"] == "Mg")
    assert mg["status"] in ("absorbed", "thin")
    assert mg["line1_i_obs"] > 0
    assert mg["line2_i_obs"] > 0
    assert mg["line1_i_corrected"] >= mg["line1_i_obs"]


def test_cf_libs_api_success(client):
    wave, flux = _generate_test_doublet_spectrum()
    resp = client.post(
        "/api/v1/plasma/cf-libs",
        json={
            "wavelength_nm": wave,
            "intensity": flux,
            "temperature_k": 9000.0,
            "medium": "vacuum",
        },
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert "elements" in data
    assert len(data["elements"]) > 0
    assert abs(sum(e["atomic_fraction"] for e in data["elements"]) - 1.0) < 1e-3
    assert abs(sum(e["weight_fraction_wt"] for e in data["elements"]) - 100.0) < 1e-2


def test_self_absorption_api_stored_spectrum(client):
    gen_resp = client.post(
        "/api/v1/spectra/generate",
        json={
            "elements": ["Fe", "Mg", "Ca"],
            "component_weights": [1.0, 0.5, 0.3],
            "seed": 42,
        },
    )
    assert gen_resp.status_code == 200, gen_resp.text
    spectrum_id = gen_resp.json()["spectrum_id"]

    resp = client.post(
        "/api/v1/plasma/self-absorption",
        json={"spectrum_id": spectrum_id},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["doublets"]) >= 1


def test_self_absorption_validation_errors(client):
    # Missing all data
    resp = client.post("/api/v1/plasma/self-absorption", json={})
    assert resp.status_code == 422

    # Length mismatch
    resp = client.post(
        "/api/v1/plasma/self-absorption",
        json={"wavelength_nm": [300.0, 400.0], "intensity": [1.0]},
    )
    assert resp.status_code == 422

    # Invalid spectrum_id format
    resp = client.post(
        "/api/v1/plasma/self-absorption",
        json={"spectrum_id": "invalid_id_format"},
    )
    assert resp.status_code == 422

    # Nonexistent valid spectrum_id
    resp = client.post(
        "/api/v1/plasma/self-absorption",
        json={"spectrum_id": "00000000000000000000000000000000"},
    )
    assert resp.status_code == 404
