"""Nested grouped DEVELOPMENT comparison; no claim of independent blank validation."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import numpy as np
from sklearn.model_selection import StratifiedGroupKFold
from spectralab_ml.config import repository_root
from spectralab_ml.data.chemcam_measurements import read_chemcam_ccs
from spectralab_ml.data.reference import ReferenceCatalog
from spectralab_ml.evaluation.adaptation_protocol import evaluate_detections, validate_protocol
from spectralab_ml.evaluation.campaign_shift_v2 import connected_groups
from spectralab_ml.evaluation.instrument_selection import select_operational_thresholds
from spectralab_ml.evaluation.real_validation import interpolate_observed
from spectralab_ml.models.instrument_adaptation import InstrumentAdapter
from spectralab_ml.models.line_adapter_v2 import (
    BANDS,
    CLASSES,
    LineAdapterV2,
    extract_line_features,
    line_plan,
)

SEED = 20261014
CANDIDATES = [0.01, 0.1, 1.0]


def write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def development_records(root: Path, additional: Path) -> tuple[dict, list[dict]]:
    previous = json.loads((root / "data/reference/chemcam_adaptation_protocol.json").read_text())
    records = copy.deepcopy(previous["development"] + previous["test"])
    extra = json.loads(additional.read_text())
    for pinned in extra["pinned_inputs"]:
        if sha(root / pinned["path"]) != pinned["sha256"]:
            raise ValueError("Additional intake provenance changed")
    chemistry_path = "data/raw/chemcam_external/ccam_calibration_compositions.csv"
    chemistry_sha = sha(root / chemistry_path)
    for record in extra["records"]:
        if record.get("status") != "downloaded" or record.get("role") != "development":
            raise ValueError(
                "Only explicitly downloaded additional development measurements allowed"
            )
        record = copy.deepcopy(record)
        record["labels"] = {}
        for element, oxide in zip(CLASSES, ["Na2O", "MgO", "CaO", "FeOT"], strict=True):
            raw = record["composition_row"][oxide].strip()
            value = float(raw) if raw else None
            known = value is not None and (0 < value <= 0.1 or value >= 1)
            record["labels"][element] = {
                "state": "positive" if known and value >= 1 else "negative" if known else "unknown",
                "basis": "independent_composition" if known else "unknown",
                "source": previous["source"]["composition_url"],
                "source_sha256": chemistry_sha,
                "statement": (
                    f"Published {oxide} assay, row {record['composition_row_number']}; "
                    "zero is unknown."
                ),
                "value": value,
                "units": "oxide_wt_percent",
                "oxide": oxide,
            }
        records.append(record)
    if len({r["measurement_id"] for r in records}) != len(records):
        raise ValueError("Repeated measurement identity")
    for record in records:
        record["former_role"] = record["role"]
        record["role"] = "development"
        record["campaign"] = 0 if record["session_id"].split("testbed-")[-1] < "2016" else 1
    return previous, records


def folds(records: list[dict], indices: np.ndarray, count: int, seed: int) -> list[tuple]:
    groups = np.asarray(connected_groups(records))
    campaigns = np.asarray([r["campaign"] for r in records])
    splitter = StratifiedGroupKFold(n_splits=count, shuffle=True, random_state=seed)
    result = []
    for train, validation in splitter.split(indices, campaigns[indices], groups[indices]):
        a, b = indices[train], indices[validation]
        if set(groups[a]) & set(groups[b]):
            raise ValueError("Related physical/session/family records crossed a fold")
        result.append((a, b))
    return result


def run(root: Path, additional: Path) -> Path:
    previous, records = development_records(root, additional)
    grid = np.linspace(240, 900, 6601)
    old_grid = np.linspace(350, 800, 4501)
    old_slice = (grid >= 350 - 1e-8) & (grid <= 800 + 1e-8)
    reference_path = root / "data/reference/nist_uv_visible_v2/catalog.json"
    catalog = ReferenceCatalog.from_cache(reference_path)
    plans = {
        name: line_plan(catalog, include_uv=include)
        for name, include in (("lines-visible", False), ("lines-uv-visible", True))
    }
    indices = np.arange(len(records))
    outer = folds(records, indices, 5, SEED)
    output = (
        root
        / "reports/adaptation_v2"
        / f"development-{datetime.now(UTC):%Y%m%dT%H%M%S}-{uuid4().hex[:8]}"
    )
    output.mkdir(parents=True)
    plan = {
        "schema_version": 2,
        "scope": "nested_grouped_development_cv_not_independent_test",
        "locked_at": datetime.now(UTC).isoformat(),
        "seed": SEED,
        "C_candidates": CANDIDATES,
        "maximum_inner_oof_point_fpr": 0.05,
        "outer_folds": 5,
        "inner_folds": 3,
        "models": ["v1-bins", "lines-visible", "lines-uv-visible"],
        "primary_candidate": "lines-uv-visible",
        "selection": (
            "inner grouped OOF thresholds and maximum macro balanced accuracy; tie smaller C"
        ),
        "outer_validation_used_for_selection": False,
        "former_final_test_is_development": True,
        "independent_blank_test_status": "not_performed_pending_qualified_controls",
        "reference_sha256": sha(reference_path),
        "feature_plans": plans,
        "additional_intake_sha256": sha(additional),
        "decision_policy": previous["decision_policy"],
        "outer_partition": [
            {
                "fit_ids": [records[i]["measurement_id"] for i in a],
                "validation_ids": [records[i]["measurement_id"] for i in b],
            }
            for a, b in outer
        ],
        "records": records,
    }
    write(output / "development_plan.json", plan)
    X, y = [], []
    for record in records:
        waves, values, _ = read_chemcam_ccs(
            root / record["local_relative_path"], record["input_sha256"]
        )
        X.append(interpolate_observed(waves, values, grid, BANDS, max_gap_nm=0.5))
        y.append(
            [
                {"negative": 0, "positive": 1, "unknown": -1}[record["labels"][c]["state"]]
                for c in CLASSES
            ]
        )
    X, y = np.asarray(X), np.asarray(y)
    features = {name: extract_line_features(X, grid, cfg) for name, cfg in plans.items()}
    groups = [r["physical_sample_id"] for r in records]

    def fit(name: str, train: np.ndarray, C: float):
        if name == "v1-bins":
            return InstrumentAdapter().fit(
                X[train][:, old_slice],
                y[train] == 1,
                y[train] >= 0,
                sample_ids=[groups[i] for i in train],
                grid_nm=old_grid,
                observed_intervals=[[383, 468], [500, 800]],
                class_order=CLASSES,
                instrument_id="chemcam-lanl-testbed",
                C=C,
                seed=SEED,
                provenance={
                    "estimand": "operational_above_threshold",
                    "oxide_policy": previous["decision_policy"],
                    "scope": plan["scope"],
                },
            )
        return LineAdapterV2().fit(
            {c: v[train] for c, v in features[name].items()},
            y[train],
            [groups[i] for i in train],
            C=C,
        )

    def scores(name: str, model, selected: np.ndarray):
        return (
            model.scores(X[selected][:, old_slice])
            if name == "v1-bins"
            else model.scores({c: v[selected] for c, v in features[name].items()})
        )

    def select(name: str, train: np.ndarray, seed: int):
        inner = folds(records, train, 3, seed)
        candidates = []
        for C in CANDIDATES:
            out_of_fold = np.full((len(records), 4), np.nan)
            for a, b in inner:
                out_of_fold[b] = scores(name, fit(name, a, C), b)
            thresholds, report = select_operational_thresholds(
                out_of_fold[train], y[train], [groups[i] for i in train], maximum_fpr=0.05
            )
            candidates.append({"C": C, "thresholds": thresholds.tolist(), "selection": report})
        chosen = max(candidates, key=lambda c: (c["selection"]["macro_balanced_accuracy"], -c["C"]))
        return chosen, candidates

    result = {
        "schema_version": 2,
        "run_id": output.name,
        "status": "running",
        "scope": plan["scope"],
        "independent_blank_test_status": plan["independent_blank_test_status"],
        "models": {},
        "development_plan_sha256": sha(output / "development_plan.json"),
        "implementation_sha256": {
            str(p.relative_to(root)): sha(p)
            for p in [
                Path(__file__).resolve(),
                root / "ml/src/spectralab_ml/models/line_adapter_v2.py",
                root / "ml/src/spectralab_ml/models/instrument_adaptation.py",
                root / "ml/src/spectralab_ml/evaluation/instrument_selection.py",
            ]
        },
    }
    write(output / "result.json", result)
    try:
        # The metrics helper calls these rows 'test'; they are ONLY outer CV validation rows.
        metric_manifest = {
            "schema_version": 1,
            "class_order": CLASSES,
            "estimand": "operational_above_threshold",
            "locked_before_predictions": True,
            "decision_policy": previous["decision_policy"],
            "development": [],
            "test": records,
            "previously_seen": {
                "measurement_ids": [],
                "physical_sample_ids": [],
                "session_ids": [],
                "input_sha256s": [],
            },
            "evaluation_scope": plan["scope"],
        }
        metric_hash = validate_protocol(metric_manifest)["protocol_hash"]
        for name in plan["models"]:
            oof_scores = np.full((len(records), 4), np.nan)
            decisions = np.zeros((len(records), 4), bool)
            selection_reports = []
            for fold, (train, validation) in enumerate(outer):
                chosen, candidates = select(name, train, SEED + fold + 1)
                model = fit(name, train, chosen["C"])
                write(
                    output / name / f"fold-{fold}-selection.json",
                    {"chosen": chosen, "candidates": candidates},
                )
                oof_scores[validation] = scores(name, model, validation)
                decisions[validation] = oof_scores[validation] >= np.asarray(chosen["thresholds"])
                selection_reports.append({"fold": fold, **chosen})
                print(f"{name}: outer fold {fold + 1}/5 complete", flush=True)
            metrics = evaluate_detections(
                metric_manifest,
                decisions,
                measurement_ids=[r["measurement_id"] for r in records],
                expected_protocol_hash=metric_hash,
                bootstrap_repeats=2000,
                bootstrap_seed=SEED,
            )
            metrics["evaluation_scope"] = plan["scope"]
            metrics["independent_final_test"] = False
            np.savez_compressed(
                output / name / "development_oof.npz", scores=oof_scores, detections=decisions
            )
            result["models"][name] = {"metrics": metrics, "outer_selections": selection_reports}
            write(output / "result.json", result)
        chosen, candidates = select("lines-uv-visible", indices, SEED + 100)
        final_model = fit("lines-uv-visible", indices, chosen["C"])
        final_model.save(
            output / "profile.json",
            plan=plans["lines-uv-visible"],
            thresholds=chosen["thresholds"],
            provenance={
                "scope": plan["scope"],
                "instrument_id": "chemcam-lanl-testbed",
                "estimand": "operational_above_threshold",
                "oxide_policy": previous["decision_policy"],
                "development_plan_sha256": result["development_plan_sha256"],
                "training_measurement_ids": [r["measurement_id"] for r in records],
            },
        )
        loaded, _ = LineAdapterV2.load(output / "profile.json")
        np.testing.assert_array_equal(
            loaded.scores(features["lines-uv-visible"]),
            final_model.scores(features["lines-uv-visible"]),
        )
        result.update(
            status="completed",
            completed_at=datetime.now(UTC).isoformat(),
            selected_profile={
                "selection": chosen,
                "candidates": candidates,
                "path": str((output / "profile.json").relative_to(root)),
                "sha256": sha(output / "profile.json"),
            },
            qualification="development_only_no_independent_blank_validation",
        )
        write(output / "result.json", result)
        write(root / "docs/instrument_v2_summary.json", result)
        print(output, flush=True)
    except Exception as exc:
        result.update(status="failed", error=f"{type(exc).__name__}: {exc}")
        write(output / "result.json", result)
        raise
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--additional-intake", type=Path, required=True)
    args = parser.parse_args()
    run(repository_root(), args.additional_intake)


if __name__ == "__main__":
    main()
