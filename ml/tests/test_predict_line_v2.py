from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
from spectralab_ml.models.line_adapter_v2 import (
    BANDS,
    CLASSES,
    LineAdapterV2,
    digest,
    extract_line_features,
)

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/predict_line_v2.py"
SPEC = importlib.util.spec_from_file_location("predict_line_v2", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
prediction = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(prediction)


def write_ccs(path, wave, intensity):
    lines = ["# Synthetic fixture only", "# NSHOTS=6"]
    lines.append("# wave,shot1,shot2,shot3,shot4,shot5,shot6,median,mean")
    for wavelength, signal in zip(wave, intensity, strict=True):
        row = [wavelength, *([999] * 5), signal, -999, -999]
        lines.append(",".join(format(float(value), ".17g") for value in row))
    path.write_text("\n".join(lines) + "\n")


@pytest.fixture
def profile(tmp_path):
    grid = prediction.GRID_NM
    centers = [589.2, 285.3, 422.8, 404.7]
    plan = {
        "schema_version": 2,
        "class_order": CLASSES,
        "wavelength_medium": "vacuum",
        "bands": BANDS,
        "lines_nm": {element: [center] for element, center in zip(CLASSES, centers, strict=True)},
        "core_half_width_nm": 0.2,
        "sideband_inner_nm": 0.5,
        "sideband_outer_nm": 1.0,
        "baseline": "sideband lower quartile",
        "noise_floor": "1e-6 band positive p95",
        "features": prediction.FEATURES,
    }
    labels = np.array([[int(bool(i & (1 << k))) for k in range(4)] for i in range(16)])
    spectra = np.full((16, len(grid)), 2.0)
    for k, center in enumerate(centers):
        spectra += labels[:, k, None] * 10 * np.exp(-(((grid - center) / 0.1) ** 2))
    features = extract_line_features(spectra, grid, plan)
    model = LineAdapterV2().fit(features, labels, [str(i) for i in range(16)], C=0.1)
    expected = model.scores({key: values[-1:] for key, values in features.items()})[0]
    artifact = tmp_path / "profile.json"
    model.save(
        artifact,
        plan=plan,
        thresholds=[float(expected[0]), float(np.nextafter(1.0, np.inf)), 0.5, 0.5],
        provenance={
            "instrument_id": "synthetic-testbed",
            "estimand": "operational_above_threshold",
            "oxide_policy": {
                element: {
                    "oxide": oxide,
                    "units": "oxide_wt_percent",
                    "negative_max": 0.1,
                    "positive_min": 1.0,
                    "nominal_decision_level": 0.5,
                    "zero_policy": "unknown",
                }
                for element, oxide in zip(CLASSES, ("Na2O", "MgO", "CaO", "FeOT"), strict=True)
            },
        },
    )
    observed = np.any([(grid >= a) & (grid <= b) for a, b in BANDS], axis=0)
    source = tmp_path / "measurement.csv"
    write_ccs(source, grid[observed], spectra[-1, observed])
    return {
        "artifact": artifact,
        "input": source,
        "expected": expected,
        "wave": grid[observed],
        "intensity": spectra[-1, observed],
    }


def invoke(tmp_path, profile, *extra):
    output = tmp_path / "prediction.json"
    run = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--artifact",
            str(profile["artifact"]),
            "--input",
            str(profile["input"]),
            "--output",
            str(output),
            *extra,
        ],
        cwd=tmp_path,
        env={**os.environ, "PYTHONPATH": str(ROOT / "ml/src")},
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    return run, output


def rehash(path, value):
    value.pop("identity", None)
    value["identity"] = digest(value)
    path.write_text(json.dumps(value))


def test_cli_numeric_roundtrip_retains_shot6_and_research_status(tmp_path, profile):
    protected = {p: p.read_bytes() for p in (profile["artifact"], profile["input"])}
    run, output = invoke(tmp_path, profile, "--instrument-id", "synthetic-testbed")
    assert run.returncode == 0, run.stderr
    result = json.loads(output.read_text())
    assert result["input_sha256"] == hashlib.sha256(protected[profile["input"]]).hexdigest()
    assert result["profile_identity"] == json.loads(protected[profile["artifact"]])["identity"]
    assert result["score_kind"] == LineAdapterV2.score_kind
    assert result["calibrated"] is False
    assert result["qualification"] == prediction.QUALIFICATION
    assert result["final_test_status"] == "not_performed"
    assert result["oxide_policy"]["Mg"]["zero_policy"] == "unknown"
    assert result["aggregation"]["retained_shots"] == 1
    assert result["aggregation"]["discarded_shots"] == 5
    assert result["preprocessing"]["grid_points"] == 6601
    assert result["preprocessing"]["observed_intervals"] == BANDS
    decisions = {p["element"]: p for p in result["predictions"]}
    np.testing.assert_allclose([decisions[c]["score"] for c in CLASSES], profile["expected"])
    assert decisions["Na"]["score"] == decisions["Na"]["threshold"]
    assert decisions["Na"]["detected"] is True
    assert decisions["Mg"]["detected"] is False
    for element in ("H", "He"):
        assert decisions[element]["status"] == "unknown"
        assert decisions[element]["detected"] is None
        assert decisions[element]["score"] is None
    assert all(p.read_bytes() == value for p, value in protected.items())


@pytest.mark.parametrize("instrument", [None, "", "different-instrument"])
def test_cli_requires_matching_instrument(tmp_path, profile, instrument):
    args = [] if instrument is None else ["--instrument-id", instrument]
    run, output = invoke(tmp_path, profile, *args)
    assert run.returncode == 2 and not output.exists()
    assert "instrument" in run.stderr.lower()


@pytest.mark.parametrize(
    "failure",
    ["air", "bands", "width", "extra_class", "empty", "too_many", "string", "edge", "gap"],
)
def test_rehashed_invalid_plan_fails_before_reading_input(profile, monkeypatch, failure):
    raw = json.loads(profile["artifact"].read_text())
    plan = raw["plan"]
    if failure == "air":
        plan["wavelength_medium"] = "air"
    elif failure == "bands":
        plan["bands"][0][0] = 240
    elif failure == "width":
        plan["sideband_outer_nm"] = 0.9
    elif failure == "extra_class":
        plan["lines_nm"]["H"] = [650]
    else:
        plan["lines_nm"]["Mg"] = {
            "empty": [],
            "too_many": [285.3] * 33,
            "string": ["285.3"],
            "edge": [241.9],
            "gap": [370],
        }[failure]
    rehash(profile["artifact"], raw)
    monkeypatch.setattr(prediction, "read_chemcam_ccs", lambda *_: pytest.fail("Input read"))
    with pytest.raises(ValueError):
        prediction.predict_measurement(profile["artifact"], profile["input"], "synthetic-testbed")


@pytest.mark.parametrize("failure", ["zero", "empty_band", "gap", "coverage"])
def test_invalid_measurement_fails_before_per_line_feature_loops(profile, monkeypatch, failure):
    wave, intensity = profile["wave"], profile["intensity"].copy()
    if failure == "zero":
        intensity[:] = 0
    elif failure == "empty_band":
        intensity[wave <= 340] = 0
    elif failure == "gap":
        keep = (wave <= 285) | (wave >= 286)
        wave, intensity = wave[keep], intensity[keep]
    elif failure == "coverage":
        wave, intensity = wave[1:], intensity[1:]
    write_ccs(profile["input"], wave, intensity)
    monkeypatch.setattr(prediction, "extract_line_features", lambda *_: pytest.fail("Line loop"))
    with pytest.raises(ValueError):
        prediction.predict_measurement(profile["artifact"], profile["input"], "synthetic-testbed")


@pytest.mark.parametrize("failure", ["tamper", "duplicate", "nonfinite", "bad_state", "symlink"])
def test_profile_rejects_unsafe_or_invalid_numeric_json(profile, failure, tmp_path):
    path = profile["artifact"]
    raw = json.loads(path.read_text())
    if failure == "tamper":
        raw["states"]["Fe"]["intercept"] += 1
        path.write_text(json.dumps(raw))
    elif failure == "duplicate":
        path.write_text(
            path.read_text().replace(
                '"schema_version": 2,', '"schema_version": 2, "schema_version": 2,', 1
            )
        )
    elif failure == "nonfinite":
        raw["plan"]["lines_nm"]["Mg"] = [float("nan")]
        path.write_text(json.dumps(raw))
    elif failure == "bad_state":
        raw["states"]["Fe"]["scale"][0] = 0
        rehash(path, raw)
    elif failure == "symlink":
        link = tmp_path / "linked.json"
        link.symlink_to(path)
        path = link
    with pytest.raises(ValueError):
        prediction.load_profile(path, "synthetic-testbed")


def test_cli_does_not_overwrite_existing_result(tmp_path, profile):
    path = tmp_path / "prediction.json"
    path.write_text("existing result\n")
    run, output = invoke(tmp_path, profile, "--instrument-id", "synthetic-testbed")
    assert run.returncode == 2 and "already exists" in run.stderr
    assert output.read_text() == "existing result\n"
