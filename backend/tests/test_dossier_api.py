from __future__ import annotations

import numpy as np
import pytest
from app.main import app
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


def test_dossier_generate_and_downloads(client):
    """Test full cycle: generate dossier, fetch by ID, and download JSON, HTML, and CSV."""
    wave = np.linspace(250.0, 650.0, 5000)
    flux = np.full_like(wave, 5.0)
    # Add synthetic peaks
    flux[np.abs(wave - 373.593) < 0.05] += 50.0  # Fe I
    flux[np.abs(wave - 285.296) < 0.05] += 40.0  # Mg I
    flux[np.abs(wave - 393.477) < 0.05] += 60.0  # Ca II

    # 1. Generate dossier
    res = client.post(
        "/api/v1/dossier/generate",
        json={
            "wavelength_nm": wave.tolist(),
            "intensity": flux.tolist(),
            "transfer_profile_id": "identity",
            "plasma_element": "auto",
            "confidence_level": 0.95,
            "medium": "vacuum",
        },
    )
    assert res.status_code == 200
    data = res.json()
    assert "dossier_id" in data
    dossier_id = data["dossier_id"]
    assert data["point_count"] == 5000
    assert len(data["sha256"]) == 64
    assert "Fe" in data["explainability"]["detected_elements"]
    assert "Mg" in data["explainability"]["detected_elements"]
    assert len(data["quantitative"]["predictions"]) >= 6
    assert "markdown_report" in data
    assert "# Измерительное досье" in data["markdown_report"]

    # 2. Get dossier by ID
    get_res = client.get(f"/api/v1/dossier/{dossier_id}")
    assert get_res.status_code == 200
    assert get_res.json()["dossier_id"] == dossier_id

    # 3. Download as JSON
    json_res = client.get(f"/api/v1/dossier/{dossier_id}/download?format=json")
    assert json_res.status_code == 200
    assert "application/json" in json_res.headers["content-type"]
    assert f'filename="{dossier_id}.json"' in json_res.headers["content-disposition"]
    json_body = json_res.json()
    assert json_body["dossier_id"] == dossier_id

    # 4. Download as HTML
    html_res = client.get(f"/api/v1/dossier/{dossier_id}/download?format=html")
    assert html_res.status_code == 200
    assert "text/html" in html_res.headers["content-type"]
    assert f'filename="{dossier_id}.html"' in html_res.headers["content-disposition"]
    html_text = html_res.text
    assert "<!DOCTYPE html>" in html_text
    assert f"Измерительное досье — {dossier_id}" in html_text
    assert "Количественный анализ состава (Assay)" in html_text

    # 5. Download as CSV
    csv_res = client.get(f"/api/v1/dossier/{dossier_id}/download?format=csv")
    assert csv_res.status_code == 200
    assert "text/csv" in csv_res.headers["content-type"]
    assert f'filename="{dossier_id}_lines.csv"' in csv_res.headers["content-disposition"]
    csv_text = csv_res.text
    assert "element,ion_stage,line_name" in csv_text
    assert "Fe,1" in csv_text or "Mg,1" in csv_text


def test_dossier_not_found_and_validation(client):
    """Test 404 and 422 validations for dossier endpoints."""
    res_404 = client.get("/api/v1/dossier/dos_nonexistent")
    assert res_404.status_code == 404

    res_dl_404 = client.get("/api/v1/dossier/dos_nonexistent/download?format=json")
    assert res_dl_404.status_code == 404

    # Mismatched array lengths
    res_422 = client.post(
        "/api/v1/dossier/generate",
        json={"wavelength_nm": [300.0, 301.0], "intensity": [1.0]},
    )
    assert res_422.status_code == 422


def test_dossier_with_stored_spectrum(client):
    """Test generating dossier using a stored spectrum ID."""
    gen_res = client.post(
        "/api/v1/spectra/generate",
        json={
            "elements": ["Fe", "Mg", "Ca"],
            "component_weights": [1.0, 0.5, 0.3],
            "seed": 42,
        },
    )
    assert gen_res.status_code == 200
    spec_id = gen_res.json()["spectrum_id"]

    dos_res = client.post(
        "/api/v1/dossier/generate",
        json={"spectrum_id": spec_id, "confidence_level": 0.90},
    )
    assert dos_res.status_code == 200
    data = dos_res.json()
    assert data["spectrum_id"] == spec_id
    assert data["quantitative"]["confidence_level"] == 0.90
