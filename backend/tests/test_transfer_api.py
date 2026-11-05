from __future__ import annotations

import numpy as np
import pytest
from app.main import app
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


def test_list_transfer_profiles(client):
    """Test retrieving list of available calibration transfer profiles."""
    response = client.get("/api/v1/transfer/profiles")
    assert response.status_code == 200, response.text
    data = response.json()
    assert "profiles" in data
    profile_ids = [p["profile_id"] for p in data["profiles"]]
    assert "identity" in profile_ids
    assert "chemcam_pds_v1" in profile_ids
    assert "wavelength_alignment_v1" in profile_ids
    assert "combined_pds_alignment_v1" in profile_ids


def test_get_single_transfer_profile(client):
    """Test retrieving details of a single profile and 404 for nonexistent."""
    resp = client.get("/api/v1/transfer/profiles/chemcam_pds_v1")
    assert resp.status_code == 200, resp.text
    p = resp.json()
    assert p["profile_id"] == "chemcam_pds_v1"
    assert p["method"] == "pds"
    assert p["source_instrument"] == "chemcam-lanl-testbed"

    resp_404 = client.get("/api/v1/transfer/profiles/nonexistent_profile_xyz")
    assert resp_404.status_code == 404


def test_apply_transfer_with_arrays_and_identity(client):
    """Test applying calibration transfer with raw arrays."""
    wave = np.linspace(300.0, 500.0, 50).tolist()
    flux = (np.ones(50) * 10.0).tolist()

    # Apply identity
    resp_id = client.post(
        "/api/v1/transfer/apply",
        json={
            "wavelength_nm": wave,
            "intensity": flux,
            "profile_id": "identity",
        },
    )
    assert resp_id.status_code == 200, resp_id.text
    data_id = resp_id.json()
    assert data_id["is_identity"] is True
    assert data_id["diagnostics"]["shift_detected_nm"] == 0.0
    assert np.allclose(data_id["intensity"], flux)

    # Apply ChemCam PDS
    resp_pds = client.post(
        "/api/v1/transfer/apply",
        json={
            "wavelength_nm": wave,
            "intensity": flux,
            "profile_id": "wavelength_alignment_v1",
        },
    )
    assert resp_pds.status_code == 200, resp_pds.text
    data_pds = resp_pds.json()
    assert "diagnostics" in data_pds
    assert data_pds["profile_id"] == "wavelength_alignment_v1"


def test_apply_transfer_with_stored_spectrum(client):
    """Test applying transfer referencing stored spectrum by ID."""
    gen_resp = client.post(
        "/api/v1/spectra/generate",
        json={
            "elements": ["Fe", "Mg"],
            "component_weights": [1.0, 0.5],
            "seed": 12,
        },
    )
    assert gen_resp.status_code == 200, gen_resp.text
    spectrum_id = gen_resp.json()["spectrum_id"]

    resp = client.post(
        "/api/v1/transfer/apply",
        json={
            "spectrum_id": spectrum_id,
            "profile_id": "chemcam_pds_v1",
        },
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["profile_id"] == "chemcam_pds_v1"
    assert len(data["intensity"]) > 0


def test_fit_and_apply_custom_transfer(client):
    """Test fitting a new custom transfer profile on synthetic standards."""
    grid = np.linspace(350.0, 450.0, 50).tolist()
    master = [[float(i * s) for i in range(50)] for s in range(1, 6)]
    slave = [[float(i * s * 0.9 + 0.1) for i in range(50)] for s in range(1, 6)]

    fit_resp = client.post(
        "/api/v1/transfer/fit",
        json={
            "transfer_id": "custom_unit_test_transfer",
            "name": "Custom Test Profile",
            "method": "pds",
            "master_wavelength_nm": grid,
            "master_spectra": master,
            "slave_wavelength_nm": grid,
            "slave_spectra": slave,
            "window_half_width": 2,
            "alpha": 1e-3,
            "source_instrument": "lab",
            "target_instrument": "portable",
        },
    )
    assert fit_resp.status_code == 200, fit_resp.text
    fit_data = fit_resp.json()
    assert fit_data["profile"]["profile_id"] == "custom_unit_test_transfer"

    # Now apply the newly registered profile
    test_flux = [10.0] * 50
    apply_resp = client.post(
        "/api/v1/transfer/apply",
        json={
            "wavelength_nm": grid,
            "intensity": test_flux,
            "profile_id": "custom_unit_test_transfer",
        },
    )
    assert apply_resp.status_code == 200, apply_resp.text
    apply_data = apply_resp.json()
    assert apply_data["profile_id"] == "custom_unit_test_transfer"


def test_transfer_validation_errors(client):
    """Verify validation errors on malformed payloads."""
    # Empty
    resp = client.post("/api/v1/transfer/apply", json={})
    assert resp.status_code == 422

    # Mismatched lengths
    resp = client.post(
        "/api/v1/transfer/apply",
        json={"wavelength_nm": [400.0, 401.0], "intensity": [1.0]},
    )
    assert resp.status_code == 422

    # Short array
    resp = client.post(
        "/api/v1/transfer/apply",
        json={"wavelength_nm": [400.0] * 5, "intensity": [1.0] * 5},
    )
    assert resp.status_code == 422

    # Nonexistent profile
    resp = client.post(
        "/api/v1/transfer/apply",
        json={
            "wavelength_nm": [400.0 + i for i in range(20)],
            "intensity": [10.0] * 20,
            "profile_id": "unknown_profile_9999",
        },
    )
    assert resp.status_code == 404
