from __future__ import annotations

import pytest
from app.main import app
from fastapi.testclient import TestClient
from spectralab_ml.config import repository_root
from spectralab_ml.data.plasma_reference import PlasmaReference
from spectralab_ml.simulation.diagnostics import get_default_plasma_reference
from spectralab_ml.simulation.plasma import (
    PlasmaConditions,
    PlasmaModel,
    PlasmaZone,
)


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture(scope="module")
def plasma_ref() -> PlasmaReference:
    return get_default_plasma_reference(repository_root())


def test_plasma_diagnostics_with_arrays_success(client, plasma_ref: PlasmaReference):
    """Test diagnostics endpoint with raw arrays of synthetic spectrum."""
    model = PlasmaModel(plasma_ref, 300.0, 670.0, fine_step_nm=0.02)
    cond = PlasmaConditions(
        zones=(PlasmaZone(temperature_k=10000.0, electron_density_cm3=8e16, weight=1.0),),
        heavy_density_cm3=8e16,
        path_length_cm=0.015,
        stark_fwhm_nm=0.03,
        hydrogen_stark_fwhm_nm=1.0,
        continuum_factor=0.01,
    )
    radiance = model.radiance({"Fe": 0.15, "H": 0.20, "Si": 0.25, "O": 0.40}, cond)
    grid = model.grid_nm

    response = client.post(
        "/api/v1/plasma/diagnostics",
        json={
            "wavelength_nm": grid.tolist(),
            "intensity": radiance.tolist(),
            "element": "Fe",
            "instrumental_fwhm_nm": 0.05,
            "medium": "vacuum",
        },
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert "summary" in data
    assert data["summary"]["diagnostics_ready"] is True
    assert data["summary"]["temperature_k"] > 7000.0
    assert data["boltzmann"] is not None
    assert data["boltzmann"]["status"] == "valid"
    assert len(data["boltzmann"]["points"]) >= 8
    assert data["stark"] is not None
    assert data["stark"]["status"] == "detected"
    assert data["mcwhirter"] is not None
    assert data["mcwhirter"]["criterion_satisfied"] is True


def test_plasma_diagnostics_with_stored_spectrum(client):
    """Test diagnostics endpoint referencing stored spectrum by ID."""
    # First generate a stored spectrum
    gen_resp = client.post(
        "/api/v1/spectra/generate",
        json={
            "elements": ["Fe", "Na"],
            "component_weights": [1.0, 0.5],
            "seed": 4,
        },
    )
    assert gen_resp.status_code == 200, gen_resp.text
    spec_data = gen_resp.json()
    spectrum_id = spec_data["spectrum_id"]

    # Call plasma diagnostics
    response = client.post(
        "/api/v1/plasma/diagnostics",
        json={
            "spectrum_id": spectrum_id,
            "element": "auto",
        },
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert "summary" in data
    assert "boltzmann" in data


def test_plasma_diagnostics_validation_errors(client):
    """Verify 422 / 404 responses on invalid inputs."""
    # Empty request
    resp_empty = client.post("/api/v1/plasma/diagnostics", json={})
    assert resp_empty.status_code == 422

    # Mismatched lengths
    resp_mismatch = client.post(
        "/api/v1/plasma/diagnostics",
        json={
            "wavelength_nm": [300.0, 301.0, 302.0],
            "intensity": [1.0, 2.0],
        },
    )
    assert resp_mismatch.status_code == 422

    # Too few points
    resp_few = client.post(
        "/api/v1/plasma/diagnostics",
        json={
            "wavelength_nm": [300.0, 301.0],
            "intensity": [1.0, 2.0],
        },
    )
    assert resp_few.status_code == 422

    # Non-existent spectrum ID
    resp_notfound = client.post(
        "/api/v1/plasma/diagnostics",
        json={"spectrum_id": "00000000000000000000000000000000"},
    )
    assert resp_notfound.status_code == 404
