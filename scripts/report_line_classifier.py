"""Summarize completed matched synthetic comparisons without fitting or tuning."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from spectralab_ml.config import repository_root


def source_series(path: Path) -> tuple[dict, dict]:
    raw = (path / "result.json").read_bytes()
    value = json.loads(raw)
    if (
        value.get("status") != "completed"
        or value.get("failed_runs")
        or value.get("incomplete_runs")
        or value.get("successful_seeds") != value.get("expected_seeds")
    ):
        raise ValueError("Only complete matched series can be reported")
    try:
        relative = str(path.resolve().relative_to(repository_root()))
    except ValueError:
        relative = str(path.resolve())
    return value, {"path": relative, "result_sha256": hashlib.sha256(raw).hexdigest()}


def paired_differences(path: Path, series: dict) -> list[dict]:
    runs = [json.loads(p.read_text()) for p in sorted((path / "runs").glob("*/result.json"))]
    if sorted(r["seed"] for r in runs) != sorted(series["successful_seeds"]):
        raise ValueError("Seed sources do not match the completed series")
    comparisons = []
    for value in series["configuration"]["values"]:
        deltas = []
        for run in runs:
            cells = {r["model_id"]: r for r in run["runs"] if r["value"] == value}
            baseline, candidate = cells["logistic-ovr"], cells["line-logistic"]
            if baseline["test_base_sample_ids"] != candidate["test_base_sample_ids"]:
                raise ValueError("Comparison requires identical controlled sample IDs")
            deltas.append(candidate["metrics"]["micro_f1"] - baseline["metrics"]["micro_f1"])
        comparisons.append(
            {
                "value": value,
                "candidate_minus_logistic_f1": deltas,
                "mean_delta": float(np.mean(deltas)),
                "min_delta": float(min(deltas)),
                "max_delta": float(max(deltas)),
            }
        )
    return comparisons


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--noise-run", type=Path, required=True)
    parser.add_argument("--drift-run", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path("docs/line_classifier_v1_summary.json"))
    parser.add_argument("--figure", type=Path, default=Path("docs/assets/line_classifier_v1.png"))
    args = parser.parse_args()
    datasets = [source_series(path) for path in (args.noise_run, args.drift_run)]
    summary = {
        "schema_version": 1,
        "scope": "synthetic development comparison; not real-data or independent final validation",
        "seeds": datasets[0][0]["successful_seeds"],
        "limitations": [
            "18 fixed compositions per condition, repeated over five training/noise seeds",
            "Conditions share compositions; seed SD is not a physical confidence interval",
            "Neither model architecture nor thresholds are fitted on controlled stress spectra",
            "No quantitative concentration claims and no ChemCam profile replacement",
        ],
        "series": [],
    }
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.2))
    for axis, path, (series, source) in zip(
        axes, (args.noise_run, args.drift_run), datasets, strict=True
    ):
        config = series["configuration"]
        summary["series"].append(
            {
                "source": source,
                "reference_metadata": series["reference_metadata"],
                "protocol_hash": series["protocol_hash"],
                "source_hash": series["source_hash"],
                "configuration": config,
                "aggregates": series["aggregates"],
                "paired_vs_logistic": paired_differences(path, series),
            }
        )
        for model_id in config["models"]:
            rows = [row for row in series["aggregates"] if row["model_id"] == model_id]
            means = [row["metrics"]["micro_f1"]["mean"] for row in rows]
            std = [row["metrics"]["micro_f1"]["std"] or 0 for row in rows]
            x_values = (
                config["values"]
                if config["factor"] == "calibration_shift_nm"
                else list(range(len(rows)))
            )
            axis.errorbar(x_values, means, yerr=std, marker="o", label=model_id, capsize=2)
        axis.set_xticks(
            x_values,
            ["clean" if v is None else str(v) for v in config["values"]],
        )
        axis.set(xlabel=config["factor"], ylabel="micro-F1 (mean +/- seed SD)", ylim=(0, 1.05))
        axis.grid(alpha=0.2)
    axes[0].set_title("Noise: FWHM 1.0 nm, shift 0.15 nm")
    axes[1].set_title("Axis drift: FWHM 0.5 nm, SNR 10 dB")
    axes[1].legend(fontsize=8, loc="lower left")
    fig.suptitle("Synthetic demo reference only; 5 matched seeds")
    fig.tight_layout()
    args.figure.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(args.figure, dpi=160)
    plt.close(fig)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(summary, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
    )
    print(args.output)
    print(args.figure)


if __name__ == "__main__":
    main()
