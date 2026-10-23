"""Compare frozen line-window variants on paired synthetic trace development data."""

from __future__ import annotations

import argparse
import gc
import hashlib
import json
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
from spectralab_ml.config import load_project_config, load_reference_catalog, repository_root
from spectralab_ml.datasets import build_demo_dataset
from spectralab_ml.evaluation import evaluate_multilabel
from spectralab_ml.evaluation.trace import (
    TRACE_CONDITIONS,
    build_trace_condition,
    trace_detection_metrics,
)
from spectralab_ml.models import load_artifact
from spectralab_ml.preprocessing import SpectrumPreprocessor
from spectralab_ml.training import train_and_save_models


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seeds", type=int, nargs="+", default=[7, 17, 29, 43, 71])
    parser.add_argument("--base-samples", type=int, default=144)
    parser.add_argument("--repeats", type=int, default=2)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--summary", type=Path, help="Optional exported compact report JSON")
    args = parser.parse_args()
    if len(set(args.seeds)) != len(args.seeds) or any(seed < 0 for seed in args.seeds):
        parser.error("Seeds must be unique non-negative integers")
    project = load_project_config()
    catalog = load_reference_catalog(project)
    processor = SpectrumPreprocessor(
        project.wavelength_start_nm, project.wavelength_end_nm, project.model_step_nm
    )
    output = args.output or repository_root() / "reports/experiments" / datetime.now(UTC).strftime(
        "line-trace-v2-%Y%m%dT%H%M%S"
    )
    output.mkdir(parents=True, exist_ok=False)
    report = {
        "schema_version": 1,
        "status": "running",
        "scope": "synthetic development; not independent final or real-data validation",
        "created_at": datetime.now(UTC).isoformat(),
        "run_directory": str(output.resolve()),
        "seeds": args.seeds,
        "base_samples_per_variant": args.base_samples,
        "variations": 2,
        "repeats": args.repeats,
        "conditions": [asdict(c) for c in TRACE_CONDITIONS],
        "reference": catalog.metadata,
        "project": asdict(project),
        "protocol": {
            "architecture": "same multi-scale windows; robust classifier is element-local",
            "training": (
                "demo vs line_robust_v1; same group IDs and counts, "
                "different compositions/conditions"
            ),
            "thresholds": "per-model validation only; no controlled-condition tuning",
            "evaluation": "every ordered pair; independent noise seeds; paired arrays",
            "controls": "ratio zero removes target, never relabels an unknown as absent",
            "caveat": (
                "conditions share compositions/noise seeds; "
                "SD across five seeds is not a physical confidence interval"
            ),
        },
        "source_sha256": {
            name: hashlib.sha256((repository_root() / name).read_bytes()).hexdigest()
            for name in (
                "ml/src/spectralab_ml/datasets.py",
                "ml/src/spectralab_ml/models/line_classifier.py",
                "ml/src/spectralab_ml/evaluation/trace.py",
                "scripts/benchmark_line_trace.py",
            )
        },
        "training": [],
        "rows": [],
    }
    for seed in args.seeds:
        models = []
        for profile, model_id in (
            ("demo", "line-logistic"),
            ("line_robust_v1", "line-logistic-robust"),
        ):
            dataset = build_demo_dataset(
                catalog,
                processor,
                project.elements,
                seed=seed,
                base_samples=args.base_samples,
                variations=2,
                ion_stage=project.ion_stage,
                generation_profile=profile,
            )
            path = output / f"seed-{seed}"
            metrics = train_and_save_models(
                dataset,
                catalog,
                processor,
                path,
                seed=seed,
                model_ids=[model_id],
                timing_warmups=1,
                timing_repeats=2,
            )
            model, _, manifest = load_artifact(path / model_id)
            models.append(
                (model_id, model, np.array([manifest.thresholds[e] for e in project.elements]))
            )
            report["training"].append(
                {
                    "seed": seed,
                    "model_id": model_id,
                    "profile": profile,
                    "dataset_hash": dataset.dataset_hash,
                    "split_hash": dataset.split_hash,
                    "split_ids": dataset.split_ids,
                    "thresholds": manifest.thresholds,
                    "model_sha256": manifest.model_sha256,
                    "heldout_metrics": metrics[model_id],
                }
            )
            del dataset
            gc.collect()
        for condition in TRACE_CONDITIONS:
            X, y, targets, metadata = build_trace_condition(
                catalog,
                processor,
                project.elements,
                condition,
                seed=seed,
                repeats=args.repeats,
                ion_stage=project.ion_stage,
            )
            trace_ids = {record["base_sample_id"] for record in metadata["records"]}
            if any(
                trace_ids & set(ids)
                for entry in report["training"]
                for ids in entry["split_ids"].values()
            ):
                raise ValueError("Trace evaluation group overlaps training/development")
            for model_id, model, thresholds in models:
                scores = model.scores(X)
                report["rows"].append(
                    {
                        "seed": seed,
                        "model_id": model_id,
                        "condition": condition.name,
                        "evaluation_hash": metadata["dataset_hash"],
                        "metrics": evaluate_multilabel(y, scores, thresholds, project.elements),
                        "trace": trace_detection_metrics(y, scores >= thresholds, targets),
                    }
                )
        print(f"seed={seed} complete", flush=True)
        (output / "summary.json").write_text(
            json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
        )
    report["aggregate"] = []
    for condition in TRACE_CONDITIONS:
        for model_id, _, _ in models:
            selected = [
                row
                for row in report["rows"]
                if row["condition"] == condition.name and row["model_id"] == model_id
            ]
            aggregate = {"condition": condition.name, "model_id": model_id}
            for key in (
                "micro_f1",
                "exact_match_accuracy",
                "weak_target_recall",
                "target_false_positive_rate",
                "false_positive_rate",
            ):
                values = [
                    row["metrics" if key in {"micro_f1", "exact_match_accuracy"} else "trace"][key]
                    for row in selected
                ]
                aggregate[key] = (
                    None
                    if values[0] is None
                    else {
                        "mean": float(np.mean(values)),
                        "sd": float(np.std(values, ddof=1)) if len(values) > 1 else 0,
                        "per_seed": values,
                    }
                )
            report["aggregate"].append(aggregate)
    report["status"] = "completed"
    rendered = json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
    (output / "summary.json").write_text(rendered)
    if args.summary:
        args.summary.parent.mkdir(parents=True, exist_ok=True)
        args.summary.write_text(rendered)
    print(output / "summary.json")


if __name__ == "__main__":
    main()
