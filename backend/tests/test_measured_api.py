from __future__ import annotations

import asyncio
import csv
import io
import json
import zipfile

import httpx
import numpy as np
import pytest
from app.core.config import Settings
from app.main import app
from app.services.container import build_services
from fastapi.testclient import TestClient
from spectralab_ml.data.wavelengths import vacuum_to_air
from spectralab_ml.inference.measured import predict_line_arrays
from spectralab_ml.models.line_adapter_v3 import BANDS, GRID


@pytest.fixture
def client(tmp_path):
    artifacts = tmp_path / "artifacts"
    reports = tmp_path / "reports"
    artifacts.mkdir(parents=True, exist_ok=True)
    reports.mkdir(parents=True, exist_ok=True)
    with TestClient(app) as client:
        app.state.services = build_services(Settings(artifacts_dir=artifacts, reports_dir=reports))
        yield client


def valid_metadata(medium: str = "vacuum", instrument_id: str = "chemcam-lanl-testbed"):
    return {
        "schema_version": 1,
        "instrument_id": instrument_id,
        "measurement_id": "meas-synthetic-01",
        "axis": {
            "units": "nm",
            "medium": medium,
            "source": "synthetic-test-axis",
            "source_sha256": "c" * 64,
        },
    }


def make_csv_content(wave: np.ndarray, signal: np.ndarray) -> str:
    stream = io.StringIO()
    writer = csv.writer(stream)
    writer.writerow(["wavelength_nm", "intensity"])
    writer.writerows(zip(wave, signal, strict=True))
    return stream.getvalue()


def test_list_and_get_instrument_profiles(client):
    res = client.get("/api/v1/instrument-profiles")
    assert res.status_code == 200
    profiles = res.json()
    assert isinstance(profiles, list)
    assert any(p["profile_id"] == "calcium-v4" for p in profiles)

    ca_profile = next(p for p in profiles if p["profile_id"] == "calcium-v4")
    assert ca_profile["artifact_type"] == "calcium_ensemble_v4"
    assert ca_profile["instrument_id"] == "chemcam-lanl-testbed"
    assert ca_profile["supported_elements"] == ["Na", "Mg", "Ca", "Fe"]
    assert ca_profile["unknown_elements"] == ["H", "He"]
    assert ca_profile["ready"] is True
    assert ca_profile["unavailable_reason"] is None
    assert ca_profile["qualification"] == "development_only_no_independent_blank_validation"
    assert ca_profile["final_test_status"] == "not_performed"

    # Direct get
    single_res = client.get("/api/v1/instrument-profiles/calcium-v4")
    assert single_res.status_code == 200
    assert single_res.json()["profile_id"] == "calcium-v4"

    # Not found
    not_found_res = client.get("/api/v1/instrument-profiles/unknown-profile")
    assert not_found_res.status_code == 404


def test_measured_prediction_agrees_with_direct_inference(client):
    observed = np.any(np.array([(a <= GRID) & (b >= GRID) for a, b in BANDS]), axis=0)
    wave = GRID[observed]
    signal = np.ones(len(wave))
    csv_text = make_csv_content(wave, signal)
    meta = valid_metadata(medium="vacuum")

    # Call HTTP endpoint via JSON
    res = client.post(
        "/api/v1/measured-predictions",
        json={"profile_id": "calcium-v4", "csv_content": csv_text, "metadata": meta},
    )
    assert res.status_code == 200, res.text
    api_result = res.json()

    # Call direct ML implementation
    services = app.state.services
    model, profile, _ = services.instrument_profiles.resolve_profile("calcium-v4")
    direct_result = predict_line_arrays(model, profile, wave, signal, meta)

    # Compare predictions
    assert api_result["predictions"] == direct_result["predictions"]
    assert api_result["profile_identity"] == direct_result["profile_identity"]
    assert api_result["instrument_id"] == direct_result["instrument_id"]
    assert api_result["measurement_id"] == direct_result["measurement_id"]
    assert api_result["qualification"] == direct_result["qualification"]
    assert api_result["final_test_status"] == direct_result["final_test_status"]

    # Check unknown and unsupported elements
    by_elem = {p["element"]: p for p in api_result["predictions"]}
    assert by_elem["H"]["status"] == "unknown"
    assert by_elem["H"]["detected"] is None
    assert by_elem["H"]["score"] is None
    assert by_elem["H"]["reason"] == "unsupported_by_profile"

    assert by_elem["He"]["status"] == "unknown"
    assert by_elem["He"]["detected"] is None

    # Check evaluated elements have valid boolean detection or refusal
    for elem in ["Na", "Mg", "Ca", "Fe"]:
        assert by_elem[elem]["status"] in ("evaluated", "unknown")
        if by_elem[elem]["accepted"]:
            assert isinstance(by_elem[elem]["detected"], bool)
            assert isinstance(by_elem[elem]["score"], float)
        else:
            assert by_elem[elem]["detected"] is None


def test_measured_prediction_air_conversion(client):
    observed = np.any(np.array([(a <= GRID) & (b >= GRID) for a, b in BANDS]), axis=0)
    wave_vac = GRID[observed]
    wave_air = vacuum_to_air(wave_vac)
    signal = np.ones(len(wave_vac))

    vac_csv = make_csv_content(wave_vac, signal)
    air_csv = make_csv_content(wave_air, signal)

    vac_res = client.post(
        "/api/v1/measured-predictions",
        json={
            "profile_id": "calcium-v4",
            "csv_content": vac_csv,
            "metadata": valid_metadata(medium="vacuum"),
        },
    )
    air_res = client.post(
        "/api/v1/measured-predictions",
        json={
            "profile_id": "calcium-v4",
            "csv_content": air_csv,
            "metadata": valid_metadata(medium="air"),
        },
    )

    assert vac_res.status_code == 200
    assert air_res.status_code == 200

    vac_data = vac_res.json()
    air_data = air_res.json()

    assert air_data["wavelength_conversion"] is not None
    assert air_data["wavelength_conversion"]["from"] == "air"
    assert air_data["wavelength_conversion"]["to"] == "vacuum"
    assert vac_data["wavelength_conversion"] is None

    # Scores and decisions match after air->vacuum conversion
    assert air_data["predictions"] == vac_data["predictions"]


def test_measured_prediction_multipart_upload(client):
    observed = np.any(np.array([(a <= GRID) & (b >= GRID) for a, b in BANDS]), axis=0)
    wave = GRID[observed]
    signal = np.ones(len(wave))
    csv_bytes = make_csv_content(wave, signal).encode("utf-8")
    meta = valid_metadata(medium="vacuum")

    res = client.post(
        "/api/v1/measured-predictions",
        data={"profile_id": "calcium-v4", "metadata": json.dumps(meta)},
        files={"file": ("test.csv", csv_bytes, "text/csv")},
    )
    assert res.status_code == 200
    data = res.json()
    assert "prediction_id" in data
    assert data["profile_id"] == "calcium-v4"


def test_measured_prediction_rejections(client):
    observed = np.any(np.array([(a <= GRID) & (b >= GRID) for a, b in BANDS]), axis=0)
    wave = GRID[observed]
    signal = np.ones(len(wave))
    csv_text = make_csv_content(wave, signal)

    # 1. Incompatible instrument
    bad_inst_meta = valid_metadata(instrument_id="incompatible-spectrometer")
    res = client.post(
        "/api/v1/measured-predictions",
        json={"profile_id": "calcium-v4", "csv_content": csv_text, "metadata": bad_inst_meta},
    )
    assert res.status_code == 422
    assert "instrument" in res.text

    # 2. Incompatible axis medium
    bad_axis_meta = valid_metadata()
    bad_axis_meta["axis"]["medium"] = "unknown_medium"
    res = client.post(
        "/api/v1/measured-predictions",
        json={"profile_id": "calcium-v4", "csv_content": csv_text, "metadata": bad_axis_meta},
    )
    assert res.status_code == 422

    # 3. Truth labels passed in metadata (must be rejected)
    cheat_meta = valid_metadata()
    cheat_meta["true_labels"] = {"Ca": 1}
    res = client.post(
        "/api/v1/measured-predictions",
        json={"profile_id": "calcium-v4", "csv_content": csv_text, "metadata": cheat_meta},
    )
    assert res.status_code == 422

    # 4. Incomplete coverage / internal gap > 0.5 nm
    gap_wave = wave[(wave < 500) | (wave > 502)]
    gap_csv = make_csv_content(gap_wave, np.ones(len(gap_wave)))
    res = client.post(
        "/api/v1/measured-predictions",
        json={"profile_id": "calcium-v4", "csv_content": gap_csv, "metadata": valid_metadata()},
    )
    assert res.status_code == 422

    # 5. Invalid CSV header
    bad_csv = "wavelength,intensity\n400,1\n500,2\n"
    res = client.post(
        "/api/v1/measured-predictions",
        json={"profile_id": "calcium-v4", "csv_content": bad_csv, "metadata": valid_metadata()},
    )
    assert res.status_code == 422

    # 6. Checksum mismatch
    sha_meta = valid_metadata()
    sha_meta["input_sha256"] = "f" * 64
    res = client.post(
        "/api/v1/measured-predictions",
        json={"profile_id": "calcium-v4", "csv_content": csv_text, "metadata": sha_meta},
    )
    assert res.status_code == 422


def test_measured_prediction_get_and_export(client):
    observed = np.any(np.array([(a <= GRID) & (b >= GRID) for a, b in BANDS]), axis=0)
    wave = GRID[observed]
    signal = np.ones(len(wave))
    csv_text = make_csv_content(wave, signal)

    create_res = client.post(
        "/api/v1/measured-predictions",
        json={"profile_id": "calcium-v4", "csv_content": csv_text, "metadata": valid_metadata()},
    )
    assert create_res.status_code == 200
    pred_id = create_res.json()["prediction_id"]

    # 1. GET by ID
    get_res = client.get(f"/api/v1/measured-predictions/{pred_id}")
    assert get_res.status_code == 200
    assert get_res.json() == create_res.json()

    # 2. Export JSON
    export_json = client.get(f"/api/v1/measured-predictions/{pred_id}/export?format=json")
    assert export_json.status_code == 200
    payload = export_json.json()
    assert "manifest" in payload
    assert "record" in payload
    assert payload["manifest"]["kind"] == "measured_prediction"
    assert "X-Content-SHA256" in export_json.headers

    # 3. Export CSV
    export_csv = client.get(f"/api/v1/measured-predictions/{pred_id}/export?format=csv")
    assert export_csv.status_code == 200
    csv_lines = export_csv.text.splitlines()
    assert csv_lines[0].startswith("element,status,score,threshold")
    assert len(csv_lines) == 7  # 1 header + 6 elements

    # 4. Export ZIP
    export_zip = client.get(f"/api/v1/measured-predictions/{pred_id}/export?format=zip")
    assert export_zip.status_code == 200
    with zipfile.ZipFile(io.BytesIO(export_zip.content)) as archive:
        names = archive.namelist()
        assert "record.json" in names
        assert "summary.csv" in names
        assert "manifest.json" in names
        assert "input.csv" in names
        manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
        assert manifest["kind"] == "measured_prediction"


@pytest.mark.anyio
async def test_measured_prediction_does_not_block_event_loop(tmp_path, monkeypatch):
    import threading
    import time

    from app.api.routes import measured as measured_route

    artifacts = tmp_path / "artifacts"
    reports = tmp_path / "reports"
    artifacts.mkdir(parents=True, exist_ok=True)
    reports.mkdir(parents=True, exist_ok=True)
    app.state.services = build_services(Settings(artifacts_dir=artifacts, reports_dir=reports))

    observed = np.any(np.array([(a <= GRID) & (b >= GRID) for a, b in BANDS]), axis=0)
    wave = GRID[observed]
    signal = np.ones(len(wave))
    csv_text = make_csv_content(wave, signal)

    entered = threading.Event()
    release = threading.Event()
    original_predict = measured_route.predict_line_arrays

    def slow_predict(*args, **kwargs):
        entered.set()
        if not release.wait(timeout=2):
            raise RuntimeError("health request never released the blocked inference")
        return original_predict(*args, **kwargs)

    monkeypatch.setattr(measured_route, "predict_line_arrays", slow_predict)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as async_client:
        # Start the heavy inference request
        predict_task = asyncio.create_task(
            async_client.post(
                "/api/v1/measured-predictions",
                json={
                    "profile_id": "calcium-v4",
                    "csv_content": csv_text,
                    "metadata": valid_metadata(),
                },
            )
        )

        try:
            # Confirm inference started in worker thread before checking health
            while not entered.is_set():
                await asyncio.sleep(0.005)

            health_start = time.perf_counter()
            health_res = await async_client.get("/api/v1/health")
            health_duration = time.perf_counter() - health_start

            assert health_res.status_code == 200
            assert health_duration < 0.15
            assert not predict_task.done()
        finally:
            release.set()

        res = await predict_task
        assert res.status_code == 200
    assert entered.is_set()


def test_measured_prediction_openapi_request_body():
    schema = app.openapi()
    post_op = schema["paths"]["/api/v1/measured-predictions"]["post"]
    assert "requestBody" in post_op
    req_body = post_op["requestBody"]
    assert req_body.get("required") is True
    content = req_body.get("content", {})
    assert "multipart/form-data" in content
    assert "application/json" in content
    assert "$ref" in content["multipart/form-data"]["schema"]
    assert "$ref" in content["application/json"]["schema"]

    schemas = schema["components"]["schemas"]
    assert "MeasuredPredictionFormRequest" in schemas
    assert "MeasuredPredictionRequestJson" in schemas
    assert "MeasuredPredictionPoint" in schemas


def test_measured_prediction_flexible_inputs(client):
    observed = np.any(np.array([(a <= GRID) & (b >= GRID) for a, b in BANDS]), axis=0)
    wave = vacuum_to_air(GRID[observed])
    signal = np.ones(len(wave))
    csv_text = make_csv_content(wave, signal)
    air_meta = valid_metadata("air")

    # 1. Multipart with valid metadata
    form_res = client.post(
        "/api/v1/measured-predictions",
        files={"file": ("spectrum.csv", csv_text.encode("utf-8"), "text/csv")},
        data={"metadata": json.dumps(air_meta)},
    )
    assert form_res.status_code == 200, form_res.text
    data1 = form_res.json()
    assert data1["profile_id"] == "calcium-v4"
    assert data1["input_axis"]["medium"] == "air"

    # 2. JSON with points array and explicit metadata
    points = [
        {"wavelength_nm": float(w), "intensity": float(s)}
        for w, s in zip(wave, signal, strict=False)
    ]
    json_points_res = client.post(
        "/api/v1/measured-predictions",
        json={"points": points, "metadata": air_meta},
    )
    assert json_points_res.status_code == 200, json_points_res.text
    data2 = json_points_res.json()
    assert data2["profile_id"] == "calcium-v4"

    # 3. JSON with separate wavelength_nm and intensity arrays and explicit metadata
    json_arrays_res = client.post(
        "/api/v1/measured-predictions",
        json={
            "wavelength_nm": wave.tolist(),
            "intensity": signal.tolist(),
            "metadata": air_meta,
        },
    )
    assert json_arrays_res.status_code == 200, json_arrays_res.text
    data3 = json_arrays_res.json()
    assert data3["profile_id"] == "calcium-v4"

    # 4. JSON with csv_content and explicit metadata
    json_air_res = client.post(
        "/api/v1/measured-predictions",
        json={"csv_content": csv_text, "metadata": air_meta},
    )
    assert json_air_res.status_code == 200
    assert json_air_res.json()["input_axis"]["medium"] == "air"

    # 5. Missing instrument evidence / axis evidence is rejected with 422
    missing_res = client.post(
        "/api/v1/measured-predictions",
        files={"file": ("spectrum.csv", csv_text.encode("utf-8"), "text/csv")},
    )
    assert missing_res.status_code == 422

    # 6. Contradictory instrument_id is rejected with 422
    contra_res = client.post(
        "/api/v1/measured-predictions",
        json={
            "csv_content": csv_text,
            "instrument_id": "other-instrument",
            "metadata": air_meta,
        },
    )
    assert contra_res.status_code == 422

    # 7. Contradictory medium is rejected with 422
    contra_med_res = client.post(
        "/api/v1/measured-predictions",
        json={
            "csv_content": csv_text,
            "medium": "vacuum",
            "metadata": air_meta,  # air_meta has medium="air"
        },
    )
    assert contra_med_res.status_code == 422

    # 8. Multiple spectrum representations rejected with 422
    multi_rep_res = client.post(
        "/api/v1/measured-predictions",
        json={
            "csv_content": csv_text,
            "points": points,
            "metadata": air_meta,
        },
    )
    assert multi_rep_res.status_code == 422


def test_measured_prediction_sample_metadata_roundtrip(client, tmp_path):
    observed = np.any(np.array([(a <= GRID) & (b >= GRID) for a, b in BANDS]), axis=0)
    wave = vacuum_to_air(GRID[observed])
    signal = np.ones(len(wave))
    csv_text = make_csv_content(wave, signal)
    air_meta = valid_metadata("air")

    # 1. POST request with sample metadata
    create_res = client.post(
        "/api/v1/measured-predictions",
        json={
            "profile_id": "calcium-v4",
            "csv_content": csv_text,
            "physical_sample_id": "SAMPLE-42",
            "family_id": "FAM-99",
            "session_id": "SESS-01",
            "metadata": air_meta,
        },
    )
    assert create_res.status_code == 200, create_res.text
    pred = create_res.json()
    pred_id = pred["prediction_id"]

    assert pred["physical_sample_id"] == "SAMPLE-42"
    assert pred["family_id"] == "FAM-99"
    assert pred["session_id"] == "SESS-01"
    assert pred["measurement_metadata"] is not None
    assert pred["measurement_metadata"]["physical_sample_id"] == "SAMPLE-42"

    # 2. GET request reproduces sample metadata
    get_res = client.get(f"/api/v1/measured-predictions/{pred_id}")
    assert get_res.status_code == 200
    get_pred = get_res.json()
    assert get_pred["physical_sample_id"] == "SAMPLE-42"
    assert get_pred["family_id"] == "FAM-99"
    assert get_pred["session_id"] == "SESS-01"

    # 3. Export ZIP includes metadata in both prediction.json and record.json
    export_res = client.get(f"/api/v1/measured-predictions/{pred_id}/export?format=zip")
    assert export_res.status_code == 200
    with zipfile.ZipFile(io.BytesIO(export_res.content)) as archive:
        for json_name in ("prediction.json", "record.json"):
            archived = json.loads(archive.read(json_name).decode("utf-8"))
            assert archived["physical_sample_id"] == "SAMPLE-42"
            assert archived["family_id"] == "FAM-99"
            assert archived["session_id"] == "SESS-01"

    # 4. Backup and restore preserves metadata
    settings = app.state.services.settings
    backup_zip = tmp_path / "metadata_backup.zip"
    from app.maintenance import backup, restore

    backup(settings, backup_zip)
    assert backup_zip.exists()

    restored_settings = Settings(
        artifacts_dir=tmp_path / "restored_art",
        reports_dir=tmp_path / "restored_rep",
    )
    restore(restored_settings, backup_zip)

    from app.services.storage import MeasuredPredictionStorage

    restored_storage = MeasuredPredictionStorage(
        restored_settings.resolved_artifacts_dir() / "measured_predictions"
    )
    restored_pred = restored_storage.get(pred_id)
    assert restored_pred["physical_sample_id"] == "SAMPLE-42"
    assert restored_pred["family_id"] == "FAM-99"
    assert restored_pred["session_id"] == "SESS-01"


@pytest.mark.parametrize("bad_units", [None, "MISSING", "angstrom"])
def test_axis_units_contract_rejection(client, bad_units):
    observed = np.any(np.array([(a <= GRID) & (b >= GRID) for a, b in BANDS]), axis=0)
    wave = GRID[observed]
    csv_text = make_csv_content(wave, np.ones(len(wave)))

    meta = valid_metadata()
    if bad_units == "MISSING":
        del meta["axis"]["units"]
    else:
        meta["axis"]["units"] = bad_units

    res = client.post(
        "/api/v1/measured-predictions",
        json={"profile_id": "calcium-v4", "csv_content": csv_text, "metadata": meta},
    )
    assert res.status_code == 422


def test_multipart_contradictory_units(client):
    observed = np.any(np.array([(a <= GRID) & (b >= GRID) for a, b in BANDS]), axis=0)
    wave = GRID[observed]
    csv_text = make_csv_content(wave, np.ones(len(wave)))

    meta = valid_metadata()
    files = {"file": ("spectrum.csv", csv_text.encode("utf-8"), "text/csv")}
    data = {
        "metadata": json.dumps(meta),
        "units": "angstrom",
    }
    res = client.post("/api/v1/measured-predictions", files=files, data=data)
    assert res.status_code == 422


@pytest.mark.parametrize(
    ("field", "bad_value"),
    [
        ("measurement_id", {"nested": "object"}),
        ("physical_sample_id", {"nested": "object"}),
        ("family_id", {"nested": "object"}),
        ("session_id", {"nested": "object"}),
        ("physical_sample_id", "x" * 129),
        ("measurement_id", "m" * 129),
    ],
)
def test_nested_identifiers_contract_rejection(client, field, bad_value):
    observed = np.any(np.array([(a <= GRID) & (b >= GRID) for a, b in BANDS]), axis=0)
    wave = GRID[observed]
    csv_text = make_csv_content(wave, np.ones(len(wave)))

    meta = valid_metadata()
    meta[field] = bad_value

    res = client.post(
        "/api/v1/measured-predictions",
        json={"profile_id": "calcium-v4", "csv_content": csv_text, "metadata": meta},
    )
    assert res.status_code == 422
