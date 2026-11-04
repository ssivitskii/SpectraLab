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


def test_quantitative_predict_arrays_success(client, plasma_ref: PlasmaReference):
    """Test quantitative prediction endpoint with raw synthetic spectrum arrays."""
    model = PlasmaModel(plasma_ref, 250.0, 600.0, fine_step_nm=0.03)
    cond = PlasmaConditions(
        zones=(PlasmaZone(temperature_k=10000.0, electron_density_cm3=5e16, weight=1.0),),
        heavy_density_cm3=5e16,
        path_length_cm=0.015,
        stark_fwhm_nm=0.02,
        continuum_factor=0.01,
    )
    # Fe 15%, Mg 10%, Ca 5%, Ti 3%, Si 30%, Al 15%, O 22%
    radiance = model.radiance(
        {"Fe": 0.15, "Mg": 0.10, "Ca": 0.05, "Ti": 0.03, "Si": 0.30, "Al": 0.15, "O": 0.22},
        cond,
    )
    grid = model.grid_nm

    response = client.post(
        "/api/v1/quantitative/predict",
        json={
            "wavelength_nm": grid.tolist(),
            "intensity": radiance.tolist(),
            "confidence_level": 0.95,
            "medium": "vacuum",
        },
    )
    assert response.status_code == 200, response.text
    data = response.json()

    assert "predictions" in data
    assert len(data["predictions"]) == 6
    assert data["model_id"] == "ridge-conformal-v1"
    assert data["confidence_level"] == 0.95
    assert data["total_detected_mass_wt_percent"] > 0.0
    assert "applicability_domain_summary" in data

    pred_map = {p["species"]: p for p in data["predictions"]}
    assert "FeOT" in pred_map
    fe = pred_map["FeOT"]
    assert fe["element"] == "Fe"
    assert fe["value_wt_percent"] > 0.0
    assert fe["lower_wt_percent"] <= fe["value_wt_percent"] <= fe["upper_wt_percent"]
    assert fe["status"] in ("in_domain", "near_boundary", "out_of_domain", "below_lod")
    assert fe["lod_wt_percent"] > 0.0


def test_quantitative_predict_stored_spectrum(client):
    """Test quantitative predict referencing stored spectrum by ID."""
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

    response = client.post(
        "/api/v1/quantitative/predict",
        json={
            "spectrum_id": spectrum_id,
            "confidence_level": 0.90,
        },
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert len(data["predictions"]) == 6
    assert data["confidence_level"] == 0.90


def test_quantitative_predict_custom_targets(client):
    """Test quantitative predict filtering specific target species."""
    gen_resp = client.post(
        "/api/v1/spectra/generate",
        json={
            "elements": ["Fe", "Mg"],
            "component_weights": [1.0, 0.8],
            "seed": 7,
        },
    )
    assert gen_resp.status_code == 200, gen_resp.text
    spectrum_id = gen_resp.json()["spectrum_id"]

    response = client.post(
        "/api/v1/quantitative/predict",
        json={
            "spectrum_id": spectrum_id,
            "targets": ["FeOT", "MgO"],
            "confidence_level": 0.99,
        },
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert len(data["predictions"]) == 2
    species_names = {p["species"] for p in data["predictions"]}
    assert species_names == {"FeOT", "MgO"}


def test_quantitative_predict_validation_errors(client):
    """Test error handling on invalid requests."""
    # Empty payload
    resp = client.post("/api/v1/quantitative/predict", json={})
    assert resp.status_code == 422

    # Mismatched array lengths
    resp = client.post(
        "/api/v1/quantitative/predict",
        json={"wavelength_nm": [400.0, 401.0], "intensity": [1.0]},
    )
    assert resp.status_code == 422

    # Too few points
    resp = client.post(
        "/api/v1/quantitative/predict",
        json={"wavelength_nm": [400.0] * 5, "intensity": [1.0] * 5},
    )
    assert resp.status_code == 422

    # Invalid spectrum_id format
    resp = client.post(
        "/api/v1/quantitative/predict",
        json={"spectrum_id": "invalid_id_format"},
    )
    assert resp.status_code == 422

    # Nonexistent valid spectrum_id
    resp = client.post(
        "/api/v1/quantitative/predict",
        json={"spectrum_id": "00000000000000000000000000000000"},
    )
    assert resp.status_code == 404
