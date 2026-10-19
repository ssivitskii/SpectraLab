"""Fictional contracts only: this is not real independent validation."""

import copy

import numpy as np
import pytest
from spectralab_ml.evaluation.calcium_crossfit_v4 import crossfit_partitions, fit_crossfit
from spectralab_ml.models.calcium_adapter_v4 import CalciumClassifier, feature_dimension
from spectralab_ml.models.calcium_ensemble_v4 import CalciumEnsembleV4
from spectralab_ml.models.line_adapter_v3 import GRID
from test_calcium_v4 import fixture as fixture


def records_fixture():
    return [
        {
            "measurement_id": str(i),
            "physical_sample_id": f"p{i}",
            "family_id": f"f{i}",
            "session_id": f"s{i}",
            "input_sha256": f"h{i}",
            "campaign": i % 2,
        }
        for i in range(100)
    ]


def test_four_member_inner_exclusion_and_no_outer_selection(tmp_path):
    records = records_fixture()
    y = (np.arange(100) // 2) % 2
    parent, validation = np.arange(80), np.arange(80, 100)
    parts = crossfit_partitions(records, y, parent, 42)
    assert len(parts) == 16
    np.testing.assert_array_equal(np.bincount(np.concatenate([p["inner_oof"] for p in parts])), 4)
    for p in parts:
        assert not (set(p["fit"]) & set(p["calibrate"]))
        assert not ((set(p["fit"]) | set(p["calibrate"])) & set(p["inner_oof"]))
    rng = np.random.default_rng(1)
    X = rng.normal(size=(100, 4)) + y[:, None]
    specs = [
        {
            "id": "fixture",
            "family": "full-v3-ca",
            "learner": "logistic",
            "C": 0.1,
            "gamma_multiplier": 1.0,
        }
    ]
    prediction, report, models = fit_crossfit(
        {"full-v3-ca": X}, y, records, parent, validation, specs, [0.025], 42, tmp_path / "a"
    )
    changed = X.copy()
    changed[validation] *= 100
    changed_y = y.copy()
    changed_y[validation] = 1 - changed_y[validation]
    _, report_b, models_b = fit_crossfit(
        {"full-v3-ca": changed},
        changed_y,
        records,
        parent,
        validation,
        specs,
        [0.025],
        42,
        tmp_path / "b",
    )
    assert report == report_b and models == models_b
    expected = np.mean(
        [CalciumClassifier.from_payload(m, 4).probabilities(X[validation]) for m in models[0]],
        axis=0,
    )
    np.testing.assert_allclose(prediction[0], expected, atol=1e-15)
    # A family/session chain crossing the supplied outer boundary is refused.
    records[80]["session_id"] = records[0]["session_id"]
    with pytest.raises(ValueError, match="crossed"):
        fit_crossfit(
            {"full-v3-ca": X},
            y,
            records,
            parent,
            validation,
            specs,
            [0.025],
            42,
            tmp_path / "crossed",
        )


def test_ensemble_safe_roundtrip_and_probability_aggregation(tmp_path, fixture):  # noqa: F811
    plan, X, _, _, original = fixture
    family = "blue-ca-agreement"
    dim = feature_dimension(plan, family)
    members = []
    for probability in [0.1, 0.2, 0.7, 0.9]:
        members.append(
            {
                "state": {
                    "learner": "logistic",
                    "C": 0.1,
                    "mean": [0.0] * dim,
                    "scale": [1.0] * dim,
                    "coef": [0.0] * dim,
                    "intercept": 0.0,
                },
                "calibration": {
                    "slope": 1.0,
                    "intercept": float(np.log(probability / (1 - probability))),
                },
            }
        )
    path = tmp_path / "ensemble.json"
    CalciumEnsembleV4.save(
        path,
        original_profile=original,
        members=members,
        family=family,
        threshold=0.5,
        provenance={"fictional": True},
    )
    loaded, raw = CalciumEnsembleV4.load(path)
    result = loaded.predict_spectra(X[:2], GRID)
    np.testing.assert_allclose(result["probabilities"][:, 2], 0.475)
    np.testing.assert_array_equal(result["probabilities"][:, [0, 1, 3]], 0.5)
    assert not result["detections"][:, 2].any() and result["accepted"][:, 2].all()
    for bad in ("members", "instrument", "dimensions"):
        value = copy.deepcopy(raw)
        if bad == "members":
            value["members"].pop()
        elif bad == "instrument":
            value["provenance"]["instrument_id"] = "wrong"
        else:
            value["members"][3]["state"]["scale"].pop()
        with pytest.raises(ValueError):
            CalciumEnsembleV4.from_payload(value)
    path.write_text('{"schema_version": 4, "schema_version": 4}')
    with pytest.raises(ValueError):
        CalciumEnsembleV4.load(path)
