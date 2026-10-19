from __future__ import annotations

import hashlib
import json

import numpy as np
import pytest
from spectralab_ml.data.measured_spectra import read_measured_csv, read_measurement_metadata
from spectralab_ml.data.reference import ReferenceCatalog, SpectralLine
from spectralab_ml.data.wavelengths import vacuum_to_air
from spectralab_ml.inference.measured import predict_measured_profile
from spectralab_ml.models.line_adapter_v2 import CLASSES, LineAdapterV2
from spectralab_ml.models.line_adapter_v3 import (
    BANDS,
    DECISION_POLICY,
    GRID,
    LineAdapterV3,
    feature_count,
    feature_plan,
)


def metadata():
    return {
        "schema_version": 1,
        "instrument_id": "fixture-only",
        "measurement_id": "fixture",
        "axis": {
            "units": "nm",
            "medium": "vacuum",
            "source": "synthetic-fixture",
            "source_sha256": "a" * 64,
        },
    }


def csv_file(path, wave, signal):
    np.savetxt(
        path,
        np.column_stack([wave, signal]),
        delimiter=",",
        header="wavelength_nm,intensity",
        comments="",
    )


@pytest.fixture
def profile(tmp_path):
    centers = [589.2, 285.3, 422.8, 404.7]
    lines = [
        SpectralLine(e, 1, w, "vacuum", "observed", 1.0, "1", "", "fixture", str(w), {})
        for e, w in zip(CLASSES, centers, strict=True)
    ]
    catalog = ReferenceCatalog(
        lines, {"content_sha256": "b" * 64, "wavelength_medium": "vacuum", "units": "nm"}
    )
    plan = feature_plan(catalog, "v2-line-baseline")
    model = LineAdapterV3()
    model.base = LineAdapterV2()
    model.base.C = 1.0
    model.base.states = {
        e: {
            "mean": [0.0] * feature_count(plan, e),
            "scale": [1.0] * feature_count(plan, e),
            "coef": [0.0] * feature_count(plan, e),
            "intercept": 0.0,
        }
        for e in CLASSES
    }
    model.calibration = {e: {"slope": 1.0, "intercept": 0.0} for e in CLASSES}
    path = tmp_path / "profile.json"
    model.save(
        path,
        plan=plan,
        thresholds=[0.4] * 4,
        selective={e: {"minimum_snr": 5 if e == "Fe" else 0, "margin": 0} for e in CLASSES},
        provenance={
            "instrument_id": "fixture-only",
            "scope": "nested_grouped_development_cv_not_independent_test",
            "decision_policy": DECISION_POLICY,
        },
    )
    return path


def test_measurement_retains_refusal_and_unsupported_elements(profile, tmp_path):
    observed = np.any(np.array([(a <= GRID) & (b >= GRID) for a, b in BANDS]), axis=0)
    path = tmp_path / "input.csv"
    csv_file(path, GRID[observed], np.ones(observed.sum()))
    meta = tmp_path / "metadata.json"
    meta.write_text(json.dumps(metadata()))
    result = predict_measured_profile(profile, path, meta)
    assert result["input"]["input_sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert result["final_test_status"] == "not_performed"
    assert result["calibration_scope"] == "grouped_development_only"
    by_element = {v["element"]: v for v in result["predictions"]}
    assert by_element["Na"]["detected"] is True
    assert by_element["Fe"]["detected"] is None
    assert by_element["Fe"]["accepted"] is False
    assert by_element["Fe"]["status"] == "unknown"
    assert by_element["H"]["score"] is None


def test_explicit_air_conversion_matches_vacuum_application(profile, tmp_path):
    vac = tmp_path / "vac.csv"
    air = tmp_path / "air.csv"
    csv_file(vac, GRID, np.ones(len(GRID)))
    csv_file(air, vacuum_to_air(GRID), np.ones(len(GRID)))
    m = tmp_path / "meta.json"
    value = metadata()
    m.write_text(json.dumps(value))
    a = predict_measured_profile(profile, vac, m)
    value["axis"]["medium"] = "air"
    m.write_text(json.dumps(value))
    b = predict_measured_profile(profile, air, m)
    assert a["predictions"] == b["predictions"]
    assert b["wavelength_conversion"]["to"] == "vacuum"


@pytest.mark.parametrize("case", ["instrument", "coverage", "internal_gap", "checksum"])
def test_measured_application_fails_on_incompatible_inputs(profile, tmp_path, case):
    wave = GRID.copy()
    if case == "coverage":
        wave = wave[(wave >= 452) & (wave <= 537)]
    if case == "internal_gap":
        wave = wave[(wave < 500) | (wave > 502)]
    path = tmp_path / "input.csv"
    csv_file(path, wave, np.ones(len(wave)))
    value = metadata()
    if case == "instrument":
        value["instrument_id"] = "another-instrument"
    if case == "checksum":
        value["input_sha256"] = "0" * 64
    m = tmp_path / "metadata.json"
    m.write_text(json.dumps(value))
    with pytest.raises(ValueError):
        predict_measured_profile(profile, path, m)


@pytest.mark.parametrize(
    "raw",
    [
        "wavelength_nm,intensity\n1,1\n1,2\n",
        "wavelength_nm,intensity\n1,1\n2,nan\n",
        "wavelength_nm,intensity\n1,1,0\n2,2,0\n",
        "wavelength_nm,intensity\n1,1\n2,1e999\n",
    ],
)
def test_measured_csv_never_drops_invalid_numeric_rows(tmp_path, raw):
    path = tmp_path / "invalid.csv"
    path.write_text(raw)
    with pytest.raises(ValueError):
        read_measured_csv(path)


def test_measurement_metadata_rejects_truth_and_undeclared_axis(tmp_path):
    path = tmp_path / "meta.json"
    value = metadata()
    value["labels"] = {"Fe": 1}
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError):
        read_measurement_metadata(path)
    value = metadata()
    del value["axis"]["medium"]
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError):
        read_measurement_metadata(path)


def test_final_adapter_binds_actual_thresholds_and_all_normalization_bands(profile, tmp_path):
    from spectralab_ml.inference.measured import bind_final_line_profile

    _, raw = LineAdapterV3.load(profile)
    contract = {
        "identity": raw["identity"],
        "instrument_id": "fixture-only",
        "class_order": CLASSES,
        "thresholds": dict.fromkeys(CLASSES, 0.4),
        "units": "nm",
        "medium": "vacuum",
        "required_intervals_nm": BANDS,
        "max_sampling_step_nm": 0.5,
        "require_positive_signal": True,
        "artifact_files": [
            {"path": profile.name, "sha256": hashlib.sha256(profile.read_bytes()).hexdigest()}
        ],
    }
    callback, identity = bind_final_line_profile(contract, tmp_path)
    assert identity["profile_identity"] == raw["identity"]
    scores = callback(
        {"instrument_id": "fixture-only", "axis": {"medium": "vacuum"}}, GRID, np.ones(len(GRID))
    )
    assert scores == dict.fromkeys(CLASSES, 0.5)
    contract["thresholds"]["Fe"] = 0.9
    with pytest.raises(ValueError, match="actual line-v3"):
        bind_final_line_profile(contract, tmp_path)
    contract["thresholds"]["Fe"] = 0.4
    contract["required_intervals_nm"] = [[383.0, 468.0]]
    with pytest.raises(ValueError, match="bands"):
        bind_final_line_profile(contract, tmp_path)
