from __future__ import annotations

import hashlib

import numpy as np
import pytest
from app.core.config import Settings
from app.main import app
from app.services.container import build_services
from fastapi.testclient import TestClient


def make_spectrum_csv() -> bytes:
    wave = np.linspace(350.0, 800.0, 2000)
    flux = np.full_like(wave, 2.0)
    flux[np.abs(wave - 373.593) < 0.2] += 25.0  # Fe I
    flux[np.abs(wave - 393.477) < 0.2] += 30.0  # Ca II
    flux[np.abs(wave - 589.158) < 0.2] += 20.0  # Na I
    lines = ["wavelength_nm,intensity"]
    for w, f in zip(wave, flux, strict=False):
        lines.append(f"{w:.3f},{f:.3f}")
    return "\n".join(lines).encode("utf-8")


@pytest.fixture
def client(tmp_path):
    with TestClient(app) as test_client:
        app.state.services = build_services(
            Settings(artifacts_dir=tmp_path / "artifacts", reports_dir=tmp_path / "reports")
        )
        yield test_client


def test_batch_assay_matrix_api(client):
    raw_csv = make_spectrum_csv()
    sha = hashlib.sha256(raw_csv).hexdigest()
    file_desc = {
        "relative_path": "rock_samples/sample_01.csv",
        "sha256": sha,
        "size_bytes": len(raw_csv),
    }

    # 1. Create batch
    create_res = client.post(
        "/api/v1/batches",
        json={
            "name": "Geological Assay Batch",
            "model_ids": ["builtin-nnls"],
            "expected_files": [file_desc],
            "idempotency_key": "batch-assay-test-key-1",
            "measurement_metadata": {"sample_id": "GEO-101"},
        },
    )
    assert create_res.status_code == 201, create_res.text
    batch = create_res.json()
    batch_id = batch["batch_id"]

    # 2. Upload sample CSV
    upload_res = client.post(
        f"/api/v1/batches/{batch_id}/files",
        data={"relative_path": "rock_samples/sample_01.csv"},
        files={"file": ("sample_01.csv", raw_csv, "text/csv")},
    )
    assert upload_res.status_code == 200, upload_res.text

    # 3. Request batch assay matrix
    matrix_res = client.get(f"/api/v1/batches/{batch_id}/assay-matrix?confidence_level=0.95")
    assert matrix_res.status_code == 200, matrix_res.text
    data = matrix_res.json()

    assert data["batch_id"] == batch_id
    summary = data["summary"]
    assert summary["total_samples"] == 1
    assert "FeOT" in summary["mean_concentrations"]
    assert "MgO" in summary["mean_concentrations"]
    assert summary["confidence_level"] == 0.95

    assert len(data["samples"]) == 1
    sample = data["samples"][0]
    assert sample["relative_path"] == "rock_samples/sample_01.csv"
    assert sample["sample_id"] == "GEO-101"
    assert sample["applicability_status"] in ("in_domain", "near_boundary", "out_of_domain")
    assert "FeOT" in sample["predictions"]
    assert sample["predictions"]["FeOT"]["value_wt_percent"] >= 0.0
    assert "CaO" in sample["predictions"]
    assert sample["predictions"]["CaO"]["value_wt_percent"] >= 0.0

    # 4. Download batch assay CSV
    dl_res = client.get(f"/api/v1/batches/{batch_id}/assay-matrix/download?confidence_level=0.95")
    assert dl_res.status_code == 200
    assert "text/csv" in dl_res.headers["content-type"]
    assert f'filename="batch_{batch_id}_assay_matrix.csv"' in dl_res.headers["content-disposition"]
    csv_text = dl_res.text
    assert "file_id,relative_path,sample_id,applicability_status" in csv_text
    assert "FeOT_wt,FeOT_low,FeOT_high" in csv_text
    assert "sample_01.csv" in csv_text
