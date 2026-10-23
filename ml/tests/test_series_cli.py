from __future__ import annotations

import copy
import hashlib
import json

import numpy as np
import pytest
from spectralab_ml.cli import main
from spectralab_ml.data.measured_spectra import read_measured_csv, read_measurement_metadata
from spectralab_ml.data.series_metadata import (
    read_series_metadata,
    validate_series_coverage,
    validate_series_metadata,
    validate_series_splits,
)


@pytest.fixture
def series(tmp_path):
    wave = np.linspace(500, 502, 21)
    good = 1 + 5 * np.exp(-(((wave - 501) / 0.3) ** 2))
    bad = good.copy()
    bad[2] = 100
    shots = np.array([np.zeros(21)] * 5 + [good, good * 1.1, bad, np.ones(21)])
    header = "# NSHOTS=9\n# wave," + ",".join(f"shot{i}" for i in range(1, 10))
    text = header + ",median,mean\n"
    for index, wavelength in enumerate(wave):
        text += ",".join(str(v) for v in [wavelength, *shots[:, index], 999, -999]) + "\n"
    raw = text.encode()
    source = tmp_path / "shots.csv"
    source.write_bytes(raw)
    metadata = {
        "schema_version": 1,
        "measurement_id": "fixture-series-1",
        "physical_sample_id": "fixture-material-1",
        "family_id": "fixture-family-1",
        "session_id": "fixture-session-1",
        "instrument_id": "synthetic-fixture-only",
        "axis": {
            "units": "nm",
            "medium": "vacuum",
            "source": "synthetic test fixture, not a physical measurement",
            "source_sha256": hashlib.sha256(raw).hexdigest(),
            "bands_nm": [[500.0, 502.0]],
            "max_step_nm": 0.101,
        },
        "input_sha256": hashlib.sha256(raw).hexdigest(),
    }
    metadata_path = tmp_path / "series.json"
    metadata_path.write_text(json.dumps(metadata))
    output = tmp_path / "run"
    return source, metadata_path, output, metadata, wave, good


def arguments(series):
    source, metadata_path, output, *_ = series
    return [
        "analyze-series",
        "--input",
        str(source),
        "--metadata",
        str(metadata_path),
        "--output-dir",
        str(output),
        "--min-snr",
        "0",
    ]


def test_cli_exports_auditable_series_and_reloadable_aggregate(series, capsys):
    _, _, output, metadata, wave, good = series
    main(arguments(series))
    result = json.loads(capsys.readouterr().out)
    assert result == json.loads((output / "receipt.json").read_text())
    assert result["status"] == "completed"
    assert result["accepted_shot_numbers"] == [6, 7]
    assert result["discarded_shot_numbers"] == [1, 2, 3, 4, 5]
    assert result["shots"][2]["shot_number"] == 8
    assert result["shots"][2]["reasons"] == ["shape_outlier"]
    assert result["measurement"] == metadata
    aggregate_metadata, _ = read_measurement_metadata(output / "metadata.json")
    actual_wave, aggregate, _ = read_measured_csv(
        output / "aggregate.csv", expected_sha256=aggregate_metadata["input_sha256"]
    )
    np.testing.assert_array_equal(actual_wave, wave)
    np.testing.assert_allclose(aggregate, 1.05 * good)
    for name, descriptor in result["outputs"].items():
        assert hashlib.sha256((output / name).read_bytes()).hexdigest() == descriptor["sha256"]


def test_insufficient_series_saves_receipt_and_exits_nonzero(series, capsys):
    output = series[2]
    with pytest.raises(SystemExit) as exc:
        main([*arguments(series), "--min-accepted-shots", "100"])
    assert exc.value.code == 2
    receipt = json.loads(capsys.readouterr().out)
    assert receipt["status"] == "insufficient_series"
    assert receipt["outputs"] == {}
    assert {p.name for p in output.iterdir()} == {"receipt.json"}


@pytest.mark.parametrize("problem", ["checksum", "coverage", "all_discarded", "existing_run"])
def test_invalid_input_never_writes_outputs_or_overwrites_a_run(series, problem, capsys):
    _, metadata_path, output, metadata, *_ = series
    args = arguments(series)
    if problem == "checksum":
        metadata["input_sha256"] = "0" * 64
    elif problem == "coverage":
        metadata["axis"]["max_step_nm"] = 0.01
    elif problem == "all_discarded":
        args += ["--discard-first-shots", "9"]
    else:
        output.mkdir()
        (output / "receipt.json").write_text("original")
    metadata_path.write_text(json.dumps(metadata))
    with pytest.raises(SystemExit) as exc:
        main(args)
    assert exc.value.code == 2
    capsys.readouterr()
    if problem == "existing_run":
        assert (output / "receipt.json").read_text() == "original"
    else:
        assert not output.exists()


def test_coverage_preserves_declared_gaps_but_rejects_missing_internal_band(series):
    metadata = copy.deepcopy(series[3])
    metadata["axis"]["bands_nm"] = [[500.0, 500.2], [600.0, 600.2]]
    wave = np.array([500, 500.1, 500.2, 600, 600.1, 600.2])
    assert len(validate_series_coverage(wave, metadata)) == 2
    with pytest.raises(ValueError, match="internal gap"):
        validate_series_coverage(np.delete(wave, 1), metadata)


@pytest.mark.parametrize("field", ["physical_sample_id", "family_id", "session_id"])
def test_splits_reject_leakage_of_any_declared_group(series, field):
    train = copy.deepcopy(series[3])
    test = copy.deepcopy(train)
    for key in ("measurement_id", "physical_sample_id", "family_id", "session_id"):
        test[key] += "-test"
    validate_series_splits({"train": [train], "test": [test]})
    test[field] = train[field]
    with pytest.raises(ValueError, match=field):
        validate_series_splits({"train": [train], "test": [test]})


@pytest.mark.parametrize(
    ("field", "bad"),
    [("physical_sample_id", ""), ("family_id", None), ("session_id", " session ")],
)
def test_series_metadata_requires_canonical_group_ids(series, field, bad):
    metadata = copy.deepcopy(series[3])
    metadata[field] = bad
    with pytest.raises(ValueError, match=field):
        validate_series_metadata(metadata)


def test_series_splits_reject_duplicate_series_even_inside_one_split(series):
    with pytest.raises(ValueError, match="Duplicate measurement_id"):
        validate_series_splits({"train": [series[3], series[3]], "test": []})


@pytest.mark.parametrize("raw", ['{"schema_version":1,"schema_version":1}', '{"x":NaN}'])
def test_metadata_parser_rejects_ambiguous_or_nonfinite_json(tmp_path, raw):
    path = tmp_path / "invalid.json"
    path.write_text(raw)
    with pytest.raises(ValueError):
        read_series_metadata(path)


def test_cli_example_runs_with_default_policy(tmp_path, capsys):
    from spectralab_ml.config import repository_root

    fixture = repository_root() / "data/fixtures"
    args = [
        "analyze-series",
        "--input",
        str(fixture / "shot_series_demo.csv"),
        "--metadata",
        str(fixture / "shot_series_demo.metadata.json"),
        "--output-dir",
        str(tmp_path / "demo"),
    ]
    main(args)
    receipt = json.loads(capsys.readouterr().out)
    assert receipt["accepted_shot_numbers"] == [6, 7]
    assert receipt["status"] == "completed"
