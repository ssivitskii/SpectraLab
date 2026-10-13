from __future__ import annotations

from copy import deepcopy

import numpy as np
import pytest
from spectralab_ml.data.reference import ReferenceCatalog, SpectralLine
from spectralab_ml.evaluation import campaign_shift_v2 as diagnostic


@pytest.fixture
def plan():
    lines = [
        SpectralLine(
            element,
            stage,
            centre + 0.001,
            "vacuum",
            "observed",
            100,
            "100",
            "",
            "NIST fixture",
            str(centre),
            {},
        )
        for _, element, stage, centre in diagnostic.LINE_ANCHORS
    ]
    return diagnostic.feature_plan(ReferenceCatalog(lines, {"wavelength_medium": "vacuum"}))


@pytest.fixture
def grid():
    return 240 + np.arange(6601) * 0.1


def record(i, *, campaign="campaign_2015", physical=None, family=None, session=None):
    return {
        "measurement_id": f"m{i}",
        "physical_sample_id": physical or f"p{i}",
        "family_id": family or f"f{i}",
        "session_id": session or f"s{i}",
        "input_sha256": f"sha{i}",
        "campaign": campaign,
        "labels": {c: {"state": "negative" if i % 2 else "positive"} for c in diagnostic.CLASSES},
    }


def test_feature_math_flat_spectrum_and_line_background(grid, plan):
    X, names = diagnostic.extract_features(grid, np.ones((1, len(grid))), plan)
    values = dict(zip(names, X[0], strict=True))
    assert values["uv_log_positive_area"] == pytest.approx(np.log1p(99))
    assert values["blue_log_positive_area"] == pytest.approx(np.log1p(85))
    assert values["visible_log_positive_area"] == pytest.approx(np.log1p(426))
    assert values["log_area_ratio_uv_to_visible"] == pytest.approx(np.log(99 / 426))
    assert values["uv_negative_area_fraction"] == 0
    assert values["Mg_I_285_log_signal_background"] == pytest.approx(np.log(2))
    assert values["Mg_I_285_contrast"] == 0
    line = next(line for line in plan["lines"] if line["name"] == "Mg_I_285")
    signal = np.ones((1, len(grid)))
    signal[:, np.abs(grid - line["wavelength_nm"]) <= 0.2] = 5
    peak, _ = diagnostic.extract_features(grid, signal, plan)
    assert peak[0, names.index("Mg_I_285_log_signal_background")] == pytest.approx(np.log(6))
    assert peak[0, names.index("Mg_I_285_contrast")] == pytest.approx(4 / (6 + 1e-6))


def test_gaps_never_contribute_and_negative_signal_is_explicit(grid, plan):
    original = np.ones((1, len(grid)))
    mask = np.zeros(len(grid), dtype=bool)
    for start, end in diagnostic.BANDS.values():
        mask |= (grid >= start) & (grid <= end)
    changed = original.copy()
    changed[:, ~mask] = 1e10
    before, names = diagnostic.extract_features(grid, original, plan)
    after, _ = diagnostic.extract_features(grid, changed, plan)
    np.testing.assert_array_equal(before, after)
    changed = original.copy()
    changed[:, (grid >= 300) & (grid <= 310)] = -2
    negative, _ = diagnostic.extract_features(grid, changed, plan)
    assert negative[0, names.index("uv_negative_point_fraction")] == pytest.approx(101 / 991)
    assert negative[0, names.index("uv_negative_area_fraction")] > 0
    unchanged = [
        i
        for i, name in enumerate(names)
        if not name.startswith("uv_") and name != "log_area_ratio_uv_to_visible"
    ]
    np.testing.assert_array_equal(before[:, unchanged], negative[:, unchanged])


def test_relative_features_are_invariant_to_positive_global_scale(grid, plan):
    signal = 1 + np.random.default_rng(42).random((1, len(grid)))
    signal[:, 1000:1050] *= -1
    first, names = diagnostic.extract_features(grid, signal, plan)
    scaled, _ = diagnostic.extract_features(grid, signal * 100, plan)
    relative = [i for i, name in enumerate(names) if not name.endswith("_log_positive_area")]
    np.testing.assert_allclose(first[:, relative], scaled[:, relative], atol=1e-12)


def test_zero_band_and_changed_grid_fail_without_silent_drops(grid, plan):
    signal = np.ones((2, len(grid)))
    signal[1, (grid >= 241) & (grid <= 340)] = -1
    with pytest.raises(ValueError, match="positive observed signal in uv"):
        diagnostic.extract_features(grid, signal, plan)
    with pytest.raises(ValueError, match="fixed 240"):
        diagnostic.extract_features(grid + 0.01, np.ones((1, len(grid))), plan)


def test_reference_must_contain_all_fixed_vacuum_transitions(plan):
    assert plan["lines"][0]["wavelength_nm"] == pytest.approx(diagnostic.LINE_ANCHORS[0][3] + 0.001)
    with pytest.raises(ValueError, match="explicitly vacuum"):
        diagnostic.feature_plan(ReferenceCatalog([], {"wavelength_medium": "air"}))
    with pytest.raises(ValueError, match="Missing pinned"):
        diagnostic.feature_plan(ReferenceCatalog([], {"wavelength_medium": "vacuum"}))


def test_connected_groups_are_transitive_and_physical_repeats_have_one_weight():
    records = [
        record(0, physical="repeat", family="a"),
        record(2, physical="repeat", family="b"),
        record(4, family="b"),
    ]
    assert len(set(diagnostic.connected_groups(records))) == 1
    rows, features = diagnostic.aggregate_physical(records, np.asarray([[1, 3], [3, 5], [8, 10]]))
    repeated = next(i for i, row in enumerate(rows) if row["physical_sample_id"] == "repeat")
    np.testing.assert_array_equal(features[repeated], [2, 4])
    assert len(rows) == 2
    assert rows[repeated]["replicates"] == 2
    summaries = diagnostic.descriptive_summaries(rows, features, ["x", "y"])
    assert summaries["campaign_2015"]["all"]["physical_samples"] == 2
    assert summaries["campaign_2015"]["all"]["features"]["x"]["median"] == 5
    records[1]["labels"]["Mg"]["state"] = "negative"
    with pytest.raises(ValueError, match="conflicting campaign or labels"):
        diagnostic.aggregate_physical(records, np.ones((3, 2)))


def test_class_conditional_support_and_unknown_are_separate():
    records = [record(0), record(1), record(2, campaign="campaign_2021")]
    records[0]["labels"]["Mg"]["state"] = "unknown"
    rows, features = diagnostic.aggregate_physical(records, np.asarray([[1], [2], [3]]))
    summary = diagnostic.descriptive_summaries(rows, features, ["x"])
    assert summary["campaign_2015"]["Mg:unknown"]["physical_samples"] == 1
    assert summary["campaign_2015"]["Mg:positive"]["physical_samples"] == 0
    assert summary["campaign_2015"]["Mg:positive"]["features"]["x"] is None
    assert diagnostic.composition_features(rows).shape == (3, 12)
    np.testing.assert_array_equal(diagnostic.composition_features(rows).sum(axis=1), 4)


def test_grouped_domain_cv_uses_training_fold_scaler_only_and_is_deterministic(monkeypatch):
    rows = [
        {
            "physical_sample_id": f"p{i}",
            "cluster_id": f"g{i // 2}",
            "campaign": "campaign_2015" if i < 20 else "campaign_2021",
        }
        for i in range(40)
    ]
    features = np.column_stack([np.arange(40), np.arange(40) ** 2])
    fits = []
    real_scaler = diagnostic.StandardScaler

    class TrackingScaler(real_scaler):
        def fit(self, X, *args, **kwargs):
            fits.append(X.copy())
            return super().fit(X, *args, **kwargs)

    monkeypatch.setattr(diagnostic, "StandardScaler", TrackingScaler)
    result = diagnostic.domain_separability(rows, features)
    assert result["status"] == "completed"
    assert len(fits) == result["n_splits"] == 5
    validation = []
    for fold, fitted in zip(result["folds"], fits, strict=True):
        train = [int(name[1:]) for name in fold["train_physical_ids"]]
        np.testing.assert_array_equal(fitted, features[train])
        assert not set(fold["train_clusters"]) & set(fold["validation_clusters"])
        validation.extend(fold["validation_physical_ids"])
    assert sorted(validation) == sorted(row["physical_sample_id"] for row in rows)
    repeated = diagnostic.domain_separability(rows, features)
    assert repeated == result


def test_single_connected_group_is_not_reported_as_cv_evidence():
    rows = [
        {"physical_sample_id": f"p{i}", "cluster_id": "same", "campaign": "a" if i % 2 else "b"}
        for i in range(6)
    ]
    result = diagnostic.domain_separability(rows, np.ones((6, 2)))
    assert result["status"] == "insufficient_groups"


def test_line_window_cannot_move_into_missing_band(grid, plan):
    changed = deepcopy(plan)
    changed["lines"][0]["wavelength_nm"] = 360
    with pytest.raises(ValueError, match="fully observed"):
        diagnostic.extract_features(grid, np.ones((1, len(grid))), changed)


def test_conditional_domain_analysis_never_uses_unknown_as_negative():
    rows = [
        {
            "physical_sample_id": f"p{i}",
            "cluster_id": f"g{i // 2}",
            "campaign": "a" if i < 20 else "b",
            "labels": {
                "Mg": "unknown" if i % 3 == 0 else "positive",
                "Fe": "negative" if i < 20 else "unknown",
            },
        }
        for i in range(40)
    ]
    result = diagnostic.conditional_domain_separability(rows, np.arange(40).reshape(-1, 1))
    assert result["Mg:negative"]["status"] == "insufficient_campaign_support"
    assert result["Mg:negative"]["physical_samples"] == 0
    assert result["Fe:negative"]["status"] == "insufficient_campaign_support"
    assert result["Fe:negative"]["physical_samples"] == 20
    assert result["Mg:positive"]["status"] == "completed"
    assert result["Mg:positive"]["physical_samples"] == 26
    assert len(result["Mg:positive"]["out_of_fold"]) == 26
