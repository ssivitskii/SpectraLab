"""Preregistered development comparison with disjoint fit/calibrate/tune clusters."""

from __future__ import annotations

import hashlib
import json
import platform
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path
from time import perf_counter
from uuid import uuid4

import numpy as np
from sklearn.model_selection import StratifiedGroupKFold

from spectralab_ml.data.chemcam_measurements import read_chemcam_ccs
from spectralab_ml.data.reference import ReferenceCatalog
from spectralab_ml.evaluation.campaign_shift_v2 import connected_groups
from spectralab_ml.evaluation.instrument_selection import select_operational_thresholds
from spectralab_ml.evaluation.real_validation import interpolate_observed
from spectralab_ml.models.line_adapter_v3 import (
    BANDS,
    CLASSES,
    GRID,
    QUALIFICATION,
    VARIANTS,
    LineAdapterV3,
    extract_features,
    feature_plan,
    physical_weights,
    validate_decision_policy,
    validate_labels,
)

SCOPE = "nested_grouped_development_cv_not_independent_test"
PRIMARY = "continuum-snr-ion"
DEFAULT_PLAN = "data/reference/development_v4_source_plan.json"
CANDIDATES = (0.01, 0.1, 1.0)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n")


def record_labels(records: list[dict]) -> np.ndarray:
    if not records or len({r["measurement_id"] for r in records}) != len(records):
        raise ValueError("Unique development measurement IDs required")
    for r in records:
        if r.get("role") != "development" or any(
            not isinstance(r.get(k), str) or not r[k]
            for k in ("physical_sample_id", "family_id", "session_id", "input_sha256")
        ):
            raise ValueError("Explicit development role and complete grouping metadata required")
    labels = np.asarray(
        [
            [
                {"negative": 0, "positive": 1, "unknown": -1}[r["labels"][c]["state"]]
                for c in CLASSES
            ]
            for r in records
        ]
    )
    return validate_labels(labels, [r["physical_sample_id"] for r in records])


def assert_partition(
    groups: np.ndarray, parts: dict[str, np.ndarray], expected: np.ndarray
) -> None:
    combined = np.concatenate(list(parts.values()))
    if sorted(combined.tolist()) != sorted(expected.tolist()) or len(set(combined)) != len(
        combined
    ):
        raise ValueError("Partitions must cover their parent exactly once")
    names = list(parts)
    for i, first in enumerate(names):
        for second in names[i + 1 :]:
            if set(groups[parts[first]]) & set(groups[parts[second]]):
                raise ValueError(
                    "Related physical/family/session/checksum groups crossed partitions"
                )


def outer_partitions(records: list[dict], *, seed: int, count: int = 5) -> list[dict]:
    """Campaign-stratified outer split never inspects element response labels."""
    groups = np.asarray(connected_groups(records))
    campaigns = np.asarray([r["campaign"] for r in records])
    all_rows = np.arange(len(records))
    splitter = StratifiedGroupKFold(n_splits=count, shuffle=True, random_state=seed)
    result = []
    for train, test in splitter.split(all_rows, campaigns, groups):
        part = {"outer_fit": train, "outer_validation": test}
        assert_partition(groups, part, all_rows)
        result.append(part)
    return result


def inner_partition(records: list[dict], indices: np.ndarray, *, seed: int) -> tuple[dict, int]:
    """First seeded metadata-supported 60/20/20 split; no spectra or score search."""
    labels = record_labels(records)
    groups = np.asarray(connected_groups(records))
    unique = np.unique(groups[indices])
    if len(unique) < 10:
        raise ValueError("Insufficient connected groups for fit/calibrate/tune")
    rng = np.random.default_rng(seed)
    n = max(2, round(0.2 * len(unique)))
    for attempt in range(10000):
        order = rng.permutation(unique)
        part = {
            "fit": indices[np.isin(groups[indices], order[2 * n :])],
            "calibrate": indices[np.isin(groups[indices], order[:n])],
            "tune": indices[np.isin(groups[indices], order[n : 2 * n])],
        }
        if all(
            len(set(groups[selected][labels[selected, k] == state])) >= 2
            for selected in part.values()
            for k in range(4)
            for state in (0, 1)
        ):
            assert_partition(groups, part, indices)
            return part, attempt
    raise ValueError("No supported disjoint calibration/tuning partition; study not run")


def _ratio(num: float, den: float) -> float | None:
    return float(num / den) if den > 0 else None


def reliability(y: np.ndarray, p: np.ndarray, weights: np.ndarray, bins: int = 10) -> dict:
    if (
        y.ndim != 1
        or p.shape != y.shape
        or weights.shape != y.shape
        or not np.isin(y, [-1, 0, 1]).all()
        or not np.isfinite(weights).all()
        or np.any(weights < 0)
        or type(bins) is not int
        or bins < 1
    ):
        raise ValueError("Aligned three-state labels and nonnegative weights required")
    known = y >= 0
    y, p, w = y[known], p[known], weights[known]
    if not len(y) or w.sum() == 0:
        return {"brier": None, "ece": None, "bins": []}
    if not np.isfinite(p).all() or np.any((p < 0) | (p > 1)):
        raise ValueError("Finite probabilities in [0,1] required")
    cells = np.minimum((p * bins).astype(int), bins - 1)
    rows, ece = [], 0.0
    for b in range(bins):
        chosen = cells == b
        mass = float(w[chosen].sum())
        mean = _ratio(float(np.sum(w[chosen] * p[chosen])), mass)
        frequency = _ratio(float(np.sum(w[chosen] * y[chosen])), mass)
        if mass:
            ece += mass / w.sum() * abs(mean - frequency)
        rows.append(
            {
                "lower": b / bins,
                "upper": (b + 1) / bins,
                "physical_weight": mass,
                "mean_probability": mean,
                "positive_fraction": frequency,
            }
        )
    return {"brier": float(np.sum(w * (p - y) ** 2) / w.sum()), "ece": float(ece), "bins": rows}


def class_metrics(y: np.ndarray, detected: np.ndarray, accepted: np.ndarray, w: np.ndarray) -> dict:
    positive, negative, known = y == 1, y == 0, y >= 0
    tp, fp = (
        float(w[positive & detected & accepted].sum()),
        float(w[negative & detected & accepted].sum()),
    )
    fn = float(w[positive & ~detected & accepted].sum())
    tn = float(w[negative & ~detected & accepted].sum())
    pos, neg = float(w[positive].sum()), float(w[negative].sum())
    result = {
        "known_physical_weight": pos + neg,
        "positive_physical_weight": pos,
        "negative_physical_weight": neg,
        "unknown_label_physical_weight": float(w[~known].sum()),
        "coverage_all": _ratio(float(w[accepted].sum()), float(w.sum())),
        "coverage_known": _ratio(tp + fn + fp + tn, pos + neg),
        "positive_coverage": _ratio(tp + fn, pos),
        "negative_coverage": _ratio(fp + tn, neg),
        "accepted_true_positive_weight": tp,
        "accepted_false_positive_weight": fp,
        "accepted_false_negative_weight": fn,
        "accepted_true_negative_weight": tn,
        "selective_recall": _ratio(tp, tp + fn),
        "selective_fpr": _ratio(fp, fp + tn),
        "selective_fnr": _ratio(fn, tp + fn),
        "positive_capture_including_refusals": _ratio(tp, pos),
        "false_positive_risk_all_negatives": _ratio(fp, neg),
        "accepted_false_negative_risk_all_positives": _ratio(fn, pos),
        "refused_positive_weight": pos - tp - fn,
        "refused_negative_weight": neg - fp - tn,
        "unknown_truth_acceptance": _ratio(
            float(w[~known & accepted].sum()), float(w[~known].sum())
        ),
    }
    result["selective_balanced_accuracy"] = (
        (result["selective_recall"] + 1 - result["selective_fpr"]) / 2
        if tp + fn > 0 and fp + tn > 0
        else None
    )
    return result


def select_refusal_policy(
    p: np.ndarray, snr: np.ndarray, y: np.ndarray, ids: list[str], thresholds: np.ndarray
) -> tuple[dict, dict]:
    validate_labels(y, ids)
    if (
        p.shape != y.shape
        or snr.shape != y.shape
        or not np.isfinite(p).all()
        or not np.isfinite(snr).all()
        or np.any((p < 0) | (p > 1))
        or np.any(snr < 0)
        or np.asarray(thresholds).shape != (4,)
        or not np.isfinite(thresholds).all()
    ):
        raise ValueError("Finite aligned tuning scores/evidence required")
    weights = physical_weights(ids)
    chosen, reports = {}, {}
    for k, c in enumerate(CLASSES):
        candidates = []
        detected = p[:, k] >= thresholds[k]
        for minimum_snr in (0.0, 2.0, 5.0, 10.0):
            for margin in (0.0, 0.05, 0.1, 0.2):
                accepted = (snr[:, k] >= minimum_snr) & (abs(p[:, k] - thresholds[k]) >= margin)
                m = class_metrics(y[:, k], detected, accepted, weights)
                eligible = (
                    m["selective_fpr"] is not None
                    and m["selective_recall"] is not None
                    and m["selective_fpr"] <= 0.05 + 1e-12
                    and m["coverage_known"] >= 0.5 - 1e-12
                )
                candidates.append(
                    {
                        "minimum_snr": minimum_snr,
                        "margin": margin,
                        "eligible": eligible,
                        "metrics": m,
                    }
                )
        eligible = [c for c in candidates if c["eligible"]]
        if not eligible:
            raise ValueError("Accept-all operational policy should be a feasible fallback")
        selected = max(
            eligible,
            key=lambda q: (
                q["metrics"]["selective_balanced_accuracy"],
                q["metrics"]["coverage_known"],
                -q["minimum_snr"],
                -q["margin"],
            ),
        )
        chosen[c] = {k: selected[k] for k in ("minimum_snr", "margin")}
        reports[c] = {"selected": selected, "candidates": candidates}
    return chosen, reports


def evaluate_oof(
    records: list[dict],
    y: np.ndarray,
    raw: np.ndarray,
    p: np.ndarray,
    detected: np.ndarray,
    accepted: np.ndarray,
    *,
    seed: int,
) -> dict:
    ids = [r["physical_sample_id"] for r in records]
    validate_labels(y, ids)
    w = physical_weights(ids)
    groups = np.asarray(connected_groups(records))
    unique = np.unique(groups)
    rng = np.random.default_rng(seed)
    multiplicities = rng.multinomial(len(unique), np.full(len(unique), 1 / len(unique)), size=1000)
    group_position = {g: i for i, g in enumerate(unique)}
    columns = np.asarray([group_position[g] for g in groups])
    result = {
        "scope": SCOPE,
        "independent_final_test": False,
        "weighting": "Each physical material totals one; linked clusters resampled for CI",
        "bootstrap": {
            "clusters": len(unique),
            "repeats": 1000,
            "seed": seed,
            "method": (
                "percentile 95% connected-cluster bootstrap of fixed OOF predictions; no refit"
            ),
        },
        "per_class": {},
        "by_campaign": {},
    }
    for k, c in enumerate(CLASSES):
        point = {
            "raw_score_reliability_diagnostic": reliability(y[:, k], raw[:, k], w),
            "calibrated_reliability": reliability(y[:, k], p[:, k], w),
            "all_answers": class_metrics(y[:, k], detected[:, k], np.ones(len(y), bool), w),
            "selective": class_metrics(y[:, k], detected[:, k], accepted[:, k], w),
        }
        samples = {
            m: []
            for m in (
                "brier",
                "coverage_known",
                "selective_fpr",
                "selective_recall",
                "positive_capture_including_refusals",
            )
        }
        for counts in multiplicities:
            bw = w * counts[columns]
            m = class_metrics(y[:, k], detected[:, k], accepted[:, k], bw)
            known = y[:, k] >= 0
            m["brier"] = _ratio(
                float(np.sum(bw[known] * (p[known, k] - y[known, k]) ** 2)), float(bw[known].sum())
            )
            for key in samples:
                if m[key] is not None:
                    samples[key].append(m[key])
        point["confidence_intervals"] = {
            key: {
                "lower": float(np.quantile(v, 0.025)),
                "upper": float(np.quantile(v, 0.975)),
                "valid_repeats": len(v),
            }
            if v
            else None
            for key, v in samples.items()
        }
        result["per_class"][c] = point
    for campaign in sorted({r["campaign"] for r in records}):
        mask = np.asarray([r["campaign"] == campaign for r in records])
        result["by_campaign"][str(campaign)] = {
            c: {
                "calibrated_reliability": reliability(y[mask, k], p[mask, k], w[mask]),
                "selective": class_metrics(
                    y[mask, k], detected[mask, k], accepted[mask, k], w[mask]
                ),
            }
            for k, c in enumerate(CLASSES)
        }

    def complete_mean(values):
        return float(np.mean(values)) if all(v is not None for v in values) else None

    result["macro"] = {
        "raw_brier": complete_mean(
            [q["raw_score_reliability_diagnostic"]["brier"] for q in result["per_class"].values()]
        ),
        "calibrated_brier": complete_mean(
            [q["calibrated_reliability"]["brier"] for q in result["per_class"].values()]
        ),
        "calibrated_ece": complete_mean(
            [q["calibrated_reliability"]["ece"] for q in result["per_class"].values()]
        ),
        **{
            key: complete_mean([q["selective"][key] for q in result["per_class"].values()])
            for key in (
                "coverage_known",
                "selective_fpr",
                "selective_recall",
                "positive_capture_including_refusals",
            )
        },
    }
    return result


def paired_brier_difference(
    records: list[dict], y: np.ndarray, before: np.ndarray, after: np.ndarray, *, seed: int
) -> dict:
    """Matched linked-cluster interval; negative delta means lower Brier after."""
    weights = physical_weights([r["physical_sample_id"] for r in records])
    groups = np.asarray(connected_groups(records))
    unique, inverse = np.unique(groups, return_inverse=True)
    if before.shape != y.shape or after.shape != y.shape:
        raise ValueError("Aligned paired probabilities required")
    difference = (after - y) ** 2 - (before - y) ** 2

    def statistic(w):
        return [
            _ratio(
                float(np.sum(w[y[:, k] >= 0] * difference[y[:, k] >= 0, k])),
                float(w[y[:, k] >= 0].sum()),
            )
            for k in range(4)
        ]

    point = statistic(weights)
    rng = np.random.default_rng(seed)
    samples = [
        statistic(weights * counts[inverse])
        for counts in rng.multinomial(len(unique), np.full(len(unique), 1 / len(unique)), size=1000)
    ]
    values = {c: [row[k] for row in samples if row[k] is not None] for k, c in enumerate(CLASSES)}
    values["macro"] = [float(np.mean(row)) for row in samples if all(v is not None for v in row)]
    points = dict(zip(CLASSES, point, strict=True))
    points["macro"] = float(np.mean(point)) if all(v is not None for v in point) else None
    return {
        "definition": "after minus before Brier; equal physical weight, unknown truth excluded",
        "method": "paired connected-cluster percentile 95% bootstrap of fixed OOF predictions",
        "repeats": 1000,
        "seed": seed,
        "differences": {
            c: {
                "point": points[c],
                "lower": float(np.quantile(v, 0.025)) if v else None,
                "upper": float(np.quantile(v, 0.975)) if v else None,
                "valid_repeats": len(v),
            }
            for c, v in values.items()
        },
    }


def fit_partition(
    features: dict,
    evidence: np.ndarray,
    labels: np.ndarray,
    records: list[dict],
    parts: dict,
    output: Path,
) -> tuple[LineAdapterV3, dict]:
    """Only supplied fit/calibrate/tune labels and features enter model selection."""
    groups = np.asarray(connected_groups(records))
    expected = np.concatenate([parts[k] for k in ("fit", "calibrate", "tune")])
    assert_partition(groups, parts, expected)
    fit, cal, tune = (parts[k] for k in ("fit", "calibrate", "tune"))
    ids = [r["physical_sample_id"] for r in records]

    def take(idx):
        return {c: X[idx] for c, X in features.items()}

    def ids_at(idx):
        return [ids[i] for i in idx]

    models, candidates = [], []
    for C in CANDIDATES:
        model = LineAdapterV3().fit(take(fit), labels[fit], ids_at(fit), C=C)
        model.fit_calibration(take(cal), labels[cal], ids_at(cal))
        p = model.probabilities(take(tune))
        thresholds, metrics = select_operational_thresholds(
            p, labels[tune], ids_at(tune), maximum_fpr=0.05
        )
        models.append(model)
        candidates.append(
            {
                "C": C,
                "thresholds": thresholds.tolist(),
                "selection": metrics,
                "calibration": model.calibration,
            }
        )
        output.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(
            output / f"candidate-C{C}-scores.npz",
            fit_indices=fit,
            fit_raw_scores=model.raw_scores(take(fit)),
            fit_probabilities=model.probabilities(take(fit)),
            calibrate_indices=cal,
            tune_indices=tune,
            calibrate_raw_scores=model.raw_scores(take(cal)),
            calibrate_probabilities=model.probabilities(take(cal)),
            tune_raw_scores=model.raw_scores(take(tune)),
            tune_probabilities=p,
        )
    chosen_index = max(
        range(len(candidates)),
        key=lambda i: (candidates[i]["selection"]["macro_balanced_accuracy"], -candidates[i]["C"]),
    )
    model, selected = models[chosen_index], candidates[chosen_index]
    thresholds = np.asarray(selected["thresholds"])
    policy, policy_report = select_refusal_policy(
        model.probabilities(take(tune)), evidence[tune], labels[tune], ids_at(tune), thresholds
    )
    model.thresholds, model.selective = selected["thresholds"], policy
    report = {
        "parts": {
            name: {
                "measurement_ids": [records[i]["measurement_id"] for i in ix],
                "physical_ids": sorted({ids[i] for i in ix}),
                "clusters": sorted(set(groups[ix])),
            }
            for name, ix in parts.items()
        },
        "chosen": selected,
        "candidates": candidates,
        "selective_policy": policy,
        "selective_tuning": policy_report,
    }
    write(output / "selection.json", report)
    return model, report


def run_development_study_v3(
    project_root: Path,
    *,
    source_plan_path: Path | None = None,
    output_root: Path | None = None,
    seed: int = 20261019,
) -> Path:
    root = Path(project_root).resolve()
    source_path = source_plan_path or root / DEFAULT_PLAN
    source = json.loads(source_path.read_text())
    if source.get("scope") != SCOPE or source.get("former_final_test_is_development") is not True:
        raise ValueError("Only an explicitly disclosed development source plan is accepted")
    records = source["records"]
    validate_decision_policy(source.get("decision_policy"))
    labels = record_labels(records)
    reference = root / "data/reference/nist_uv_visible_v2/catalog.json"
    if sha(reference) != source["reference_sha256"]:
        raise ValueError("Pinned reference changed")
    catalog = ReferenceCatalog.from_cache(reference)
    plans = {v: feature_plan(catalog, v) for v in VARIANTS}
    groups = np.asarray(connected_groups(records))
    outer = outer_partitions(records, seed=seed)
    nested, partition_meta = [], []
    for fold, part in enumerate(outer):
        inner, attempt = inner_partition(records, part["outer_fit"], seed=seed + fold + 1)
        nested.append(inner)
        partition_meta.append(
            {
                **{k: v.tolist() for k, v in part.items()},
                **{k: v.tolist() for k, v in inner.items()},
                "metadata_partition_attempt": attempt,
            }
        )
    deployment, attempt = inner_partition(records, np.arange(len(records)), seed=seed + 100)
    output = (
        output_root or root / "reports/adaptation_v3"
    ) / f"development-{datetime.now(UTC):%Y%m%dT%H%M%S}-{uuid4().hex[:8]}"
    output.mkdir(parents=True, exist_ok=False)
    implementation = [
        Path(__file__),
        root / "ml/src/spectralab_ml/models/line_adapter_v3.py",
        root / "ml/src/spectralab_ml/models/line_adapter_v2.py",
        root / "ml/src/spectralab_ml/evaluation/instrument_selection.py",
        root / "ml/src/spectralab_ml/evaluation/campaign_shift_v2.py",
        root / "ml/src/spectralab_ml/data/chemcam_measurements.py",
        root / "ml/src/spectralab_ml/data/reference.py",
        root / "ml/src/spectralab_ml/evaluation/real_validation.py",
        root / "scripts/study_development_v3.py",
    ]
    protocol = {
        "schema_version": 3,
        "locked_before_spectral_read": True,
        "locked_at": datetime.now(UTC).isoformat(),
        "scope": SCOPE,
        "seed": seed,
        "source_plan": {"path": str(source_path), "sha256": sha(source_path)},
        "reference_sha256": sha(reference),
        "models": list(VARIANTS),
        "primary_candidate": PRIMARY,
        "feature_plans": plans,
        "C_candidates": list(CANDIDATES),
        "outer_folds": 5,
        "inner_roles": "fit60/calibrate20/tune20 percent of clusters",
        "inner_support_rule": (
            "First seeded partition with >=2 positive/negative clusters per class "
            "in each role; metadata only"
        ),
        "calibration": (
            "monotonic Platt on independent calibration groups; "
            "unbalanced equal physical weights; fixed ridge0.001"
        ),
        "selection": (
            "tune maximum macro balanced accuracy under per-class point FPR<=0.05; ties smaller C"
        ),
        "refusal": (
            "tune max selective balanced accuracy with known coverage>=0.5 "
            "and selective point FPR<=0.05; ties more coverage then weaker refusal"
        ),
        "refusal_grid": {"minimum_snr": [0, 2, 5, 10], "margin": [0, 0.05, 0.1, 0.2]},
        "outer_used_for_selection": False,
        "new_final_data_used": False,
        "final_test_status": "not_performed",
        "decision_policy": source["decision_policy"],
        "response_correction": "not performed; no independently measured response curve available",
        "uncertainty": (
            "1000 paired linked-cluster bootstrap resamples of fixed OOF predictions; "
            "no refit, no multiplicity correction, exploratory development intervals"
        ),
        "fit_scores": "saved for audit only; never used for reported validation metrics",
        "environment": {
            "python": platform.python_version(),
            **{name: version(name) for name in ("numpy", "scipy", "scikit-learn")},
        },
        "outer_partitions": partition_meta,
        "deployment_partition": {
            **{k: v.tolist() for k, v in deployment.items()},
            "metadata_partition_attempt": attempt,
        },
        "records": records,
        "connected_group_ids": groups.tolist(),
        "implementation_sha256": {str(p.relative_to(root)): sha(p) for p in implementation},
    }
    write(output / "protocol.json", protocol)
    result = {
        "schema_version": 3,
        "run_id": output.name,
        "status": "running",
        "scope": SCOPE,
        "qualification": QUALIFICATION,
        "final_test_status": "not_performed",
        "protocol_sha256": sha(output / "protocol.json"),
        "primary_candidate": PRIMARY,
        "support": {
            "spectra": len(records),
            "physical": len({r["physical_sample_id"] for r in records}),
            "connected_groups": len(set(groups)),
        },
        "models": {},
    }
    write(output / "result.json", result)
    started = perf_counter()
    try:
        X = []
        for record in records:
            waves, values, _ = read_chemcam_ccs(
                root / record["local_relative_path"], record["input_sha256"]
            )
            X.append(interpolate_observed(waves, values, GRID, BANDS, max_gap_nm=0.5))
        X = np.asarray(X)
        oof_probabilities = {}
        for variant in VARIANTS:
            feature_started = perf_counter()
            features, evidence = extract_features(X, GRID, plans[variant])
            np.savez_compressed(output / f"{variant}-features.npz", line_snr=evidence, **features)
            raw, probability = np.full(labels.shape, np.nan), np.full(labels.shape, np.nan)
            detected, accepted = np.zeros(labels.shape, bool), np.zeros(labels.shape, bool)
            folds = []
            for fold, (part, inner) in enumerate(zip(outer, nested, strict=True)):
                folder = output / variant / f"fold-{fold}"
                model, report = fit_partition(features, evidence, labels, records, inner, folder)
                model.plan = plans[variant]
                idx = part["outer_validation"]
                prediction = model.predict_spectra(X[idx], GRID)
                raw[idx], probability[idx] = prediction["raw_scores"], prediction["probabilities"]
                detected[idx], accepted[idx] = prediction["detections"], prediction["accepted"]
                model.save(
                    folder / "profile.json",
                    plan=model.plan,
                    thresholds=model.thresholds,
                    selective=model.selective,
                    provenance={
                        "scope": SCOPE,
                        "instrument_id": "chemcam-lanl-testbed",
                        "decision_policy": source["decision_policy"],
                        "protocol_sha256": result["protocol_sha256"],
                        "partition": report["parts"],
                    },
                )
                loaded, _ = LineAdapterV3.load(folder / "profile.json")
                np.testing.assert_array_equal(
                    loaded.predict_spectra(X[idx], GRID)["probabilities"], probability[idx]
                )
                folds.append(
                    {
                        "fold": fold,
                        "C": report["chosen"]["C"],
                        "thresholds": model.thresholds,
                        "selective": model.selective,
                    }
                )
                print(f"{variant}: outer fold {fold + 1}/5 complete", flush=True)
            if not np.isfinite(probability).all() or not np.isfinite(raw).all():
                raise ValueError("Every record must receive an outer OOF prediction")
            np.savez_compressed(
                output / variant / "development_oof.npz",
                raw_scores=raw,
                probabilities=probability,
                detections=detected,
                accepted=accepted,
                decisions=np.where(accepted, detected.astype(int), -1),
                labels=labels,
                measurement_ids=np.asarray([r["measurement_id"] for r in records]),
                physical_ids=np.asarray([r["physical_sample_id"] for r in records]),
                connected_groups=groups,
                line_snr=evidence,
            )
            result["models"][variant] = {
                "metrics": evaluate_oof(
                    records, labels, raw, probability, detected, accepted, seed=seed
                ),
                "folds": folds,
                "seconds": perf_counter() - feature_started,
                "calibration_paired_brier": paired_brier_difference(
                    records, labels, raw, probability, seed=seed
                ),
            }
            oof_probabilities[variant] = probability.copy()
            write(output / "result.json", result)
        result["paired_comparisons_to_baseline"] = {
            variant: paired_brier_difference(
                records,
                labels,
                oof_probabilities[VARIANTS[0]],
                oof_probabilities[variant],
                seed=seed,
            )
            for variant in VARIANTS[1:]
        }
        # This primary is fixed above, never selected by outer OOF results.
        features, evidence = extract_features(X, GRID, plans[PRIMARY])
        model, report = fit_partition(
            features, evidence, labels, records, deployment, output / "deployment"
        )
        model.save(
            output / "profile.json",
            plan=plans[PRIMARY],
            thresholds=model.thresholds,
            selective=model.selective,
            provenance={
                "scope": SCOPE,
                "instrument_id": "chemcam-lanl-testbed",
                "decision_policy": source["decision_policy"],
                "protocol_sha256": result["protocol_sha256"],
                "partition": report["parts"],
                "primary_fixed_before_outer_predictions": True,
            },
        )
        loaded, _ = LineAdapterV3.load(output / "profile.json")
        np.testing.assert_array_equal(loaded.probabilities(features), model.probabilities(features))
        result.update(
            status="completed",
            completed_at=datetime.now(UTC).isoformat(),
            seconds=perf_counter() - started,
            profile={
                "path": str(output / "profile.json"),
                "sha256": sha(output / "profile.json"),
                "training_fit_only": True,
                "selected_by_outer_metrics": False,
            },
        )
        if any(
            sha(root / p) != expected for p, expected in protocol["implementation_sha256"].items()
        ):
            raise ValueError("Implementation changed during the pinned run")
        write(output / "result.json", result)
    except Exception as exc:
        result.update(status="failed", error=f"{type(exc).__name__}: {exc}")
        write(output / "result.json", result)
        raise
    return output
