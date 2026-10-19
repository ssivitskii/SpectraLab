"""Second adaptive round: group-crossfitted Ca calibration/selection ensemble."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from uuid import uuid4

import numpy as np
from sklearn.model_selection import StratifiedGroupKFold

from spectralab_ml.evaluation.calcium_v4 import (
    candidates,
    choose_candidate,
    counts,
    paired_interval,
    verified_baseline,
)
from spectralab_ml.evaluation.campaign_shift_v2 import connected_groups
from spectralab_ml.evaluation.development_v3 import assert_partition, record_labels, sha, write
from spectralab_ml.evaluation.instrument_selection import select_operational_thresholds
from spectralab_ml.models.calcium_adapter_v4 import CalciumClassifier
from spectralab_ml.models.calcium_ensemble_v4 import CalciumEnsembleV4
from spectralab_ml.models.line_adapter_v3 import physical_weights


def calibration_partition(records, labels, parent, seed):
    groups = np.asarray(connected_groups(records))
    unique = np.unique(groups[parent])
    rng = np.random.default_rng(seed)
    count = max(2, round(len(unique) * 0.25))
    for _attempt in range(10000):
        cal_groups = rng.permutation(unique)[:count]
        cal = parent[np.isin(groups[parent], cal_groups)]
        fit = parent[~np.isin(groups[parent], cal_groups)]
        if all(
            len(set(groups[ix][labels[ix] == state])) >= 2 for ix in (fit, cal) for state in (0, 1)
        ):
            result = {"fit": fit, "calibrate": cal}
            assert_partition(groups, result, parent)
            return result
    raise ValueError("Supported crossfit calibration split unavailable")


def crossfit_partitions(records, labels, parent, seed):
    groups = np.asarray(connected_groups(records))
    campaigns = np.asarray([r["campaign"] for r in records])
    splitter = StratifiedGroupKFold(n_splits=4, shuffle=True, random_state=seed + 4000)
    parts = []
    for fold, (remain, hold) in enumerate(
        splitter.split(parent, campaigns[parent], groups[parent])
    ):
        remain, hold = parent[remain], parent[hold]
        for member in range(4):
            part = calibration_partition(records, labels, remain, seed + 5000 + fold * 100 + member)
            part["inner_oof"] = hold
            assert_partition(groups, part, parent)
            parts.append(part)
    covered = np.concatenate([p["inner_oof"] for p in parts])
    if sorted(covered.tolist()) != sorted(np.tile(parent, 4).tolist()):
        raise ValueError("Each parent must receive exactly four inner OOF member predictions")
    return parts


def fit_crossfit(features, labels, records, parent, validation, specs, budgets, seed, output):
    assert_partition(
        np.asarray(connected_groups(records)),
        {"parent": parent, "validation": validation},
        np.arange(len(records)),
    )
    parts = crossfit_partitions(records, labels, parent, seed)
    ids = [r["physical_sample_id"] for r in records]

    def sample_ids(ix):
        return [ids[i] for i in ix]

    inner = np.zeros((len(specs), len(records)))
    predictions = np.zeros((len(specs), len(validation)))
    model_members = [[] for _ in specs]
    output.mkdir(parents=True, exist_ok=False)
    write(
        output / "partitions.json",
        [
            {
                k: {
                    "indices": ix.tolist(),
                    "measurement_ids": [records[i]["measurement_id"] for i in ix],
                    "physical_ids": sample_ids(ix),
                }
                for k, ix in part.items()
            }
            for part in parts
        ],
    )
    for member, part in enumerate(parts):
        fit, cal, held = (part[k] for k in ("fit", "calibrate", "inner_oof"))
        cal_raw, cal_p = [], []
        for i, spec in enumerate(specs):
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
            inner[i, held] += model.probabilities(X[held]) / 4
            cal_raw.append(model.raw_scores(X[cal]))
            cal_p.append(model.probabilities(X[cal]))
        np.savez_compressed(
            output / f"member-{member}-calibration.npz",
            calibrate_indices=cal,
            raw_scores=cal_raw,
            probabilities=cal_p,
        )
    outer_parts = []
    for member in range(4):
        part = calibration_partition(records, labels, parent, seed + 6000 + member)
        outer_parts.append({key: ix.tolist() for key, ix in part.items()})
        fit, cal = part["fit"], part["calibrate"]
        for i, spec in enumerate(specs):
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
            if len(validation):
                predictions[i] += model.probabilities(X[validation]) / 4
            model_members[i].append(model.payload())
    write(output / "outer_member_partitions.json", outer_parts)
    if not np.isfinite(inner[:, parent]).all():
        raise ValueError("Incomplete inner OOF")
    weights = physical_weights(sample_ids(parent))
    known = labels[parent] >= 0
    reports = []
    for i, spec in enumerate(specs):
        p = inner[i, parent]
        thresholds = {}
        for budget in budgets:
            threshold, _ = select_operational_thresholds(
                p[:, None], labels[parent, None], sample_ids(parent), maximum_fpr=budget
            )
            thresholds[str(budget)] = {
                "threshold": float(threshold[0]),
                "metrics": counts(labels[parent], p >= threshold[0], weights),
            }
        reports.append(
            {
                "spec": spec,
                "dimension": features[spec["family"]].shape[1],
                "tune_brier": float(
                    np.sum(weights[known] * (p[known] - labels[parent][known]) ** 2)
                    / weights[known].sum()
                ),
                "thresholds": thresholds,
            }
        )
    np.savez_compressed(
        output / "crossfit_scores.npz",
        inner_indices=parent,
        inner_oof_probabilities=inner[:, parent],
        validation_indices=validation,
        validation_ensemble_probabilities=predictions,
    )
    write(output / "selection.json", {"candidates": reports})
    write(output / "candidate_member_states.json", model_members)
    return predictions, reports, model_members


def run_crossfit_study(root: Path):
    root = root.resolve()
    protocol_path = root / "data/reference/calcium_v4_round2_protocol.json"
    protocol = json.loads(protocol_path.read_text())
    source_path = root / protocol["source_plan"]
    previous = root / protocol["preceding_run"]["path"]
    if (
        sha(source_path) != protocol["source_sha256"]
        or sha(previous / "result.json") != protocol["preceding_run"]["result_sha256"]
        or sha(previous / "features.npz") != protocol["preceding_run"]["features_sha256"]
    ):
        raise ValueError("Pinned source or prior attempt changed")
    records = json.loads(source_path.read_text())["records"]
    labels = record_labels(records)[:, 2]
    with np.load(previous / "features.npz", allow_pickle=False) as raw:
        features = {
            family: raw[family]
            for family in ("full-v3-ca", "blue-ca-agreement", "full-ca-agreement")
        }
        if not np.array_equal(raw["labels"], labels):
            raise ValueError("Prior feature rows changed")
    references = [
        verified_baseline(root, row, records) for row in protocol["matched_baseline_runs"]
    ]
    specs, budgets = candidates(protocol), protocol["threshold_budgets"]
    output = (
        root
        / "reports/adaptation_v4"
        / f"calcium-crossfit-{datetime.now(UTC):%Y%m%dT%H%M%S}-{uuid4().hex[:8]}"
    )
    output.mkdir(parents=True, exist_ok=False)
    prior_lock = json.loads((previous / "lock.json").read_text())
    implementation = {
        **{name: sha(root / name) for name in prior_lock["implementation_sha256"]},
        "ml/src/spectralab_ml/models/calcium_ensemble_v4.py": sha(
            root / "ml/src/spectralab_ml/models/calcium_ensemble_v4.py"
        ),
        str(Path(__file__).relative_to(root)): sha(Path(__file__)),
        "scripts/study_calcium_crossfit_v4.py": sha(root / "scripts/study_calcium_crossfit_v4.py"),
    }
    lock = {
        "protocol": protocol,
        "protocol_sha256": sha(protocol_path),
        "implementation_sha256": implementation,
        "features_sha256": sha(previous / "features.npz"),
        "locked_before_scoring": True,
    }
    write(output / "lock.json", lock)
    result = {
        "schema_version": 4,
        "status": "running",
        "scope": protocol["scope"],
        "lock_sha256": sha(output / "lock.json"),
        "primary_selector": "global@0.025",
        "final_test_status": "not_performed",
        "seeds": [],
    }
    started = perf_counter()
    weights = physical_weights([r["physical_sample_id"] for r in records])
    write(output / "result.json", result)
    try:
        for saved, baseline_all, _ in references:
            seed = saved["seed"]
            folder = output / f"seed-{seed}"
            folder.mkdir()
            baseline = baseline_all[:, 2]
            probabilities = np.full((len(specs), len(records)), np.nan)
            detected = {str(b): np.zeros((len(specs), len(records)), bool) for b in budgets}
            names = ["global"] + [
                f"{family}|{learner}" for family in features for learner in ("logistic", "rbf")
            ]
            selected = {
                f"{name}@{b}": np.zeros(len(records), bool) for name in names for b in budgets
            }
            choices = []
            for fold, part in enumerate(saved["outer_partitions"]):
                parent, val = np.asarray(part["outer_fit"]), np.asarray(part["outer_validation"])
                p, reports, _ = fit_crossfit(
                    features,
                    labels,
                    records,
                    parent,
                    val,
                    specs,
                    budgets,
                    seed + fold * 100,
                    folder / f"fold-{fold}",
                )
                probabilities[:, val] = p
                for i in range(len(specs)):
                    for b in budgets:
                        detected[str(b)][i, val] = (
                            p[i] >= reports[i]["thresholds"][str(b)]["threshold"]
                        )
                decisions = {}
                for name in names:
                    filters = (
                        {}
                        if name == "global"
                        else dict(zip(("family", "learner"), name.split("|"), strict=True))
                    )
                    for b in budgets:
                        chosen = choose_candidate(reports, b, **filters)
                        selected[f"{name}@{b}"][val] = detected[str(b)][chosen, val]
                        decisions[f"{name}@{b}"] = specs[chosen]["id"]
                choices.append({"fold": fold, "choices": decisions})
                print(f"crossfit seed {seed}: outer fold {fold + 1}/5 completed", flush=True)
            if not np.isfinite(probabilities).all():
                raise ValueError("Incomplete outer scores")
            np.savez_compressed(
                folder / "development_oof.npz",
                labels=labels,
                candidate_probabilities=probabilities,
                baseline_detection=baseline,
                **{f"candidates@{b}": v for b, v in detected.items()},
                **selected,
            )
            metrics = {name: counts(labels, pred, weights) for name, pred in selected.items()}
            baseline_metrics = counts(labels, baseline, weights)
            for name, metric in metrics.items():
                metric["matched_recall_delta"] = metric["recall"] - baseline_metrics["recall"]
                metric["matched_noninferiority_and_fpr"] = (
                    metric["matched_recall_delta"] >= -0.02 - 1e-12
                    and metric["fpr"] <= 0.05 + 1e-12
                )
                metric["false_positive_measurement_ids"] = [
                    records[i]["measurement_id"]
                    for i in np.flatnonzero((labels == 0) & selected[name])
                ]
                metric["false_negative_measurement_ids"] = [
                    records[i]["measurement_id"]
                    for i in np.flatnonzero((labels == 1) & ~selected[name])
                ]
            metrics["global@0.025"]["paired_cluster_intervals"] = paired_interval(
                labels, selected["global@0.025"], baseline, records, seed=seed
            )
            result["seeds"].append(
                {
                    "seed": seed,
                    "baseline": counts(labels, baseline, weights),
                    "selectors": metrics,
                    "inner_choices": choices,
                    "all_fixed_attempts": {
                        f"{spec['id']}@{b}": counts(labels, detected[str(b)][i], weights)
                        for i, spec in enumerate(specs)
                        for b in budgets
                    },
                }
            )
            write(output / "result.json", result)
        _, reports, members = fit_crossfit(
            features,
            labels,
            records,
            np.arange(len(records)),
            np.array([], int),
            specs,
            budgets,
            20261019,
            output / "deployment",
        )
        chosen = choose_candidate(reports, 0.025)
        CalciumEnsembleV4.save(
            output / "research_profile.json",
            original_profile=references[0][2],
            members=members[chosen],
            family=specs[chosen]["family"],
            threshold=reports[chosen]["thresholds"]["0.025"]["threshold"],
            provenance={
                "source_sha256": protocol["source_sha256"],
                "lock_sha256": result["lock_sha256"],
                "selected_by_outer": False,
                "candidate": specs[chosen],
                "automatic_promotion": False,
            },
        )
        loaded, _ = CalciumEnsembleV4.load(output / "research_profile.json")
        expected = np.mean(
            [
                CalciumClassifier.from_payload(m, features[loaded.family].shape[1]).probabilities(
                    features[loaded.family]
                )
                for m in members[chosen]
            ],
            axis=0,
        )
        actual = np.mean([m.probabilities(features[loaded.family]) for m in loaded.members], axis=0)
        np.testing.assert_array_equal(expected, actual)
        if any(sha(root / path) != expected for path, expected in implementation.items()):
            raise ValueError("Implementation changed during pinned crossfit run")
        result.update(
            status="completed",
            seconds=perf_counter() - started,
            research_profile={
                "path": str(output / "research_profile.json"),
                "sha256": sha(output / "research_profile.json"),
            },
            secondary_robust_target_achieved=(
                result["seeds"][0]["selectors"]["global@0.025"]["true_positive"] >= 83
                and all(
                    s["selectors"]["global@0.025"]["matched_noninferiority_and_fpr"]
                    for s in result["seeds"]
                )
            ),
            robust_point_target_achieved=all(
                s["selectors"]["global@0.025"]["meets_point_target"] for s in result["seeds"]
            ),
        )
        write(output / "result.json", result)
    except Exception as exc:
        result.update(status="failed", error=f"{type(exc).__name__}: {exc}")
        write(output / "result.json", result)
        raise
    return output
