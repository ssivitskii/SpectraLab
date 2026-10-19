"""Locked candidate search on disclosed development data, with nested selection."""

from __future__ import annotations

import hashlib
import io
import json
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from uuid import uuid4

import numpy as np

from spectralab_ml.data.chemcam_measurements import read_chemcam_ccs
from spectralab_ml.evaluation.campaign_shift_v2 import connected_groups
from spectralab_ml.evaluation.development_v3 import assert_partition, record_labels, sha, write
from spectralab_ml.evaluation.instrument_selection import select_operational_thresholds
from spectralab_ml.evaluation.real_validation import interpolate_observed
from spectralab_ml.models.calcium_adapter_v4 import (
    FAMILIES,
    SCOPE,
    CalciumAdapterV4,
    CalciumClassifier,
    calcium_features,
)
from spectralab_ml.models.line_adapter_v3 import (
    BANDS,
    GRID,
    LineAdapterV3,
    digest,
    physical_weights,
)


def candidates(protocol):
    rows = []
    for family in FAMILIES:
        for C in protocol["learners"]["balanced_logistic"]["C"]:
            rows.append({"family": family, "learner": "logistic", "C": C, "gamma_multiplier": 1.0})
        for C in protocol["learners"]["balanced_rbf_svc"]["C"]:
            for gamma in protocol["learners"]["balanced_rbf_svc"]["gamma_multiplier"]:
                rows.append({"family": family, "learner": "rbf", "C": C, "gamma_multiplier": gamma})
    for i, row in enumerate(rows):
        row["id"] = f"candidate-{i:02d}"
    return rows


def counts(y, detected, weights):
    positive, negative = y == 1, y == 0
    tp, fp = float(weights[positive & detected].sum()), float(weights[negative & detected].sum())
    pos, neg = float(weights[positive].sum()), float(weights[negative].sum())
    recall, fpr = (tp / pos if pos else None), (fp / neg if neg else None)
    return {
        "positive_support": pos,
        "negative_support": neg,
        "true_positive": tp,
        "false_negative": pos - tp,
        "false_positive": fp,
        "true_negative": neg - fp,
        "recall": recall,
        "fpr": fpr,
        "meets_point_target": bool(
            recall is not None
            and fpr is not None
            and recall >= 84 / 93 - 0.02 - 1e-12
            and fpr <= 0.05 + 1e-12
        ),
    }


def choose_candidate(reports, budget, *, family=None, learner=None):
    possible = [
        i
        for i, r in enumerate(reports)
        if (family is None or r["spec"]["family"] == family)
        and (learner is None or r["spec"]["learner"] == learner)
    ]

    def ranking(i):
        r = reports[i]
        score = r["thresholds"][str(budget)]["metrics"]
        s = r["spec"]
        return (
            score["recall"],
            -score["fpr"],
            -r["tune_brier"],
            -(s["learner"] == "rbf"),
            -r["dimension"],
            -s["C"],
            -s["gamma_multiplier"],
            -i,
        )

    return max(possible, key=ranking)


def fit_candidates(features, labels, records, parts, specs, budgets, output):
    groups = np.asarray(connected_groups(records))
    assert_partition(groups, parts, np.concatenate(list(parts.values())))
    ids = [r["physical_sample_id"] for r in records]
    fit, cal, tune = (parts[k] for k in ("fit", "calibrate", "tune"))

    def sample_ids(ix):
        return [ids[i] for i in ix]

    weights = physical_weights(sample_ids(tune))
    known = labels[tune] >= 0
    models, reports, raw_cal, p_cal, raw_tune, p_tune = [], [], [], [], [], []
    for spec in specs:
        X = features[spec["family"]]
        model = CalciumClassifier().fit(
            X[fit],
            labels[fit],
            sample_ids(fit),
            learner=spec["learner"],
            C=spec["C"],
            gamma_multiplier=spec["gamma_multiplier"],
        )
        model.fit_calibration(X[cal], labels[cal], sample_ids(cal))
        p = model.probabilities(X[tune])
        brier = float(
            np.sum(weights[known] * (p[known] - labels[tune][known]) ** 2) / weights[known].sum()
        )
        thresholds = {}
        for budget in budgets:
            t, _ = select_operational_thresholds(
                p[:, None], labels[tune, None], sample_ids(tune), maximum_fpr=budget
            )
            thresholds[str(budget)] = {
                "threshold": float(t[0]),
                "metrics": counts(labels[tune], p >= t[0], weights),
            }
        reports.append(
            {
                "spec": spec,
                "dimension": X.shape[1],
                "tune_brier": brier,
                "thresholds": thresholds,
                "calibration": model.calibration,
            }
        )
        models.append(model)
        raw_cal.append(model.raw_scores(X[cal]))
        p_cal.append(model.probabilities(X[cal]))
        raw_tune.append(model.raw_scores(X[tune]))
        p_tune.append(p)
    output.mkdir(parents=True, exist_ok=False)
    np.savez_compressed(
        output / "selection_scores.npz",
        fit_indices=fit,
        calibrate_indices=cal,
        tune_indices=tune,
        calibrate_raw_scores=raw_cal,
        calibrate_probabilities=p_cal,
        tune_raw_scores=raw_tune,
        tune_probabilities=p_tune,
    )
    write(
        output / "selection.json",
        {
            "parts": {
                k: {
                    "indices": ix.tolist(),
                    "measurement_ids": [records[i]["measurement_id"] for i in ix],
                    "physical_ids": sorted(set(sample_ids(ix))),
                    "connected_groups": sorted(set(groups[ix])),
                }
                for k, ix in parts.items()
            },
            "candidates": reports,
        },
    )
    write(output / "candidate_models.json", [model.payload() for model in models])
    return models, reports


def paired_interval(y, prediction, baseline, records, *, seed):
    weights = physical_weights([r["physical_sample_id"] for r in records])
    groups, inverse = np.unique(connected_groups(records), return_inverse=True)
    rng = np.random.default_rng(seed)
    values = {"fpr": [], "recall": [], "fpr_delta": [], "recall_delta": []}
    for draw in rng.multinomial(len(groups), np.full(len(groups), 1 / len(groups)), size=2000):
        w = weights * draw[inverse]
        new, old = counts(y, prediction, w), counts(y, baseline, w)
        for key in ("fpr", "recall"):
            if new[key] is not None:
                values[key].append(new[key])
                values[key + "_delta"].append(new[key] - old[key])
    return {
        key: {
            "lower": float(np.quantile(v, 0.025)),
            "upper": float(np.quantile(v, 0.975)),
            "valid_repeats": len(v),
        }
        if v
        else None
        for key, v in values.items()
    }


def verified_baseline(root, row, records):
    """Verify one immutable ordered baseline before any adaptive candidate scoring."""
    path = root / row["path"]
    buffers = {}
    for key, relative in (
        ("protocol", "protocol.json"),
        ("result", "result.json"),
        ("oof", "continuum-snr-ion/development_oof.npz"),
        ("profile", "profile.json"),
    ):
        buffer = (path / relative).read_bytes()
        if hashlib.sha256(buffer).hexdigest() != row.get(key + "_sha256"):
            raise ValueError("Matched baseline " + key + " integrity mismatch")
        buffers[key] = buffer
    saved, result, profile = (json.loads(buffers[k]) for k in ("protocol", "result", "profile"))
    if saved["seed"] != row["seed"] or saved["records"] != records:
        raise ValueError("Baseline rows or seed do not match")
    if result["profile"]["sha256"] != row["profile_sha256"]:
        raise ValueError("Baseline profile differs from original completed result")
    body = dict(profile)
    if body.pop("identity", None) != digest(body):
        raise ValueError("Baseline profile identity mismatch")
    loaded = LineAdapterV3._from_payload(body)
    if loaded.plan != saved["feature_plans"]["continuum-snr-ion"]:
        raise ValueError("Baseline feature plan mismatch")
    with np.load(io.BytesIO(buffers["oof"]), allow_pickle=False) as raw:
        if (
            not np.array_equal(raw["labels"], record_labels(records))
            or raw["measurement_ids"].tolist() != [r["measurement_id"] for r in records]
            or raw["physical_ids"].tolist() != [r["physical_sample_id"] for r in records]
            or not np.array_equal(raw["connected_groups"], connected_groups(records))
            or raw["detections"].shape != (len(records), 4)
            or raw["detections"].dtype != np.dtype(bool)
        ):
            raise ValueError("Baseline OOF row identities, labels or dimensions changed")
        detections = raw["detections"].copy()
    return saved, detections, profile


def run_calcium_study(root: Path, *, protocol_path: Path | None = None) -> Path:
    root = root.resolve()
    protocol_path = (
        protocol_path or root / "data/reference/calcium_v4_round1_verified_protocol.json"
    )
    protocol = json.loads(protocol_path.read_text())
    source_path = root / protocol["source_plan"]
    if sha(source_path) != protocol["source_sha256"] or not protocol["locked_before_new_scoring"]:
        raise ValueError("Locked development protocol/source required")
    source = json.loads(source_path.read_text())
    records = source["records"]
    labels = record_labels(records)[:, 2]
    weights = physical_weights([r["physical_sample_id"] for r in records])
    groups = np.asarray(connected_groups(records))
    references = [
        verified_baseline(root, row, records) for row in protocol["matched_baseline_runs"]
    ]
    specs, budgets = candidates(protocol), protocol["threshold_budgets"]
    first_plan = references[0][0]["feature_plans"]["continuum-snr-ion"]
    output = (
        root
        / "reports/adaptation_v4"
        / f"calcium-{datetime.now(UTC):%Y%m%dT%H%M%S}-{uuid4().hex[:8]}"
    )
    output.mkdir(parents=True, exist_ok=False)
    implementation = [
        Path(__file__),
        root / "ml/src/spectralab_ml/models/calcium_adapter_v4.py",
        root / "scripts/study_calcium_v4.py",
    ]
    for name in references[0][0]["implementation_sha256"]:
        implementation.append(root / name)
    lock = {
        "protocol_sha256": sha(protocol_path),
        "protocol": protocol,
        "implementation_sha256": {str(p.relative_to(root)): sha(p) for p in implementation},
        "candidate_specs": specs,
        "started_at": datetime.now(UTC).isoformat(),
        "numerical_inputs_read_at_lock": "Pinned baseline only; candidate spectra unread",
    }
    write(output / "lock.json", lock)
    result = {
        "schema_version": 4,
        "status": "running",
        "scope": SCOPE,
        "final_test_status": "not_performed",
        "primary_selector": "global@0.025",
        "lock_sha256": sha(output / "lock.json"),
        "protocol_sha256": sha(protocol_path),
        "seeds": [],
        "all_attempts_disclosed": True,
    }
    started = perf_counter()
    write(output / "result.json", result)
    try:
        spectra = []
        for record in records:
            wave, signal, _ = read_chemcam_ccs(
                root / record["local_relative_path"], record["input_sha256"]
            )
            spectra.append(interpolate_observed(wave, signal, GRID, BANDS, max_gap_nm=0.5))
        features = calcium_features(np.asarray(spectra), GRID, first_plan)
        np.savez_compressed(
            output / "features.npz", **features, labels=labels, connected_groups=groups
        )
        for saved, baseline_all, _ in references:
            seed = saved["seed"]
            folder = output / f"seed-{seed}"
            folder.mkdir()
            baseline = baseline_all[:, 2]
            candidate_p = np.full((len(specs), len(records)), np.nan)
            candidate_detection = {
                str(b): np.zeros((len(specs), len(records)), bool) for b in budgets
            }
            names = ["global"] + [
                f"{family}|{learner}" for family in FAMILIES for learner in ("logistic", "rbf")
            ]
            selected = {
                f"{name}@{b}": np.zeros(len(records), bool) for name in names for b in budgets
            }
            selection_audit = []
            for fold, part in enumerate(saved["outer_partitions"]):
                inner = {k: np.asarray(part[k]) for k in ("fit", "calibrate", "tune")}
                val = np.asarray(part["outer_validation"])
                assert_partition(
                    groups,
                    {"outer_fit": np.asarray(part["outer_fit"]), "validation": val},
                    np.arange(len(records)),
                )
                assert_partition(groups, inner, np.asarray(part["outer_fit"]))
                models, reports = fit_candidates(
                    features, labels, records, inner, specs, budgets, folder / f"fold-{fold}"
                )
                for i, (model, spec) in enumerate(zip(models, specs, strict=True)):
                    p = model.probabilities(features[spec["family"]][val])
                    candidate_p[i, val] = p
                    for b in budgets:
                        t = reports[i]["thresholds"][str(b)]["threshold"]
                        candidate_detection[str(b)][i, val] = p >= t
                choices = {}
                for name in names:
                    filters = (
                        {}
                        if name == "global"
                        else dict(zip(("family", "learner"), name.split("|"), strict=True))
                    )
                    for b in budgets:
                        chosen = choose_candidate(reports, b, **filters)
                        selected[f"{name}@{b}"][val] = candidate_detection[str(b)][chosen, val]
                        choices[f"{name}@{b}"] = specs[chosen]["id"]
                selection_audit.append({"fold": fold, "choices": choices})
                print(f"seed {seed}: matched outer fold {fold + 1}/5 completed", flush=True)
            if not np.isfinite(candidate_p).all():
                raise ValueError("Incomplete outer predictions")
            np.savez_compressed(
                folder / "development_oof.npz",
                labels=labels,
                candidate_probabilities=candidate_p,
                baseline_detection=baseline,
                **{f"candidates@{b}": v for b, v in candidate_detection.items()},
                **selected,
            )
            metrics = {name: counts(labels, pred, weights) for name, pred in selected.items()}
            for name in metrics:
                metrics[name]["false_positive_measurement_ids"] = [
                    records[i]["measurement_id"]
                    for i in np.flatnonzero((labels == 0) & selected[name])
                ]
                metrics[name]["false_negative_measurement_ids"] = [
                    records[i]["measurement_id"]
                    for i in np.flatnonzero((labels == 1) & ~selected[name])
                ]
            primary = "global@0.025"
            metrics[primary]["paired_cluster_intervals"] = paired_interval(
                labels, selected[primary], baseline, records, seed=seed
            )
            attempts = {
                f"{spec['id']}@{b}": counts(labels, candidate_detection[str(b)][i], weights)
                for i, spec in enumerate(specs)
                for b in budgets
            }
            result["seeds"].append(
                {
                    "seed": seed,
                    "baseline": counts(labels, baseline, weights),
                    "selectors": metrics,
                    "all_fixed_attempts": attempts,
                    "inner_choices": selection_audit,
                }
            )
            write(output / "result.json", result)
        original = references[0][2]
        deployment = {
            k: np.asarray(references[0][0]["deployment_partition"][k])
            for k in ("fit", "calibrate", "tune")
        }
        models, reports = fit_candidates(
            features, labels, records, deployment, specs, budgets, output / "deployment"
        )
        chosen = choose_candidate(reports, 0.025)
        report = reports[chosen]
        CalciumAdapterV4.save(
            output / "research_profile.json",
            original_profile=original,
            calcium=models[chosen],
            family=specs[chosen]["family"],
            threshold=report["thresholds"]["0.025"]["threshold"],
            provenance={
                "source_sha256": protocol["source_sha256"],
                "lock_sha256": result["lock_sha256"],
                "selected_by_outer": False,
                "candidate": specs[chosen],
                "automatic_promotion": False,
            },
        )
        loaded, _ = CalciumAdapterV4.load(output / "research_profile.json")
        np.testing.assert_array_equal(
            loaded.calcium.probabilities(features[loaded.family]),
            models[chosen].probabilities(features[loaded.family]),
        )
        result.update(
            status="completed",
            seconds=perf_counter() - started,
            research_profile={
                "path": str(output / "research_profile.json"),
                "sha256": sha(output / "research_profile.json"),
            },
            robust_point_target_achieved=all(
                r["selectors"]["global@0.025"]["meets_point_target"] for r in result["seeds"]
            ),
        )
        if any(sha(root / p) != expected for p, expected in lock["implementation_sha256"].items()):
            raise ValueError("Implementation changed during pinned run")
        write(output / "result.json", result)
    except Exception as exc:
        result.update(status="failed", error=f"{type(exc).__name__}: {exc}")
        write(output / "result.json", result)
        raise
    return output
