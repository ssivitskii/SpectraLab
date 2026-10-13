from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
from spectralab_ml.models.instrument_adaptation import (
    NEVER_POSITIVE_THRESHOLD,
    InstrumentAdapter,
)

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "predict_instrument.py"


def write_ccs(path, wavelengths, intensity):
    rows = [
        "# Synthetic CLI fixture only",
        "# NSHOTS = 6",
        "# wave,shot1,shot2,shot3,shot4,shot5,shot6,median,mean",
    ]
    for wave, value in zip(wavelengths, intensity, strict=True):
        # First five shots and provider summaries intentionally disagree with retained shot6.
        row = [wave, *([999] * 5), value, -999, -999]
        rows.append(",".join(format(float(v), ".17g") for v in row))
    path.write_text("\n".join(rows) + "\n")


@pytest.fixture
def synthetic_profile(tmp_path):
    grid = np.round(np.arange(500, 506.01, 0.1), 8)
    y = np.array([[bool(i & 1), bool(i & 2)] for i in range(4)])
    X = np.ones((4, len(grid)))
    X[:, np.argmin(abs(grid - 500.2))] += y[:, 0] * 10
    X[:, np.argmin(abs(grid - 504.2))] += y[:, 1] * 10
    model = InstrumentAdapter().fit(
        X,
        y,
        np.ones_like(y, bool),
        sample_ids=[f"synthetic-{i}" for i in range(4)],
        grid_nm=grid,
        observed_intervals=[[500, 502], [504, 506]],
        class_order=("Na", "Mg"),
        instrument_id="synthetic-testbed",
        provenance={
            "estimand": "operational_above_threshold",
            "oxide_policy": {"negative_max_wt_pct": 0.1, "positive_min_wt_pct": 1},
            "source": "synthetic unit-test fixture only",
        },
    )
    expected = model.scores(X[3:4])[0]
    artifact = tmp_path / "profile"
    model.save(artifact, thresholds={"Na": float(expected[0]), "Mg": NEVER_POSITIVE_THRESHOLD})
    selected = (grid <= 502) | (grid >= 504)
    source = tmp_path / "new.csv"
    write_ccs(source, grid[selected], X[3, selected])
    return {
        "artifact": artifact,
        "input": source,
        "model": model,
        "expected": expected,
        "wave": grid[selected],
        "intensity": X[3, selected],
    }


def invoke(tmp_path, fixture, *extra):
    output = tmp_path / "prediction.json"
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--artifact",
            str(fixture["artifact"]),
            "--input",
            str(fixture["input"]),
            "--output",
            str(output),
            *extra,
        ],
        cwd=tmp_path,
        env={**os.environ, "PYTHONPATH": str(ROOT / "ml" / "src")},
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    return result, output


def test_real_cli_uses_saved_profile_retained_shots_and_inclusive_threshold(
    tmp_path, synthetic_profile
):
    fixture = synthetic_profile
    protected = [fixture["input"], *fixture["artifact"].iterdir()]
    original = {p: p.read_bytes() for p in protected}
    run, output = invoke(tmp_path, fixture, "--instrument-id", "synthetic-testbed")
    assert run.returncode == 0, run.stderr
    result = json.loads(output.read_text())
    assert result["input_sha256"] == hashlib.sha256(original[fixture["input"]]).hexdigest()
    assert result["profile_identity"] == fixture["model"].identity
    assert result["score_kind"] == "instrument_adapted_logistic_score"
    assert result["calibrated"] is False
    assert result["estimand"] == "operational_above_threshold"
    assert result["oxide_policy"] == fixture["model"].manifest["provenance"]["oxide_policy"]
    assert result["aggregation"]["retained_shots"] == 1
    assert result["aggregation"]["discarded_shots"] == 5
    assert result["preprocessing"]["max_gap_nm"] == 0.5
    by_element = {p["element"]: p for p in result["predictions"]}
    assert by_element["Na"]["score"] == fixture["expected"][0]
    assert by_element["Na"]["score"] == by_element["Na"]["threshold"]
    assert by_element["Na"]["detected"] is True
    assert by_element["Mg"]["detected"] is False
    for element in ("H", "He"):
        assert by_element[element]["status"] == "unknown"
        assert by_element[element]["detected"] is None
        assert by_element[element]["score"] is None
    assert {p: p.read_bytes() for p in protected} == original


@pytest.mark.parametrize("instrument_id", [None, "", "different-instrument"])
def test_cli_requires_matching_instrument_and_writes_no_result_on_failure(
    tmp_path, synthetic_profile, instrument_id
):
    extra = [] if instrument_id is None else ["--instrument-id", instrument_id]
    run, output = invoke(tmp_path, synthetic_profile, *extra)
    assert run.returncode == 2
    assert not output.exists()
    assert "instrument" in run.stderr.lower()


def test_cli_rejects_profile_without_thresholds(tmp_path, synthetic_profile):
    model = synthetic_profile["model"]
    artifact = tmp_path / "no-thresholds"
    model.save(artifact)
    run, output = invoke(
        tmp_path,
        {**synthetic_profile, "artifact": artifact},
        "--instrument-id",
        "synthetic-testbed",
    )
    assert run.returncode == 2
    assert "thresholds are required" in run.stderr
    assert not output.exists()


@pytest.mark.parametrize("failure", ["gap", "missing_window", "zero_signal", "malformed"])
def test_cli_rejects_invalid_spectrum_without_repair_or_extrapolation(
    tmp_path, synthetic_profile, failure
):
    fixture = synthetic_profile
    wave, intensity = fixture["wave"], fixture["intensity"]
    if failure == "gap":
        selected = (wave <= 500.5) | (wave >= 501.2)
        wave, intensity = wave[selected], intensity[selected]
    elif failure == "missing_window":
        wave, intensity = wave[1:], intensity[1:]
    elif failure == "zero_signal":
        intensity = np.zeros_like(intensity)
    write_ccs(fixture["input"], wave, intensity)
    if failure == "malformed":
        fixture["input"].write_text("# NSHOTS=6\n# wave,shot2,shot1\n500,1,2\n")
    run, output = invoke(tmp_path, fixture, "--instrument-id", "synthetic-testbed")
    assert run.returncode == 2
    assert "Instrument prediction failed" in run.stderr
    assert not output.exists()


def test_cli_does_not_overwrite_an_existing_result(tmp_path, synthetic_profile):
    output = tmp_path / "prediction.json"
    output.write_text("existing result\n")
    run, _ = invoke(tmp_path, synthetic_profile, "--instrument-id", "synthetic-testbed")
    assert run.returncode == 2
    assert "already exists" in run.stderr
    assert output.read_text() == "existing result\n"
