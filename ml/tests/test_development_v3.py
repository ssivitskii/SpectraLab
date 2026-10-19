from __future__ import annotations

import copy

import numpy as np
import pytest
from spectralab_ml.evaluation.campaign_shift_v2 import connected_groups
from spectralab_ml.evaluation.development_v3 import (
    assert_partition,
    class_metrics,
    fit_partition,
    inner_partition,
    outer_partitions,
    paired_brier_difference,
    reliability,
    select_refusal_policy,
)
from spectralab_ml.models.line_adapter_v3 import CLASSES, physical_weights


def records(n=80):
    return [
        {
            "measurement_id": f"measurement-{i}",
            "physical_sample_id": f"sample-{i}",
            "family_id": f"family-{i}",
            "session_id": f"session-{i}",
            "input_sha256": f"checksum-{i}",
            "role": "development",
            "campaign": i % 2,
            "labels": {c: {"state": "positive" if i % 2 else "negative"} for c in CLASSES},
        }
        for i in range(n)
    ]


def test_transitive_related_records_never_cross_outer_or_inner_roles():
    rows = records()
    # A--family--B--session--C--checksum--D is one connected unit.
    rows[1]["family_id"] = rows[0]["family_id"]
    rows[2]["session_id"] = rows[1]["session_id"]
    rows[3]["input_sha256"] = rows[2]["input_sha256"]
    groups = np.asarray(connected_groups(rows))
    assert len(set(groups[:4])) == 1
    with pytest.raises(ValueError, match="crossed"):
        assert_partition(groups, {"a": np.array([0]), "b": np.array([3])}, np.array([0, 3]))
    for outer in outer_partitions(rows, seed=20261019):
        assert_partition(groups, outer, np.arange(len(rows)))
        parts, _ = inner_partition(rows, outer["outer_fit"], seed=7)
        assert_partition(groups, parts, outer["outer_fit"])
        assert not set(groups[outer["outer_validation"]]) & set(groups[parts["calibrate"]])


def test_outer_partition_never_uses_element_response_labels():
    rows = records()
    changed = copy.deepcopy(rows)
    for r in changed:
        r["labels"] = {c: {"state": "unknown"} for c in CLASSES}
    first, second = outer_partitions(rows, seed=9), outer_partitions(changed, seed=9)
    for a, b in zip(first, second, strict=True):
        for key in a:
            np.testing.assert_array_equal(a[key], b[key])


def test_fit_selection_does_not_use_outer_labels_or_features(tmp_path):
    rows = records(40)
    labels = np.tile(np.arange(40)[:, None] % 2, (1, 4))
    rng = np.random.default_rng(4)
    features = {c: rng.normal(size=(40, 6)) + labels[:, k, None] for k, c in enumerate(CLASSES)}
    evidence = np.full(labels.shape, 20.0)
    parts = {"fit": np.arange(16), "calibrate": np.arange(16, 24), "tune": np.arange(24, 32)}
    first, first_report = fit_partition(features, evidence, labels, rows, parts, tmp_path / "a")
    changed_labels = labels.copy()
    changed_labels[32:] = 1 - labels[32:]
    changed_features = {c: X.copy() for c, X in features.items()}
    for X in changed_features.values():
        X[32:] = 1e10
    second, second_report = fit_partition(
        changed_features, evidence, changed_labels, rows, parts, tmp_path / "b"
    )
    assert first_report == second_report
    np.testing.assert_array_equal(first.probabilities(features), second.probabilities(features))


def test_weighted_calibration_ignores_unknowns_and_repeated_measurements():
    labels = np.array([0, 1, -1])
    p = np.array([0.2, 0.7, 0.99])
    original = reliability(labels, p, physical_weights(["a", "b", "unknown"]))
    repeated = reliability(
        np.array([0, 0, 1, -1]),
        np.array([0.2, 0.2, 0.7, 0.01]),
        physical_weights(["a", "a", "b", "unknown"]),
    )
    assert original == repeated
    assert original["brier"] == pytest.approx((0.2**2 + 0.3**2) / 2)


def test_selective_metrics_count_accepted_false_negatives_and_refusals():
    y = np.array([1, 1, 0, 0, -1])
    detected = np.array([True, False, False, False, True])
    all_answers = class_metrics(y, detected, np.ones(5, bool), np.ones(5))
    selective = class_metrics(y, detected, np.array([True, False, True, True, True]), np.ones(5))
    assert all_answers["accepted_false_negative_weight"] == 1
    assert all_answers["selective_recall"] == 0.5
    assert selective["selective_recall"] == 1
    assert selective["positive_capture_including_refusals"] == 0.5
    assert selective["refused_positive_weight"] == 1
    assert selective["known_physical_weight"] == 4
    assert selective["unknown_truth_acceptance"] == 1


def test_refusal_selection_ignores_unknown_scores():
    y = np.tile(np.array([0, 0, 1, 1, -1])[:, None], (1, 4))
    p = np.tile(np.array([0.1, 0.2, 0.9, 0.55, 0.99])[:, None], (1, 4))
    snr = np.full(y.shape, 10.0)
    first, _ = select_refusal_policy(p, snr, y, list("abcdu"), np.full(4, 0.5))
    p[-1], snr[-1] = 0.5, 0
    second, _ = select_refusal_policy(p, snr, y, list("abcdu"), np.full(4, 0.5))
    assert first == second


def test_paired_interval_is_matched_and_unknown_truth_does_not_enter():
    rows = records(12)
    labels = np.tile((np.arange(12) % 2)[:, None], (1, 4))
    labels[-1] = -1
    raw = np.full(labels.shape, 0.5)
    calibrated = np.where(labels == 1, 0.8, 0.2)
    # Every known sample improves squared error by exactly .21, in every resample.
    calibrated[-1] = 0.99
    result = paired_brier_difference(rows, labels, raw, calibrated, seed=11)
    for value in result["differences"].values():
        for key in ("point", "lower", "upper"):
            assert value[key] == pytest.approx(-0.21)
