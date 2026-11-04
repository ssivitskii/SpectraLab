from __future__ import annotations

import json
from pathlib import Path

import pytest
from app.main import app
from fastapi.testclient import TestClient

PROJECT_ROOT = Path(__file__).resolve().parents[2]
FIXTURES_DIR = PROJECT_ROOT / "data" / "fixtures"


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def demo_series_csv():
    path = FIXTURES_DIR / "shot_series_demo.csv"
    assert path.is_file(), f"Missing fixture {path}"
    return path.read_bytes()


@pytest.fixture
def demo_series_meta():
    path = FIXTURES_DIR / "shot_series_demo.metadata.json"
    assert path.is_file(), f"Missing fixture {path}"
    return path.read_text(encoding="utf-8")


def test_series_analyze_multipart_success(client, demo_series_csv, demo_series_meta):
    response = client.post(
        "/api/v1/series/analyze",
        data={
            "metadata": demo_series_meta,
            "discard_first_shots": "5",
            "min_snr": "3.0",
            "min_accepted_shots": "2",
            "outlier_mad_z": "6.0",
        },
        files={"file": ("shot_series.csv", demo_series_csv, "text/csv")},
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["status"] == "completed"
    assert data["shot_count"] == 4  # 9 total shots - 5 discarded = 4 evaluated
    assert data["accepted_shot_count"] >= 2
    assert data["aggregate"] is not None
    assert len(data["aggregate"]) == 11
    assert "aggregate.csv" in data["outputs"]
    assert "receipt.json" in data["outputs"] or data["series_id"]

    series_id = data["series_id"]

    # Verify retrieval by ID
    get_res = client.get(f"/api/v1/series/{series_id}")
    assert get_res.status_code == 200
    assert get_res.json()["series_id"] == series_id

    # Verify downloading aggregate.csv
    file_res = client.get(f"/api/v1/series/{series_id}/files/aggregate.csv")
    assert file_res.status_code == 200
    assert "wavelength_nm,intensity" in file_res.text


def test_series_analyze_json_success(client, demo_series_csv, demo_series_meta):
    csv_text = demo_series_csv.decode("utf-8")
    meta_dict = json.loads(demo_series_meta)

    response = client.post(
        "/api/v1/series/analyze",
        json={
            "csv_content": csv_text,
            "metadata": meta_dict,
            "discard_first_shots": 5,
            "min_snr": 3.0,
            "min_accepted_shots": 2,
        },
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["status"] == "completed"
    assert data["accepted_shot_count"] >= 2


def test_series_analyze_insufficient_shots(client, demo_series_csv, demo_series_meta):
    response = client.post(
        "/api/v1/series/analyze",
        data={
            "metadata": demo_series_meta,
            "discard_first_shots": "5",
            "min_accepted_shots": "100",  # Requires more accepted shots than available
        },
        files={"file": ("shot_series.csv", demo_series_csv, "text/csv")},
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["status"] == "insufficient_series"
    assert data["aggregate"] is None


def test_series_list_and_delete(client, demo_series_csv, demo_series_meta):
    # 1. Post a series
    response = client.post(
        "/api/v1/series/analyze",
        data={"metadata": demo_series_meta, "discard_first_shots": "5"},
        files={"file": ("shot_series.csv", demo_series_csv, "text/csv")},
    )
    assert response.status_code == 200
    series_id = response.json()["series_id"]

    # 2. List series
    list_res = client.get("/api/v1/series")
    assert list_res.status_code == 200
    list_data = list_res.json()
    assert list_data["total"] >= 1
    assert any(item["series_id"] == series_id for item in list_data["items"])

    # 3. Delete series
    del_res = client.delete(f"/api/v1/series/{series_id}")
    assert del_res.status_code == 200
    assert del_res.json()["success"] is True

    # 4. Verify 404 after delete
    get_res = client.get(f"/api/v1/series/{series_id}")
    assert get_res.status_code == 404


def test_series_validation_errors(client):
    # Empty CSV
    res = client.post(
        "/api/v1/series/analyze",
        files={"file": ("empty.csv", b"", "text/csv")},
    )
    assert res.status_code in (400, 422)

    # Negative discard_first_shots
    res = client.post(
        "/api/v1/series/analyze",
        data={"discard_first_shots": "-1"},
        files={"file": ("series.csv", b"dummy,data", "text/csv")},
    )
    assert res.status_code == 422
