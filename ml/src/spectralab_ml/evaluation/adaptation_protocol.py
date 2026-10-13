"""Evidence, leakage guards and paired metrics for locked instrument adaptation.

Input is a JSON manifest with ``schema_version=1``, unique ``class_order``,
``estimand`` (strict_presence or operational_above_threshold),
``locked_before_predictions=True``, development/test record lists and an explicit
``previously_seen`` registry. Every record carries measurement_id,
physical_sample_id, session_id, instrument_id, input_sha256 and every class label.
Optional family_id groups related materials without equating their label states;
previously_seen may include family_ids. Missing families never share a group.

Labels have state positive/negative/unknown. Strict known labels require basis
independent_presence/independent_absence. Operational labels use
independent_composition with a finite nonnegative value and matching units;
decision_policy[element]={units,negative_max,positive_min} fixes their meaning.
Known evidence requires source, source_sha256 and statement. Unknown labels with
basis unknown are explicit omissions, never zeros. Admission checks evidence
structure, not scientific truth or the chronology of a user's lock declaration.

No estimator is fitted here. Call guard_development_fit before every learned
transform, calibration or threshold selection. Store the admission hash before
prediction, then pass it back to evaluation to detect changes to the protocol.
General leakage guidance: https://scikit-learn.org/stable/common_pitfalls.html
Cluster bootstrap reference: CRAN ClusterBootstrap, clusbootglm documentation.
https://search.r-project.org/CRAN/refmans/ClusterBootstrap/html/clusbootglm.html
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections import defaultdict
from collections.abc import Mapping, Sequence
from numbers import Real
from typing import Any

import numpy as np

STATES = {"negative": 0, "positive": 1, "unknown": -1}
FIT_OPERATIONS = {"instrument_adaptation", "calibration", "threshold"}
SEEN_FIELDS = {
    "measurement_ids": "measurement_id",
    "physical_sample_ids": "physical_sample_id",
    "session_ids": "session_id",
    "input_sha256s": "input_sha256",
}
GROUP_FIELDS = ("physical_sample_id", "family_id", "session_id", "input_sha256")


def _digest(value: Any) -> str:
    try:
        raw = json.dumps(value, sort_keys=True, allow_nan=False, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise ValueError("Protocol must contain finite JSON values") from exc
    return hashlib.sha256(raw.encode()).hexdigest()


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Nonempty {field} is required")
    return value


def _hash(value: Any, field: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{64}", value):
        raise ValueError(f"Expected SHA-256 in {field}")
    return value


def _number(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, Real) or not math.isfinite(value):
        raise ValueError(f"Finite numeric {field} is required")
    if value < 0:
        raise ValueError(f"Nonnegative {field} is required")
    return float(value)


def _label(label: Any, element: str, manifest: Mapping) -> None:
    if not isinstance(label, Mapping) or label.get("state") not in STATES:
        raise ValueError(f"Explicit three-state label required for {element}")
    state, basis = label["state"], label.get("basis")
    if state == "unknown" and basis == "unknown":
        return
    for field in ("source", "statement"):
        _text(label.get(field), f"{element} evidence {field}")
    _hash(label.get("source_sha256"), f"{element} evidence source_sha256")
    if manifest["estimand"] == "strict_presence":
        allowed = {"positive": "independent_presence", "negative": "independent_absence"}
        if state not in allowed or allowed[state] != basis:
            raise ValueError("Strict presence requires independent presence/absence evidence")
    else:
        if basis != "independent_composition":
            raise ValueError("Operational labels require independent_composition evidence")
        policy = manifest["decision_policy"][element]
        value = _number(label.get("value"), f"{element} composition value")
        if label.get("units") != policy["units"]:
            raise ValueError("Composition and decision-policy units must match")
        expected = (
            "positive"
            if value >= policy["positive_min"]
            else "negative"
            if value <= policy["negative_max"]
            else "unknown"
        )
        if state != expected:
            raise ValueError("Composition label disagrees with the fixed decision policy")


def _clusters(records: Sequence[Mapping]) -> dict[str, str]:
    """Connected physical/family/session/checksum groups; no independent replicates."""
    parent = list(range(len(records)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    seen: dict[tuple[str, str], int] = {}
    for index, record in enumerate(records):
        for field in GROUP_FIELDS:
            if field not in record:
                continue
            key = field, record[field]
            if key in seen:
                parent[find(index)] = find(seen[key])
            else:
                seen[key] = index
    members: dict[int, list[str]] = defaultdict(list)
    for index, record in enumerate(records):
        members[find(index)].append(record["measurement_id"])
    names = {root: "cluster-" + _digest(sorted(ids))[:16] for root, ids in members.items()}
    return {record["measurement_id"]: names[find(i)] for i, record in enumerate(records)}


def validate_protocol(manifest: Mapping[str, Any]) -> dict[str, Any]:
    """Reject missing evidence, changed estimands and contaminated development/test splits."""
    if manifest.get("schema_version") != 1 or isinstance(manifest.get("schema_version"), bool):
        raise ValueError("Adaptation protocol schema must be 1")
    if manifest.get("locked_before_predictions") is not True:
        raise ValueError("Protocol must be locked before predictions")
    classes = manifest.get("class_order")
    if not isinstance(classes, list) or not classes or any(not isinstance(c, str) for c in classes):
        raise ValueError("Nonempty class_order is required")
    if len(set(classes)) != len(classes) or any(not c.strip() for c in classes):
        raise ValueError("Class order must contain unique nonempty elements")
    if manifest.get("estimand") not in {"strict_presence", "operational_above_threshold"}:
        raise ValueError("Unknown estimand; threshold-negative is not strict absence")
    if manifest["estimand"] == "operational_above_threshold":
        policies = manifest.get("decision_policy")
        if not isinstance(policies, Mapping) or set(policies) != set(classes):
            raise ValueError("Decision policy is required for every element")
        for element, policy in policies.items():
            if not isinstance(policy, Mapping):
                raise ValueError("Decision policy must be an object")
            _text(policy.get("units"), f"{element} decision-policy units")
            lower = _number(policy.get("negative_max"), "negative_max")
            upper = _number(policy.get("positive_min"), "positive_min")
            if lower >= upper:
                raise ValueError("Decision policy requires negative_max < positive_min")
    elif manifest.get("decision_policy"):
        raise ValueError("Strict presence cannot be defined by a composition threshold")

    development, test = manifest.get("development"), manifest.get("test")
    if not isinstance(development, list) or not isinstance(test, list) or not test:
        raise ValueError("Development list and nonempty held-out test list are required")
    records = development + test
    physical_labels = {}
    for record in records:
        if not isinstance(record, Mapping):
            raise ValueError("Each measurement must be an object")
        for field in ("measurement_id", "physical_sample_id", "session_id", "instrument_id"):
            _text(record.get(field), field)
        if "family_id" in record:
            _text(record["family_id"], "family_id")
        _hash(record.get("input_sha256"), "input_sha256")
        labels = record.get("labels")
        if not isinstance(labels, Mapping) or set(labels) != set(classes):
            raise ValueError("Every element needs an explicit label; missing is never negative")
        for element, label in labels.items():
            _label(label, element, manifest)
        states = tuple(labels[element]["state"] for element in classes)
        physical = record["physical_sample_id"]
        if physical in physical_labels and physical_labels[physical] != states:
            raise ValueError("Replicates have conflicting physical-sample labels")
        physical_labels[physical] = states
    ids = [record["measurement_id"] for record in records]
    if len(set(ids)) != len(ids):
        raise ValueError("Measurement IDs must be globally unique")
    for field in GROUP_FIELDS:
        if {r[field] for r in development if field in r} & {r[field] for r in test if field in r}:
            raise ValueError(f"Development/test overlap in {field}")
    previously_seen = manifest.get("previously_seen")
    if (
        not isinstance(previously_seen, Mapping)
        or not set(SEEN_FIELDS) <= set(previously_seen)
        or set(previously_seen) - set(SEEN_FIELDS) - {"family_ids"}
    ):
        raise ValueError("Explicit previously_seen registry with all identity fields required")
    for field, record_field in {**SEEN_FIELDS, "family_ids": "family_id"}.items():
        values = previously_seen.get(field, [])
        if not isinstance(values, list) or any(not isinstance(value, str) for value in values):
            raise ValueError(f"previously_seen.{field} must be a list of IDs")
        for value in values:
            if field == "input_sha256s":
                _hash(value, field)
            else:
                _text(value, field)
        if set(values) & {record[record_field] for record in test if record_field in record}:
            raise ValueError(f"Held-out test contains previously seen {record_field}")
    clusters = _clusters(records)
    return {
        "schema_version": 1,
        "status": "admitted",
        "estimand": manifest["estimand"],
        "protocol_hash": _digest(manifest),
        "split_hash": _digest(
            {
                k: [
                    {
                        field: r[field]
                        for field in (
                            "measurement_id",
                            "physical_sample_id",
                            "family_id",
                            "session_id",
                            "instrument_id",
                            "input_sha256",
                        )
                        if field in r
                    }
                    for r in manifest[k]
                ]
                for k in ("development", "test")
            }
        ),
        "development_ids": [r["measurement_id"] for r in development],
        "test_ids": [r["measurement_id"] for r in test],
        "clusters": clusters,
        "test_cluster_count": len({clusters[r["measurement_id"]] for r in test}),
        "evidence_scope": "Structural validation only; independent evidence must be reviewed.",
    }


def _locked(manifest: Mapping, expected_protocol_hash: str) -> dict:
    admission = validate_protocol(manifest)
    _hash(expected_protocol_hash, "expected_protocol_hash")
    if admission["protocol_hash"] != expected_protocol_hash:
        raise ValueError("Protocol changed after its lock")
    return admission


def guard_development_fit(
    manifest: Mapping[str, Any],
    measurement_ids: Sequence[str],
    operation: str,
    *,
    expected_protocol_hash: str,
) -> dict[str, Any]:
    """Audit an allowed fit set. Invoke before fitting; no test features or labels are accepted."""
    admission = _locked(manifest, expected_protocol_hash)
    if operation not in FIT_OPERATIONS:
        raise ValueError("Unknown fit operation")
    ids = list(measurement_ids)
    if not ids or len(ids) != len(set(ids)):
        raise ValueError("Nonempty unique fit measurement IDs are required")
    if not set(ids) <= set(admission["development_ids"]):
        raise ValueError("Fit must use development measurements only; test/unknown IDs forbidden")
    result = {
        "operation": operation,
        "protocol_hash": admission["protocol_hash"],
        "split_hash": admission["split_hash"],
        "measurement_ids": sorted(ids),
        "physical_sample_ids": sorted(
            {r["physical_sample_id"] for r in manifest["development"] if r["measurement_id"] in ids}
        ),
        "session_ids": sorted(
            {r["session_id"] for r in manifest["development"] if r["measurement_id"] in ids}
        ),
        "family_ids": sorted(
            {
                r["family_id"]
                for r in manifest["development"]
                if r["measurement_id"] in ids and "family_id" in r
            }
        ),
    }
    return {**result, "audit_hash": _digest(result)}


def _prepared(
    manifest: Mapping,
    detections: np.ndarray,
    measurement_ids: Sequence[str],
    expected_protocol_hash: str,
) -> tuple[dict, np.ndarray, np.ndarray, np.ndarray]:
    admission = _locked(manifest, expected_protocol_hash)
    if list(measurement_ids) != admission["test_ids"]:
        raise ValueError("Prediction measurement IDs must match the exact held-out test order")
    predicted = np.asarray(detections)
    classes = manifest["class_order"]
    if predicted.dtype != bool or predicted.shape != (len(manifest["test"]), len(classes)):
        raise ValueError("Aligned boolean detections required; raw scores are not detections")
    samples: dict[str, list[int]] = defaultdict(list)
    for index, record in enumerate(manifest["test"]):
        samples[record["physical_sample_id"]].append(index)
    rows = list(samples.values())
    rates = np.stack([predicted[indices].mean(axis=0) for indices in rows])
    labels = np.asarray(
        [
            [STATES[manifest["test"][indices[0]]["labels"][c]["state"]] for c in classes]
            for indices in rows
        ]
    )
    cluster_names = [
        admission["clusters"][manifest["test"][indices[0]]["measurement_id"]] for indices in rows
    ]
    lookup = {key: i for i, key in enumerate(sorted(set(cluster_names)))}
    groups = np.asarray([lookup[key] for key in cluster_names])
    return admission, rates, labels, groups


def _resamples(group_count: int, repeats: int, seed: int) -> np.ndarray:
    if isinstance(repeats, bool) or not isinstance(repeats, int) or repeats < 100:
        raise ValueError("At least 100 cluster bootstrap repeats are required")
    if isinstance(seed, bool) or not isinstance(seed, int) or seed < 0:
        raise ValueError("A nonnegative bootstrap seed is required")
    draws = np.random.default_rng(seed).integers(0, group_count, (repeats, group_count))
    return np.stack([np.bincount(draw, minlength=group_count) for draw in draws])


def _estimate(
    numerator: np.ndarray,
    denominator: np.ndarray,
    groups: np.ndarray,
    draws: np.ndarray,
    required_classes: tuple[np.ndarray, np.ndarray] | None = None,
) -> tuple[dict, np.ndarray]:
    n_clusters = draws.shape[1]
    num = np.bincount(groups, weights=numerator, minlength=n_clusters)
    den = np.bincount(groups, weights=denominator, minlength=n_clusters)
    support = int(np.count_nonzero(den))
    point = float(num.sum() / den.sum()) if den.sum() else None
    sampled_den = draws @ den
    valid = sampled_den > 0
    missing_classes = False
    if required_classes is not None:
        for labels in required_classes:
            counts = np.bincount(groups, weights=labels, minlength=n_clusters)
            missing_classes |= not bool(counts.sum())
            valid &= draws @ counts > 0
        if missing_classes:
            point = None
    sampled = np.full(len(draws), np.nan)
    sampled[valid] = (draws @ num)[valid] / sampled_den[valid]
    reason = (
        "both_positive_and_negative_labels_required"
        if missing_classes
        else "zero_denominator"
        if point is None
        else "fewer_than_two_supporting_clusters"
        if support < 2
        else "insufficient_valid_bootstrap_draws"
        if int(valid.sum()) < 100
        else "degenerate_cluster_bootstrap"
        if np.ptp(sampled[valid]) == 0
        else None
    )
    return {
        "estimate": point,
        "ci95": None if reason else np.quantile(sampled[valid], [0.025, 0.975]).tolist(),
        "ci_reason": reason,
        "numerator_sample_equivalents": float(num.sum()),
        "denominator_sample_equivalents": float(den.sum()),
        "supporting_clusters": support,
        "valid_bootstrap_draws": int(valid.sum()),
    }, sampled


def _report(
    manifest: Mapping,
    detections: np.ndarray,
    admission: dict,
    rates: np.ndarray,
    labels: np.ndarray,
    groups: np.ndarray,
    draws: np.ndarray,
    seed: int,
) -> tuple[dict, dict]:
    detections = np.asarray(detections)
    elements, distributions = {}, {}
    for column, element in enumerate(manifest["class_order"]):
        positive, negative, unknown = (labels[:, column] == value for value in (1, 0, -1))
        rate = rates[:, column]
        raw_states = np.asarray([STATES[r["labels"][element]["state"]] for r in manifest["test"]])
        metrics, sampled = {}, {}
        for name, num, den, required in (
            ("recall", rate * positive, positive, None),
            ("false_positive_rate", rate * negative, negative, None),
            ("known_label_precision", rate * positive, rate * ~unknown, (positive, negative)),
        ):
            metrics[name], sampled[name] = _estimate(num, den, groups, draws, required)
        elements[element] = {
            **metrics,
            "physical_positive_support": int(positive.sum()),
            "physical_negative_support": int(negative.sum()),
            "physical_unknown_support": int(unknown.sum()),
            "positive_cluster_support": len(set(groups[positive].tolist())),
            "negative_cluster_support": len(set(groups[negative].tolist())),
            "replicate_positive_support": int(np.sum(raw_states == 1)),
            "replicate_negative_support": int(np.sum(raw_states == 0)),
            "replicate_unknown_support": int(np.sum(raw_states == -1)),
            "unknown_label_prediction_count": int(
                np.sum(detections[:, column] & (raw_states == -1))
            ),
            "unknown_prediction_sample_equivalents": float(np.sum(rate * unknown)),
            "tp_sample_equivalents": float(np.sum(rate * positive)),
            "fp_sample_equivalents": float(np.sum(rate * negative)),
            "tn_sample_equivalents": float(np.sum((1 - rate) * negative)),
            "fn_sample_equivalents": float(np.sum((1 - rate) * positive)),
        }
        distributions[element] = sampled
    return {
        "schema_version": 1,
        "estimand": manifest["estimand"],
        "protocol_hash": admission["protocol_hash"],
        "split_hash": admission["split_hash"],
        "test_measurement_ids": admission["test_ids"],
        "class_order": list(manifest["class_order"]),
        "physical_samples": len(rates),
        "replicate_spectra": len(detections),
        "independent_clusters": draws.shape[1],
        "per_element": elements,
        "uncertainty": {
            "method": "paired physical/family/session/checksum-cluster percentile bootstrap",
            "repeats": len(draws),
            "seed": seed,
            "confidence_level": 0.95,
            "weighting": "Each physical sample has unit weight; average its replicate detections.",
            "null_interval_policy": "<2 supporting clusters, <100 valid draws or degenerate draws",
        },
        "limitations": [
            "Unknown labels are excluded, never counted as negatives.",
            "Precision is conditional on known labels and requires positive and negative support.",
            "Intervals condition on sampled clusters and fixed fitted models; not simultaneous.",
            "A zero observed false-positive count is not proof of a zero population rate.",
            "Operational below-threshold labels do not establish elemental absence.",
        ],
    }, distributions


def evaluate_detections(
    manifest: Mapping[str, Any],
    detections: np.ndarray,
    *,
    measurement_ids: Sequence[str],
    expected_protocol_hash: str,
    bootstrap_repeats: int = 2000,
    bootstrap_seed: int = 20261013,
) -> dict[str, Any]:
    """Physical-sample weighted metrics on explicit, locked test detections only."""
    admission, rates, labels, groups = _prepared(
        manifest, detections, measurement_ids, expected_protocol_hash
    )
    draws = _resamples(admission["test_cluster_count"], bootstrap_repeats, bootstrap_seed)
    return _report(manifest, detections, admission, rates, labels, groups, draws, bootstrap_seed)[0]


def compare_detections(
    manifest: Mapping[str, Any],
    baseline_detections: np.ndarray,
    adapted_detections: np.ndarray,
    *,
    measurement_ids: Sequence[str],
    expected_protocol_hash: str,
    adaptation_audits: Sequence[Mapping[str, Any]],
    bootstrap_repeats: int = 2000,
    bootstrap_seed: int = 20261013,
) -> dict[str, Any]:
    """Paired adaptation-minus-baseline changes; negative FPR delta means fewer false positives."""
    if not adaptation_audits:
        raise ValueError("Adaptation requires development-only fit audits")
    audits = []
    for audit in adaptation_audits:
        expected = guard_development_fit(
            manifest,
            audit.get("measurement_ids", []),
            audit.get("operation", ""),
            expected_protocol_hash=expected_protocol_hash,
        )
        if dict(audit) != expected:
            raise ValueError("Fit audit differs from the locked development-only receipt")
        audits.append(expected)
    reports, samples = [], []
    for detections in (baseline_detections, adapted_detections):
        admission, rates, labels, groups = _prepared(
            manifest, detections, measurement_ids, expected_protocol_hash
        )
        draws = _resamples(admission["test_cluster_count"], bootstrap_repeats, bootstrap_seed)
        report, sampled = _report(
            manifest, detections, admission, rates, labels, groups, draws, bootstrap_seed
        )
        reports.append(report)
        samples.append(sampled)
    differences = {}
    for element in manifest["class_order"]:
        differences[element] = {}
        for metric in ("recall", "false_positive_rate", "known_label_precision"):
            before, after = [report["per_element"][element][metric] for report in reports]
            paired = samples[1][element][metric] - samples[0][element][metric]
            valid = np.isfinite(paired)
            point = (
                after["estimate"] - before["estimate"]
                if before["estimate"] is not None and after["estimate"] is not None
                else None
            )
            support = min(before["supporting_clusters"], after["supporting_clusters"])
            reason = (
                "undefined_component_metric"
                if point is None
                else "fewer_than_two_supporting_clusters"
                if support < 2
                else "insufficient_valid_bootstrap_draws"
                if int(valid.sum()) < 100
                else "degenerate_cluster_bootstrap"
                if np.ptp(paired[valid]) == 0
                else None
            )
            differences[element][metric] = {
                "estimate": point,
                "ci95": None if reason else np.quantile(paired[valid], [0.025, 0.975]).tolist(),
                "ci_reason": reason,
                "valid_bootstrap_draws": int(valid.sum()),
                "supporting_clusters": support,
            }
    return {
        "schema_version": 1,
        "protocol_hash": expected_protocol_hash,
        "baseline": reports[0],
        "adapted": reports[1],
        "adaptation_minus_baseline": differences,
        "fit_audits": audits,
        "comparison": "Same test observations, label mask, weights and cluster bootstrap draws.",
        "decision_scope": "Descriptive paired comparison, not selection on held-out test results.",
    }
