from __future__ import annotations

import copy
import json

import numpy as np
import pytest
from spectralab_ml.evaluation.calcium_v4 import choose_candidate, counts, fit_candidates
from spectralab_ml.models.calcium_adapter_v4 import (
    BLUE_LINES,
    BLUE_STAGES,
    CalciumAdapterV4,
    CalciumClassifier,
    calcium_features,
)
from spectralab_ml.models.line_adapter_v2 import LineAdapterV2
from spectralab_ml.models.line_adapter_v3 import (
    BANDS,
    CLASSES,
    DECISION_POLICY,
    GRID,
    LineAdapterV3,
    digest,
    feature_count,
)


@pytest.fixture
def fixture(tmp_path):
    plan = {
        "schema_version": 3,
        "variant": "continuum-snr-ion",
        "class_order": CLASSES,
        "wavelength_medium": "vacuum",
        "bands": BANDS,
        "grid_nm": {"start": 240.0, "end": 900.0, "points": 6601},
        "lines_nm": {"Na": [589.2], "Mg": [285.3], "Ca": BLUE_LINES, "Fe": [404.7]},
        "stages": {"Na": [1], "Mg": [1], "Ca": BLUE_STAGES, "Fe": [1]},
        "core_half_width_nm": 0.2,
        "sideband_inner_nm": 0.5,
        "sideband_outer_nm": 1.0,
        "robust_noise_floor_fraction": 0.001,
        "snr_cap": 1000.0,
    }
    y = np.arange(48) % 2
    rng = np.random.default_rng(10)
    X = 2 + rng.normal(0, 0.05, (48, len(GRID)))
    for center in BLUE_LINES:
        X += (0.1 + y[:, None] * 4) * np.exp(-(((GRID - center) / 0.08) ** 2))
    features = calcium_features(X, GRID, plan)
    base = LineAdapterV3()
    base.base = LineAdapterV2()
    base.base.C = 0.1
    base.base.states = {
        c: {
            "mean": [0.0] * feature_count(plan, c),
            "scale": [1.0] * feature_count(plan, c),
            "coef": [0.0] * feature_count(plan, c),
            "intercept": 0.0,
        }
        for c in CLASSES
    }
    base.calibration = {c: {"slope": 1.0, "intercept": 0.0} for c in CLASSES}
    path = tmp_path / "original.json"
    base.save(
        path,
        plan=plan,
        thresholds=[0.4] * 4,
        selective={c: {"minimum_snr": 0.0, "margin": 0.0} for c in CLASSES},
        provenance={
            "instrument_id": "fixture",
            "decision_policy": DECISION_POLICY,
            "scope": "nested_grouped_development_cv_not_independent_test",
        },
    )
    return plan, X, y, features, json.loads(path.read_text())


@pytest.mark.parametrize("learner", ["logistic", "rbf"])
def test_numeric_ca_roundtrip_and_other_elements_unchanged(tmp_path, fixture, learner):
    plan, X, y, features, original = fixture
    family = "blue-ca-agreement"
    f = features[family]
    model = CalciumClassifier().fit(
        f[:16], y[:16], [f"fit{i}" for i in range(16)], learner=learner, C=1.0, gamma_multiplier=0.1
    )
    model.fit_calibration(f[16:32], y[16:32], [f"cal{i}" for i in range(16)])
    path = tmp_path / "calcium.json"
    CalciumAdapterV4.save(
        path,
        original_profile=original,
        calcium=model,
        family=family,
        threshold=0.5,
        provenance={"fixture": True},
    )
    loaded, metadata = CalciumAdapterV4.load(path)
    actual = loaded.predict_spectra(X[32:], GRID)
    np.testing.assert_array_equal(actual["probabilities"][:, 2], model.probabilities(f[32:]))
    np.testing.assert_array_equal(actual["probabilities"][:, [0, 1, 3]], 0.5)
    assert actual["accepted"][:, 2].all()
    assert metadata["provenance"]["instrument_id"] == "fixture"
    assert metadata["final_test_status"] == "not_performed"
    np.testing.assert_allclose(
        features[family], calcium_features(X * 37, GRID, plan)[family], atol=1e-10
    )
    changed = copy.deepcopy(metadata)
    changed.pop("identity")
    changed["provenance"]["instrument_id"] = "another"
    changed["identity"] = digest(changed)
    path.write_text(json.dumps(changed))
    with pytest.raises(ValueError, match="instrument"):
        CalciumAdapterV4.load(path)


def test_unknown_labels_repeats_and_disjoint_calibration(fixture):
    _, _, y, features, _ = fixture
    X = features["full-v3-ca"]
    ids = [f"fit{i}" for i in range(16)]
    a = CalciumClassifier().fit(X[:16], y[:16], ids, learner="logistic", C=0.1)
    b = CalciumClassifier().fit(
        np.vstack([X[:16], X[:1], np.full_like(X[:1], 1e6)]),
        np.r_[y[:16], y[0], -1],
        [*ids, ids[0], "unknown"],
        learner="logistic",
        C=0.1,
    )
    np.testing.assert_allclose(a.raw_scores(X), b.raw_scores(X), atol=1e-8)
    with pytest.raises(ValueError, match="disjoint"):
        a.fit_calibration(X[:16], y[:16], ids)
    calids = [f"cal{i}" for i in range(16)]
    a.fit_calibration(X[16:32], y[16:32], calids)
    b.fit_calibration(X[16:32], y[16:32], calids)
    np.testing.assert_allclose(a.probabilities(X), b.probabilities(X), atol=1e-7)


def test_outer_data_do_not_enter_inner_selection(tmp_path, fixture):
    _, _, y, features, _ = fixture
    records = [
        {
            "measurement_id": str(i),
            "physical_sample_id": f"p{i}",
            "family_id": f"f{i}",
            "session_id": f"s{i}",
            "input_sha256": f"hash{i}",
        }
        for i in range(48)
    ]
    parts = {"fit": np.arange(16), "calibrate": np.arange(16, 28), "tune": np.arange(28, 40)}
    specs = [
        {
            "id": "a",
            "family": "full-v3-ca",
            "learner": "logistic",
            "C": 0.1,
            "gamma_multiplier": 1.0,
        },
        {
            "id": "b",
            "family": "blue-ca-agreement",
            "learner": "rbf",
            "C": 1.0,
            "gamma_multiplier": 0.1,
        },
    ]
    models_a, reports_a = fit_candidates(
        features, y, records, parts, specs, [0.025, 0.05], tmp_path / "a"
    )
    changed = {k: v.copy() for k, v in features.items()}
    changed_y = y.copy()
    for v in changed.values():
        v[40:] = 1e9
    changed_y[40:] = 1 - y[40:]
    models_b, reports_b = fit_candidates(
        changed, changed_y, records, parts, specs, [0.025, 0.05], tmp_path / "b"
    )
    assert reports_a == reports_b
    assert choose_candidate(reports_a, 0.025) == choose_candidate(reports_b, 0.025)
    np.testing.assert_array_equal(
        models_a[0].probabilities(features["full-v3-ca"]),
        models_b[0].probabilities(features["full-v3-ca"]),
    )
    records[16]["session_id"] = records[0]["session_id"]
    with pytest.raises(ValueError, match="crossed"):
        fit_candidates(features, y, records, parts, specs, [0.025], tmp_path / "bad")


def test_targets_require_unconditional_recall_and_false_positives():
    y = np.r_[np.ones(93), np.zeros(51), [-1] * 10]
    detected = np.zeros(len(y), bool)
    detected[:83] = True
    detected[93:95] = True
    assert counts(y, detected, np.ones(len(y)))["meets_point_target"]
    detected[95] = True
    assert not counts(y, detected, np.ones(len(y)))["meets_point_target"]
    detected[95] = False
    detected[82] = False
    assert not counts(y, detected, np.ones(len(y)))["meets_point_target"]


def test_bad_numeric_rbf_and_wrong_line_stage_rejected(fixture):
    plan, X, y, features, _ = fixture
    model = CalciumClassifier().fit(
        features["blue-ca-agreement"][:16],
        y[:16],
        [str(i) for i in range(16)],
        learner="rbf",
        C=1.0,
    )
    model.fit_calibration(
        features["blue-ca-agreement"][16:32], y[16:32], [f"cal{i}" for i in range(16)]
    )
    state = model.payload()
    state["state"]["support_vectors"][0] = [1.0]
    with pytest.raises(ValueError, match="RBF"):
        CalciumClassifier.from_payload(state, 34)
    bad = copy.deepcopy(plan)
    bad["stages"]["Ca"][0] = 1
    with pytest.raises(ValueError, match="stage"):
        calcium_features(X, GRID, bad)
