from __future__ import annotations

import copy
import hashlib
import json

import numpy as np
import pytest
from spectralab_ml.evaluation.adaptation_protocol import (
    compare_detections,
    evaluate_detections,
    guard_development_fit,
    validate_protocol,
)


def sha(value):
    return hashlib.sha256(value.encode()).hexdigest()


def label(state):
    if state == "unknown":
        return {"state": state, "basis": "unknown"}
    return {
        "state": state,
        "basis": "independent_presence" if state == "positive" else "independent_absence",
        "source": "https://example.org/independent-certificate",
        "source_sha256": sha("independent certificate"),
        "statement": "Synthetic unit-test evidence; not a claim about a real material",
    }


def record(index, state, *, physical=None, session=None):
    return {
        "measurement_id": f"m{index}",
        "physical_sample_id": physical or f"p{index}",
        "session_id": session or f"s{index}",
        "instrument_id": "test-instrument",
        "input_sha256": sha(f"spectrum {index}"),
        "labels": {"Na": label(state), "H": label("unknown")},
    }


@pytest.fixture
def protocol():
    return {
        "schema_version": 1,
        "class_order": ["Na", "H"],
        "estimand": "strict_presence",
        "locked_before_predictions": True,
        "development": [record("dev", "positive")],
        "test": [
            record(0, "positive"),
            record(1, "positive"),
            record(2, "negative"),
            record(3, "negative"),
            record(4, "unknown"),
        ],
        "previously_seen": {
            "measurement_ids": [],
            "physical_sample_ids": [],
            "session_ids": [],
            "input_sha256s": [],
        },
    }


def evaluation(protocol, detections, **kwargs):
    return evaluate_detections(
        protocol,
        np.asarray(detections, dtype=bool),
        measurement_ids=[r["measurement_id"] for r in protocol["test"]],
        expected_protocol_hash=validate_protocol(protocol)["protocol_hash"],
        bootstrap_repeats=1000,
        **kwargs,
    )


def test_evidence_is_required_and_absence_is_not_inferred_from_zero_or_unknown(protocol):
    for changed in (
        {"state": "negative", "basis": "unknown"},
        {**label("negative"), "basis": "below_lod", "value": 0},
        {**label("negative"), "source_sha256": ""},
    ):
        altered = copy.deepcopy(protocol)
        altered["test"][2]["labels"]["Na"] = changed
        with pytest.raises(ValueError):
            validate_protocol(altered)
    del protocol["test"][0]["labels"]["H"]
    with pytest.raises(ValueError, match="explicit label"):
        validate_protocol(protocol)


def test_operational_estimand_requires_fixed_independent_composition_policy(protocol):
    protocol["estimand"] = "operational_above_threshold"
    protocol["decision_policy"] = {
        c: {"units": "mass_fraction", "negative_max": 0.01, "positive_min": 0.1}
        for c in protocol["class_order"]
    }
    values = [0.1, 0.2, 0, 0.01, 0.05]
    for measurement, value in zip(protocol["test"], values, strict=True):
        measurement["labels"]["Na"] = {
            **label("positive"),
            "state": measurement["labels"]["Na"]["state"],
            "basis": "independent_composition",
            "value": value,
            "units": "mass_fraction",
        }
    protocol["development"][0]["labels"]["Na"] = copy.deepcopy(protocol["test"][0]["labels"]["Na"])
    assert validate_protocol(protocol)["estimand"] == "operational_above_threshold"
    for field, value in [
        ("value", -1),
        ("value", float("nan")),
        ("value", True),
        ("units", "ppm"),
        ("state", "negative"),
    ]:
        altered = copy.deepcopy(protocol)
        altered["test"][0]["labels"]["Na"][field] = value
        with pytest.raises(ValueError):
            validate_protocol(altered)
    protocol["decision_policy"]["Na"]["negative_max"] = 0.1
    with pytest.raises(ValueError, match="negative_max < positive_min"):
        validate_protocol(protocol)


@pytest.mark.parametrize("field", ["physical_sample_id", "session_id", "input_sha256"])
def test_development_test_overlap_is_rejected_by_every_group_identity(protocol, field):
    protocol["test"][0][field] = protocol["development"][0][field]
    with pytest.raises(ValueError, match="Development/test overlap"):
        validate_protocol(protocol)


def test_optional_family_guards_related_materials_and_previous_exposure(protocol):
    original_split = validate_protocol(protocol)["split_hash"]
    protocol["development"][0]["family_id"] = "development-series"
    protocol["test"][0]["family_id"] = "related-series"
    protocol["test"][2]["family_id"] = "related-series"
    admission = validate_protocol(protocol)
    assert admission["split_hash"] != original_split
    assert admission["clusters"]["m0"] == admission["clusters"]["m2"]
    assert admission["test_cluster_count"] == 4
    assert admission["clusters"]["m1"] != admission["clusters"]["m3"]
    receipt = guard_development_fit(
        protocol, ["mdev"], "threshold", expected_protocol_hash=admission["protocol_hash"]
    )
    assert receipt["family_ids"] == ["development-series"]
    protocol["test"][0]["family_id"] = "development-series"
    with pytest.raises(ValueError, match="Development/test overlap in family_id"):
        validate_protocol(protocol)
    protocol["test"][0]["family_id"] = "related-series"
    protocol["previously_seen"]["family_ids"] = ["related-series"]
    with pytest.raises(ValueError, match="previously seen family_id"):
        validate_protocol(protocol)


@pytest.mark.parametrize("family", [None, "", " ", 3, []])
def test_present_family_must_be_explicit_nonempty_string(protocol, family):
    protocol["test"][0]["family_id"] = family
    with pytest.raises(ValueError, match="family_id"):
        validate_protocol(protocol)


def test_family_clusters_change_bootstrap_support_not_physical_sample_counts(protocol):
    protocol["test"][2]["family_id"] = "wmb24"
    protocol["test"][3]["family_id"] = "wmb24"
    report = evaluation(protocol, [[1, 0], [0, 0], [1, 0], [0, 0], [0, 0]])
    sodium = report["per_element"]["Na"]
    assert sodium["physical_negative_support"] == 2
    assert sodium["negative_cluster_support"] == 1
    assert sodium["false_positive_rate"]["estimate"] == 0.5
    assert sodium["false_positive_rate"]["ci95"] is None
    assert sodium["false_positive_rate"]["ci_reason"] == "fewer_than_two_supporting_clusters"


@pytest.mark.parametrize(
    "field,record_field",
    [
        ("measurement_ids", "measurement_id"),
        ("physical_sample_ids", "physical_sample_id"),
        ("session_ids", "session_id"),
        ("input_sha256s", "input_sha256"),
    ],
)
def test_previously_seen_test_observations_cannot_be_renamed_into_a_clean_test(
    protocol,
    field,
    record_field,
):
    protocol["previously_seen"][field] = [protocol["test"][0][record_field]]
    with pytest.raises(ValueError, match="previously seen"):
        validate_protocol(protocol)


def test_identity_registry_and_lock_are_explicit_and_test_order_is_checked(protocol):
    manifest_hash = validate_protocol(protocol)["protocol_hash"]
    altered = copy.deepcopy(protocol)
    altered["previously_seen"] = {}
    with pytest.raises(ValueError, match="previously_seen registry"):
        validate_protocol(altered)
    altered = copy.deepcopy(protocol)
    altered["locked_before_predictions"] = False
    with pytest.raises(ValueError, match="locked"):
        validate_protocol(altered)
    ids = [r["measurement_id"] for r in protocol["test"]]
    with pytest.raises(ValueError, match="exact held-out test order"):
        evaluate_detections(
            protocol,
            np.zeros((5, 2), bool),
            measurement_ids=ids[::-1],
            expected_protocol_hash=manifest_hash,
        )
    protocol["test"][0]["labels"]["Na"]["statement"] = "Changed evidence"
    with pytest.raises(ValueError, match="changed after its lock"):
        evaluate_detections(
            protocol,
            np.zeros((5, 2), bool),
            measurement_ids=ids,
            expected_protocol_hash=manifest_hash,
        )


def test_fit_guard_allows_only_development_and_receipts_bind_operation_and_protocol(protocol):
    manifest_hash = validate_protocol(protocol)["protocol_hash"]
    for operation in ("instrument_adaptation", "calibration", "threshold"):
        receipt = guard_development_fit(
            protocol, ["mdev"], operation, expected_protocol_hash=manifest_hash
        )
        assert receipt["measurement_ids"] == ["mdev"]
        assert receipt["physical_sample_ids"] == ["pdev"]
        assert receipt["operation"] == operation
        for ids in (["m0"], ["mdev", "m0"], ["new"], [], ["mdev", "mdev"]):
            with pytest.raises(ValueError):
                guard_development_fit(
                    protocol, ids, operation, expected_protocol_hash=manifest_hash
                )


def test_unknowns_never_enter_false_positives_and_metrics_have_explicit_support(protocol):
    prediction = [[1, 1], [0, 1], [1, 1], [0, 1], [1, 1]]
    report = evaluation(protocol, prediction)
    na, hydrogen = report["per_element"]["Na"], report["per_element"]["H"]
    assert na["recall"]["estimate"] == 0.5
    assert na["false_positive_rate"]["estimate"] == 0.5
    assert na["known_label_precision"]["estimate"] == 0.5
    assert na["fp_sample_equivalents"] == 1
    assert na["physical_positive_support"] == na["physical_negative_support"] == 2
    assert na["physical_unknown_support"] == na["unknown_label_prediction_count"] == 1
    assert hydrogen["physical_negative_support"] == 0
    assert hydrogen["unknown_label_prediction_count"] == 5
    for metric in ("recall", "false_positive_rate", "known_label_precision"):
        assert hydrogen[metric]["estimate"] is None
        assert hydrogen[metric]["ci95"] is None
    assert na["false_positive_rate"]["ci95"] == [0.0, 1.0]
    json.dumps(report, allow_nan=False)


def test_unequal_replicates_get_equal_physical_weight_and_duplication_does_not_change_ci(protocol):
    protocol["test"] = [
        record("a1", "positive", physical="a", session="a"),
        record("a2", "positive", physical="a", session="a"),
        record("a3", "positive", physical="a", session="a"),
        record("b", "positive"),
    ]
    prediction = np.array([[1, 0], [1, 0], [1, 0], [0, 0]], dtype=bool)
    first = evaluation(protocol, prediction)
    assert first["per_element"]["Na"]["recall"]["estimate"] == 0.5
    assert first["per_element"]["Na"]["known_label_precision"]["estimate"] is None
    duplicated = copy.deepcopy(protocol)
    for entry in protocol["test"]:
        duplicated["test"].append({**entry, "measurement_id": entry["measurement_id"] + "copy"})
    second = evaluation(duplicated, np.concatenate([prediction, prediction]))
    assert first["per_element"]["Na"]["recall"] == second["per_element"]["Na"]["recall"]
    assert first["physical_samples"] == second["physical_samples"] == 2
    assert second["replicate_spectra"] == 8


def test_connected_session_and_sample_clusters_are_not_treated_as_independent(protocol):
    protocol["test"] = [
        record("a1", "positive", physical="a", session="one"),
        record("a2", "positive", physical="a", session="two"),
        record("b", "positive", session="two"),
    ]
    assert validate_protocol(protocol)["test_cluster_count"] == 1
    report = evaluation(protocol, [[1, 0], [1, 0], [0, 0]])
    metric = report["per_element"]["Na"]["recall"]
    assert metric["estimate"] == 0.5
    assert metric["ci95"] is None
    assert metric["ci_reason"] == "fewer_than_two_supporting_clusters"
    protocol["test"][1]["labels"]["Na"] = label("negative")
    with pytest.raises(ValueError, match="conflicting physical-sample labels"):
        validate_protocol(protocol)


def test_zero_false_positives_does_not_manufacture_zero_width_population_interval(protocol):
    report = evaluation(protocol, np.zeros((5, 2), bool))
    fpr = report["per_element"]["Na"]["false_positive_rate"]
    assert fpr["estimate"] == 0
    assert fpr["ci95"] is None
    assert fpr["ci_reason"] == "degenerate_cluster_bootstrap"
    assert report["per_element"]["Na"]["known_label_precision"]["estimate"] is None


def test_comparison_is_paired_preserves_unknowns_and_validates_fit_receipts(protocol):
    manifest_hash = validate_protocol(protocol)["protocol_hash"]
    audit = guard_development_fit(
        protocol, ["mdev"], "threshold", expected_protocol_hash=manifest_hash
    )
    baseline = np.array([[1, 0], [0, 0], [1, 0], [1, 0], [1, 1]], dtype=bool)
    adapted = np.array([[1, 0], [1, 0], [0, 0], [1, 0], [1, 1]], dtype=bool)
    arguments = {
        "measurement_ids": [r["measurement_id"] for r in protocol["test"]],
        "expected_protocol_hash": manifest_hash,
        "adaptation_audits": [audit],
        "bootstrap_repeats": 1000,
    }
    result = compare_detections(protocol, baseline, adapted, **arguments)
    difference = result["adaptation_minus_baseline"]["Na"]
    assert difference["recall"]["estimate"] == 0.5
    assert difference["false_positive_rate"]["estimate"] == -0.5
    assert difference["false_positive_rate"]["ci95"] == [-1.0, 0.0]
    assert result["adaptation_minus_baseline"]["H"]["recall"]["estimate"] is None
    assert result == compare_detections(protocol, baseline, adapted, **arguments)
    json.dumps(result, allow_nan=False)
    arguments["adaptation_audits"] = []
    with pytest.raises(ValueError, match="fit audits"):
        compare_detections(protocol, baseline, adapted, **arguments)
    arguments["adaptation_audits"] = [{**audit, "operation": "calibration"}]
    with pytest.raises(ValueError, match="differs"):
        compare_detections(protocol, baseline, adapted, **arguments)


def test_raw_scores_and_malformed_shapes_are_not_silently_thresholded(protocol):
    args = {
        "measurement_ids": [r["measurement_id"] for r in protocol["test"]],
        "expected_protocol_hash": validate_protocol(protocol)["protocol_hash"],
    }
    for predictions in (np.ones((5, 2)), np.zeros((4, 2), bool), np.zeros((5, 3), bool)):
        with pytest.raises(ValueError, match="boolean detections"):
            evaluate_detections(protocol, predictions, **args)
    with pytest.raises(ValueError, match="bootstrap repeats"):
        evaluate_detections(protocol, np.zeros((5, 2), bool), bootstrap_repeats=2, **args)
