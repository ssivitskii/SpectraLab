"""Statistical contracts for material-level operational rates (toy data only)."""

import numpy as np
import pytest
from spectralab_ml.evaluation.plasma_validation import operational_report


def report_for(detections, sample_ids, labels=None, **kwargs):
    detections = np.asarray(detections).reshape(-1, 1)
    if labels is None:
        labels = [[False] for _ in sample_ids]
    return operational_report(
        detections.astype(float), detections, labels, sample_ids, ("Ca",), **kwargs
    )


@pytest.mark.parametrize("detection", [False, True])
def test_unanimous_materials_do_not_imply_certainty(detection):
    report = report_for([detection, detection], ["material-a", "material-b"])
    rate = report["per_element"]["Ca"]["false_positive_rate"]
    assert rate["value"] == float(detection)
    assert rate["support"] == 2
    lower, upper = rate["ci95"]
    assert lower < upper
    if detection:
        assert lower < 0.95 and upper == 1.0
    else:
        assert lower == 0.0 and upper > 0.05


def test_one_material_has_full_uncertainty_even_with_many_replicates():
    report = report_for([False] * 100, ["only-material"] * 100)
    rate = report["per_element"]["Ca"]["false_positive_rate"]
    assert rate == {"value": 0.0, "ci95": [0.0, 1.0], "support": 1}


def test_materials_have_equal_weight_despite_unequal_replicate_counts():
    report = report_for([True] + [False] * 100, ["a"] + ["b"] * 100)
    rate = report["per_element"]["Ca"]["false_positive_rate"]
    assert rate["value"] == 0.5
    assert rate["support"] == 2
    assert report["physical_samples"] == 2


def test_disagreeing_replicates_are_fractional_and_duplication_adds_no_support():
    detections = [False, True, False, False, True, True]
    samples = ["a", "a", "b", "b", "c", "c"]
    original = report_for(detections, samples)
    duplicated = report_for(detections * 20, samples * 20)
    assert duplicated == original
    rate = original["per_element"]["Ca"]["false_positive_rate"]
    assert rate["value"] == 0.5
    assert rate["support"] == 3
    assert original["rate_interval"]["estimand"] == (
        "unweighted mean of within-material detection fractions"
    )


def test_additional_independent_materials_reduce_uncertainty():
    small = report_for([False] * 2, [f"material-{i}" for i in range(2)])
    large = report_for([False] * 100, [f"material-{i}" for i in range(100)])
    small_rate = small["per_element"]["Ca"]["false_positive_rate"]
    large_rate = large["per_element"]["Ca"]["false_positive_rate"]
    assert 0 < large_rate["ci95"][1] < small_rate["ci95"][1]
    assert large_rate["support"] == 100


def test_recall_and_fpr_use_separate_material_support():
    report = report_for(
        [True, False, False], ["positive", "negative", "unknown"], [[True], [False], [None]]
    )
    element = report["per_element"]["Ca"]
    assert element["recall"] == {"value": 1.0, "ci95": [0.0, 1.0], "support": 1}
    assert element["false_positive_rate"] == {"value": 0.0, "ci95": [0.0, 1.0], "support": 1}
    assert element["unknown_samples"] == 1
    assert element["roc_auc"] == 1.0


def test_unknown_and_empty_samples_produce_no_rate_or_interval():
    for report in (
        report_for([False], ["unknown"], [[None]]),
        report_for([], []),
    ):
        element = report["per_element"]["Ca"]
        assert element["recall"] == {"value": None, "ci95": None, "support": 0}
        assert element["false_positive_rate"] == {"value": None, "ci95": None, "support": 0}
        assert element["roc_auc"] is None


def test_interval_metadata_discloses_method_and_independence_assumption():
    report = report_for([False], ["material"])
    metadata = report["rate_interval"]
    assert metadata["method"] == "hoeffding_bounded_mean"
    assert metadata["confidence_level"] == 0.95
    assert metadata["scope"] == "pointwise_per_element_per_rate"
    assert metadata["unit"] == "physical_material"
    assert "independent physical materials" in " ".join(metadata["assumptions"])
    assert "assay uncertainty" in report["caveat"]


def test_legacy_bootstrap_options_cannot_change_analytic_interval():
    original = report_for([False, True], ["a", "b"])
    legacy = report_for([False, True], ["a", "b"], bootstrap_repeats=2, bootstrap_seed=123456)
    assert legacy == original


def test_conflicting_material_truth_is_rejected():
    with pytest.raises(ValueError, match="conflicting"):
        report_for([False, True], ["a", "a"], [[False], [True]])


@pytest.mark.parametrize("detection", [np.nan, -1.0, 0.5, 2.0])
def test_nonbinary_detections_cannot_satisfy_bounded_rate_contract(detection):
    with pytest.raises(ValueError, match="binary"):
        operational_report(np.zeros((1, 1)), np.array([[detection]]), [[False]], ["a"], ("Ca",))


def test_malformed_labels_and_row_alignment_are_rejected():
    with pytest.raises(ValueError, match="match"):
        operational_report(np.zeros((2, 1)), np.zeros((2, 1)), [[False]], ["a", "b"], ("Ca",))
    with pytest.raises(ValueError, match="boolean"):
        report_for([False], ["a"], [["unknown"]])
    with pytest.raises(ValueError, match="match"):
        operational_report(np.zeros((1, 1)), np.zeros((2, 1)), [[False]], ["a"], ("Ca",))
