import numpy as np
import pytest
from spectralab_ml.evaluation.instrument_selection import (
    grouped_development_split,
    select_operational_thresholds,
)


def test_threshold_budget_replicate_weight_and_unknown_invariance():
    scores = np.array([[0.8], [0.3], [0.6], [0.2], [0.99]])
    labels = np.array([[1], [1], [0], [0], [-1]])
    groups = ["p1", "p2", "n1", "n2", "unknown"]
    thresholds, report = select_operational_thresholds(scores, labels, groups)
    assert thresholds.tolist() == [0.8]
    assert report["per_class"][0]["recall"] == 0.5
    assert report["per_class"][0]["false_positive_rate"] == 0
    duplicate = np.r_[np.arange(5), 0, 0, 2, 2]
    copied, same_report = select_operational_thresholds(
        scores[duplicate], labels[duplicate], [groups[i] for i in duplicate]
    )
    np.testing.assert_array_equal(thresholds, copied)
    assert same_report["macro_balanced_accuracy"] == pytest.approx(
        report["macro_balanced_accuracy"]
    )
    assert same_report["per_class"][0]["recall"] == pytest.approx(0.5)
    assert same_report["per_class"][0]["negative_physical_support"] == 2
    scores[-1] = -100
    changed, _ = select_operational_thresholds(scores, labels, groups)
    np.testing.assert_array_equal(changed, thresholds)


def test_equal_scores_allow_reject_all_and_require_support():
    threshold, report = select_operational_thresholds(
        np.ones((2, 1)), np.array([[0], [1]]), ["a", "b"]
    )
    assert threshold[0] == np.nextafter(1.0, np.inf)
    assert report["per_class"][0]["false_positive_rate"] == 0
    with pytest.raises(ValueError, match="positive and negative"):
        select_operational_thresholds(np.ones((2, 1)), np.ones((2, 1)), ["a", "b"])


def test_partition_is_deterministic_and_blocks_family_and_session_leakage():
    records = [
        {
            "measurement_id": str(i),
            "physical_sample_id": str(i),
            "family_id": str(i // 2),
            "session_id": str(i // 4),
            "input_sha256": str(i),
            "labels": {"Na": {"state": "positive" if i % 2 else "negative"}},
        }
        for i in range(40)
    ]
    split = grouped_development_split(records, ["Na"], 7)
    assert split == grouped_development_split(records, ["Na"], 7)
    train = [r for r in records if r["measurement_id"] in split["train_ids"]]
    tune = [r for r in records if r["measurement_id"] in split["tune_ids"]]
    for field in ("physical_sample_id", "family_id", "session_id", "input_sha256"):
        assert not {r[field] for r in train} & {r[field] for r in tune}
    with pytest.raises(ValueError, match="four development clusters"):
        grouped_development_split(records[:4], ["Na"], 7)
