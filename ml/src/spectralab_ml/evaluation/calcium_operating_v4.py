"""Final adaptive operating-point round; no classifier retraining or outer tuning."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from uuid import uuid4

import numpy as np

from spectralab_ml.evaluation.calcium_v4 import (
    candidates,
    choose_candidate,
    counts,
    paired_interval,
    verified_baseline,
)
from spectralab_ml.evaluation.development_v3 import record_labels, sha, write
from spectralab_ml.evaluation.instrument_selection import select_operational_thresholds
from spectralab_ml.models.calcium_adapter_v4 import FAMILIES
from spectralab_ml.models.calcium_ensemble_v4 import CalciumEnsembleV4
from spectralab_ml.models.line_adapter_v3 import physical_weights


def operating_reports(probabilities, labels, physical_ids, specs, dimensions):
    """Only inner-OOF labels/probabilities enter operating-point selection."""
    p = np.asarray(probabilities, float)
    y = np.asarray(labels)
    if (
        p.shape != (len(specs), len(y))
        or not np.isfinite(p).all()
        or ((p < 0) | (p > 1)).any()
        or not np.isin(y, [-1, 0, 1]).all()
        or len(physical_ids) != len(y)
    ):
        raise ValueError("Aligned finite inner OOF probabilities required")
    w = physical_weights(physical_ids)
    known = y >= 0
    reports = []
    for row, spec, dimension in zip(p, specs, dimensions, strict=True):
        threshold, _ = select_operational_thresholds(
            row[:, None], y[:, None], physical_ids, maximum_fpr=0.0
        )
        reports.append(
            {
                "spec": spec,
                "dimension": dimension,
                "tune_brier": float(
                    np.sum(w[known] * (row[known] - y[known]) ** 2) / w[known].sum()
                ),
                "thresholds": {
                    "0.0": {
                        "threshold": float(threshold[0]),
                        "metrics": counts(y, row >= threshold[0], w),
                    }
                },
            }
        )
    return reports


def run_operating_study(root: Path):
    root = root.resolve()
    protocol_path = root / "data/reference/calcium_v4_round3_protocol.json"
    protocol = json.loads(protocol_path.read_text())
    prior = root / protocol["preceding_run"]
    if sha(prior / "lock.json") != protocol["preceding_lock_sha256"]:
        raise ValueError("Preceding lock changed")
    prior_lock = json.loads((prior / "lock.json").read_text())
    prior_result = json.loads((prior / "result.json").read_text())
    if prior_result["status"] != "completed":
        raise ValueError("Completed round2 required before zero-budget evaluation")
    prior_protocol = prior_lock["protocol"]
    source = root / prior_protocol["source_plan"]
    if sha(source) != prior_protocol["source_sha256"]:
        raise ValueError("Development source changed")
    records = json.loads(source.read_text())["records"]
    labels = record_labels(records)[:, 2]
    references = [
        verified_baseline(root, row, records) for row in prior_protocol["matched_baseline_runs"]
    ]
    specs = candidates(prior_protocol)
    paths = [prior / "lock.json", prior / "result.json", source]
    for row in prior_protocol["matched_baseline_runs"]:
        folder = root / row["path"]
        paths += [
            folder / name
            for name in (
                "protocol.json",
                "result.json",
                "profile.json",
                "continuum-snr-ion/development_oof.npz",
            )
        ]
    for saved, _, _ in references:
        folder = prior / f"seed-{saved['seed']}"
        paths.append(folder / "development_oof.npz")
        for fold in range(5):
            paths += [
                folder / f"fold-{fold}" / name
                for name in (
                    "crossfit_scores.npz",
                    "selection.json",
                    "candidate_member_states.json",
                )
            ]
    paths += [
        prior / "deployment" / name
        for name in ("crossfit_scores.npz", "selection.json", "candidate_member_states.json")
    ]
    implementation = {
        **prior_lock["implementation_sha256"],
        str(Path(__file__).relative_to(root)): sha(Path(__file__)),
        "scripts/study_calcium_operating_v4.py": sha(
            root / "scripts/study_calcium_operating_v4.py"
        ),
    }
    if any(sha(root / p) != value for p, value in implementation.items()):
        raise ValueError("Pinned implementation changed")
    output = (
        root
        / "reports/adaptation_v4"
        / f"calcium-zero-{datetime.now(UTC):%Y%m%dT%H%M%S}-{uuid4().hex[:8]}"
    )
    output.mkdir(parents=True, exist_ok=False)
    lock = {
        "protocol": protocol,
        "protocol_sha256": sha(protocol_path),
        "input_sha256": {str(p.relative_to(root)): sha(p) for p in paths},
        "implementation_sha256": implementation,
        "locked_before_new_operating_point": True,
    }
    write(output / "lock.json", lock)
    result = {
        "schema_version": 4,
        "status": "running",
        "scope": protocol["scope"],
        "primary_selector": "global@0.0",
        "final_test_status": "not_performed",
        "lock_sha256": sha(output / "lock.json"),
        "seeds": [],
    }
    write(output / "result.json", result)
    started = perf_counter()
    weights = physical_weights([r["physical_sample_id"] for r in records])
    names = ["global"] + [
        f"{family}|{learner}" for family in FAMILIES for learner in ("logistic", "rbf")
    ]

    def retune(folder, expected_parent, expected_validation):
        old = json.loads((folder / "selection.json").read_text())["candidates"]
        if [row["spec"] for row in old] != specs:
            raise ValueError("Pinned candidate order differs")
        with np.load(folder / "crossfit_scores.npz", allow_pickle=False) as raw:
            parent = raw["inner_indices"]
            if not np.array_equal(parent, expected_parent) or not np.array_equal(
                raw["validation_indices"], expected_validation
            ):
                raise ValueError("Pinned inner/outer index order differs")
            reports = operating_reports(
                raw["inner_oof_probabilities"],
                labels[parent],
                [records[i]["physical_sample_id"] for i in parent],
                specs,
                [r["dimension"] for r in old],
            )
            p = raw["validation_ensemble_probabilities"].copy()
        if p.shape != (len(specs), len(expected_validation)) or not np.isfinite(p).all():
            raise ValueError("Pinned outer probabilities invalid")
        return reports, p

    try:
        for saved, baseline_all, _ in references:
            seed = saved["seed"]
            folder = output / f"seed-{seed}"
            folder.mkdir()
            baseline = baseline_all[:, 2]
            candidate_detection = np.zeros((len(specs), len(records)), bool)
            selected = {f"{name}@0.0": np.zeros(len(records), bool) for name in names}
            choices = []
            for fold, part in enumerate(saved["outer_partitions"]):
                parent, val = np.asarray(part["outer_fit"]), np.asarray(part["outer_validation"])
                reports, p = retune(prior / f"seed-{seed}/fold-{fold}", parent, val)
                candidate_detection[:, val] = (
                    p >= np.asarray([r["thresholds"]["0.0"]["threshold"] for r in reports])[:, None]
                )
                decisions = {}
                for name in names:
                    filters = (
                        {}
                        if name == "global"
                        else dict(zip(("family", "learner"), name.split("|"), strict=True))
                    )
                    chosen = choose_candidate(reports, 0.0, **filters)
                    selected[f"{name}@0.0"][val] = candidate_detection[chosen, val]
                    decisions[f"{name}@0.0"] = specs[chosen]["id"]
                choices.append({"fold": fold, "choices": decisions})
                write(
                    folder / f"fold-{fold}-selection.json",
                    {"candidates": reports, "choices": decisions},
                )
            base = counts(labels, baseline, weights)
            metrics = {name: counts(labels, p, weights) for name, p in selected.items()}
            for name, m in metrics.items():
                m["matched_recall_delta"] = m["recall"] - base["recall"]
                m["matched_noninferiority_and_fpr"] = (
                    m["matched_recall_delta"] >= -0.02 - 1e-12 and m["fpr"] <= 0.05 + 1e-12
                )
                m["false_positive_measurement_ids"] = [
                    records[i]["measurement_id"]
                    for i in np.flatnonzero((labels == 0) & selected[name])
                ]
                m["false_negative_measurement_ids"] = [
                    records[i]["measurement_id"]
                    for i in np.flatnonzero((labels == 1) & ~selected[name])
                ]
            metrics["global@0.0"]["paired_cluster_intervals"] = paired_interval(
                labels, selected["global@0.0"], baseline, records, seed=seed
            )
            np.savez_compressed(
                folder / "development_oof.npz",
                labels=labels,
                baseline_detection=baseline,
                candidate_detections=candidate_detection,
                **selected,
            )
            result["seeds"].append(
                {
                    "seed": seed,
                    "baseline": base,
                    "selectors": metrics,
                    "inner_choices": choices,
                    "all_fixed_attempts": {
                        f"{s['id']}@0.0": counts(labels, candidate_detection[i], weights)
                        for i, s in enumerate(specs)
                    },
                }
            )
            write(output / "result.json", result)
        reports, _ = retune(prior / "deployment", np.arange(len(records)), np.array([], int))
        chosen = choose_candidate(reports, 0.0)
        members = json.loads((prior / "deployment/candidate_member_states.json").read_text())[
            chosen
        ]
        write(
            output / "deployment_selection.json", {"candidates": reports, "chosen": specs[chosen]}
        )
        CalciumEnsembleV4.save(
            output / "research_profile.json",
            original_profile=references[0][2],
            members=members,
            family=specs[chosen]["family"],
            threshold=reports[chosen]["thresholds"]["0.0"]["threshold"],
            provenance={
                "source_sha256": prior_protocol["source_sha256"],
                "lock_sha256": result["lock_sha256"],
                "selected_by_outer": False,
                "candidate": specs[chosen],
                "automatic_promotion": False,
                "operating_point_round": 3,
                "inner_fpr_budget": 0.0,
            },
        )
        CalciumEnsembleV4.load(output / "research_profile.json")
        for table in (lock["implementation_sha256"], lock["input_sha256"]):
            if any(sha(root / p) != value for p, value in table.items()):
                raise ValueError("Pinned bytes changed during operating-point run")
        result.update(
            status="completed",
            seconds=perf_counter() - started,
            research_profile={
                "path": str(output / "research_profile.json"),
                "sha256": sha(output / "research_profile.json"),
            },
            robust_point_target_achieved=all(
                s["selectors"]["global@0.0"]["meets_point_target"] for s in result["seeds"]
            ),
            secondary_robust_target_achieved=(
                result["seeds"][0]["selectors"]["global@0.0"]["true_positive"] >= 83
                and all(
                    s["selectors"]["global@0.0"]["matched_noninferiority_and_fpr"]
                    for s in result["seeds"]
                )
            ),
        )
        write(output / "result.json", result)
    except Exception as exc:
        result.update(status="failed", error=f"{type(exc).__name__}: {exc}")
        write(output / "result.json", result)
        raise
    return output
