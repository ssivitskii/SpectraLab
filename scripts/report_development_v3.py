"""Render completed development OOF results without fitting or choosing models."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

CLASSES = ("Na", "Mg", "Ca", "Fe")
VARIANTS = ("v2-line-baseline", "continuum-snr-neutral", "continuum-snr-ion")
NAMES = ("v2 line baseline", "Continuum + SNR, I", "Continuum + SNR, I + II")
MARKER = "\n## Completed locked run\n"


def file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def number(value, digits=3):
    return "undefined" if value is None else f"{value:.{digits}f}"


def interval(value, ci):
    if ci is None:
        return number(value)
    return f"{number(value)} [{number(ci['lower'])}, {number(ci['upper'])}]"


def table(headers, rows):
    return "\n".join(
        ["| " + " | ".join(headers) + " |", "| " + " | ".join(["---"] * len(headers)) + " |"]
        + ["| " + " | ".join(row) + " |" for row in rows]
    )


def without_bins(value):
    if isinstance(value, dict):
        return {k: without_bins(v) for k, v in value.items() if k != "bins"}
    if isinstance(value, list):
        return [without_bins(v) for v in value]
    return value


def render(run_dir: Path, destination: Path, additional_runs: list[Path] | None = None) -> None:
    result = json.loads((run_dir / "result.json").read_text())
    protocol = json.loads((run_dir / "protocol.json").read_text())
    if (
        result.get("status") != "completed"
        or result.get("final_test_status") != "not_performed"
        or result["protocol_sha256"] != file_sha(run_dir / "protocol.json")
        or protocol["models"] != list(VARIANTS)
        or protocol["outer_used_for_selection"] is not False
        or protocol["seed"] != 20261019
        or result["support"] != {"spectra": 202, "physical": 199, "connected_groups": 46}
    ):
        raise ValueError("Completed matching development-only run required")
    models = result["models"]
    primary = models[result["primary_candidate"]]["metrics"]["per_class"]
    figure = destination.parent / "assets/development_v3.png"
    figure.parent.mkdir(parents=True, exist_ok=True)
    colors = ("#777777", "#0072B2", "#D55E00")
    fig, axes = plt.subplots(2, 2, figsize=(12, 8), constrained_layout=True)
    x = np.arange(4)
    for j, (variant, name, color) in enumerate(zip(VARIANTS, NAMES, colors, strict=True)):
        rows = models[variant]["metrics"]["per_class"]
        values = np.array([rows[c]["calibrated_reliability"]["brier"] for c in CLASSES])
        lower = np.array([rows[c]["confidence_intervals"]["brier"]["lower"] for c in CLASSES])
        upper = np.array([rows[c]["confidence_intervals"]["brier"]["upper"] for c in CLASSES])
        axes[0, 0].errorbar(
            x + (j - 1) * 0.15,
            values,
            yerr=np.maximum(np.vstack((values - lower, upper - values)), 0),
            fmt="o",
            capsize=3,
            color=color,
            label=name,
        )
    axes[0, 0].set(title="Calibrated Brier (lower is better)", xticks=x, xticklabels=CLASSES)
    axes[0, 0].legend(fontsize=8)
    for c, color in zip(CLASSES, ("#0072B2", "#D55E00", "#009E73", "#CC79A7"), strict=True):
        bins = [b for b in primary[c]["calibrated_reliability"]["bins"] if b["physical_weight"] > 0]
        axes[0, 1].plot(
            [b["mean_probability"] for b in bins],
            [b["positive_fraction"] for b in bins],
            "o-",
            color=color,
            label=c,
        )
    axes[0, 1].plot([0, 1], [0, 1], "--", color="#888888", linewidth=1)
    axes[0, 1].set(
        title="Primary model: OOF reliability",
        xlabel="Mean calibrated probability",
        ylabel="Observed positive fraction",
        xlim=(-0.03, 1.03),
        ylim=(-0.03, 1.03),
    )
    axes[0, 1].legend(fontsize=8)
    for j, (key, name, color) in enumerate(
        (
            ("coverage_known", "Known-label coverage", "#0072B2"),
            ("positive_capture_including_refusals", "Capture / all positives", "#D55E00"),
        )
    ):
        axes[1, 0].bar(
            x + (j - 0.5) * 0.35,
            [primary[c]["selective"][key] for c in CLASSES],
            0.35,
            label=name,
            color=color,
        )
    axes[1, 0].set(
        title="Primary model: coverage and retained sensitivity",
        xticks=x,
        xticklabels=CLASSES,
        ylim=(0, 1.05),
    )
    axes[1, 0].legend(fontsize=8)
    for j, (key, name, color) in enumerate(
        (
            ("selective_fpr", "FPR among accepted negatives", "#D55E00"),
            ("selective_recall", "Recall among accepted positives", "#0072B2"),
        )
    ):
        values = np.array([primary[c]["selective"][key] for c in CLASSES])
        lo = np.array([primary[c]["confidence_intervals"][key]["lower"] for c in CLASSES])
        hi = np.array([primary[c]["confidence_intervals"][key]["upper"] for c in CLASSES])
        axes[1, 1].errorbar(
            x + (j - 0.5) * 0.15,
            values,
            yerr=np.maximum(np.vstack((values - lo, hi - values)), 0),
            fmt="o",
            capsize=3,
            color=color,
            label=name,
        )
    axes[1, 1].set(
        title="Primary model: selective errors and uncertainty",
        xticks=x,
        xticklabels=CLASSES,
        ylim=(-0.03, 1.03),
    )
    axes[1, 1].legend(fontsize=8)
    for ax in axes.ravel():
        ax.grid(alpha=0.2, axis="y")
    fig.suptitle(
        "Development OOF only • 202 spectra / 199 materials / 46 linked groups", fontsize=13
    )
    fig.savefig(figure, dpi=160)
    plt.close(fig)

    macro_rows, calibration_rows, difference_rows = [], [], []
    for variant, name in zip(VARIANTS, NAMES, strict=True):
        metrics = models[variant]["metrics"]
        macro = metrics["macro"]
        macro_rows.append(
            [name]
            + [
                number(macro[key])
                for key in (
                    "raw_brier",
                    "calibrated_brier",
                    "calibrated_ece",
                    "coverage_known",
                    "selective_fpr",
                    "selective_recall",
                    "positive_capture_including_refusals",
                )
            ]
        )
        difference = models[variant]["calibration_paired_brier"]["differences"]["macro"]
        difference_rows.append(
            [name + ": calibrated minus raw", interval(difference["point"], difference)]
        )
        for c in CLASSES:
            q = metrics["per_class"][c]
            calibration_rows.append(
                [
                    name,
                    c,
                    number(q["raw_score_reliability_diagnostic"]["brier"]),
                    number(q["calibrated_reliability"]["brier"]),
                    number(q["raw_score_reliability_diagnostic"]["ece"]),
                    number(q["calibrated_reliability"]["ece"]),
                ]
            )
    for variant, comparison in result["paired_comparisons_to_baseline"].items():
        q = comparison["differences"]["macro"]
        difference_rows.append([variant + " minus calibrated baseline", interval(q["point"], q)])
    support_rows, error_rows, campaign_rows = [], [], []
    for c in CLASSES:
        q = primary[c]
        m = q["selective"]
        ci = q["confidence_intervals"]
        support_rows.append(
            [c]
            + [
                number(m[k], 0)
                for k in (
                    "positive_physical_weight",
                    "negative_physical_weight",
                    "unknown_label_physical_weight",
                )
            ]
            + [
                interval(m[k], ci[k])
                for k in (
                    "coverage_known",
                    "selective_fpr",
                    "selective_recall",
                    "positive_capture_including_refusals",
                )
            ]
        )
        error_rows.append(
            [c]
            + [
                number(m[k], 3)
                for k in (
                    "accepted_true_positive_weight",
                    "accepted_false_positive_weight",
                    "accepted_false_negative_weight",
                    "accepted_true_negative_weight",
                    "refused_positive_weight",
                    "refused_negative_weight",
                    "unknown_truth_acceptance",
                )
            ]
        )
    for campaign, rows in models[result["primary_candidate"]]["metrics"]["by_campaign"].items():
        for c in CLASSES:
            q = rows[c]
            campaign_rows.append(
                [campaign, c, number(q["calibrated_reliability"]["brier"])]
                + [
                    number(q["selective"][k])
                    for k in (
                        "coverage_known",
                        "selective_fpr",
                        "selective_recall",
                        "positive_capture_including_refusals",
                    )
                ]
            )
    summary = without_bins(result)
    summary.update(
        result_path=str(run_dir / "result.json"),
        result_sha256=file_sha(run_dir / "result.json"),
        protocol_path=str(run_dir / "protocol.json"),
        source_plan=protocol["source_plan"],
        implementation_sha256=protocol["implementation_sha256"],
        environment=protocol["environment"],
    )
    sensitivity_section = ""
    if additional_runs:
        audit_path = Path("data/reference/development_v3_sensitivity_audit.json")
        audit = json.loads(audit_path.read_text())
        run_results = [(run_dir, result, protocol)]
        for path in additional_runs:
            q = json.loads((path / "result.json").read_text())
            p = json.loads((path / "protocol.json").read_text())
            if (
                q["status"] != "completed"
                or q["protocol_sha256"] != file_sha(path / "protocol.json")
                or p["source_plan"]["sha256"] != protocol["source_plan"]["sha256"]
                or p["implementation_sha256"] != protocol["implementation_sha256"]
            ):
                raise ValueError("Sensitivity runs must have matched sources and implementation")
            run_results.append((path, q, p))
        if sorted(p["seed"] for _, _, p in run_results) != [20261019, *audit["sensitivity_seeds"]]:
            raise ValueError("Exact complete post-primary sensitivity seed list required")
        sensitivity = {
            "audit_path": str(audit_path),
            "audit_sha256": file_sha(audit_path),
            "post_primary": True,
            "deployment_selection": False,
            "interpretation": (
                "Seed variability on the same development records; not independent data"
            ),
            "runs": [
                {
                    "path": str(path),
                    "result_sha256": file_sha(path / "result.json"),
                    "seed": p["seed"],
                    "run_id": q["run_id"],
                    "macro": {v: q["models"][v]["metrics"]["macro"] for v in VARIANTS},
                }
                for path, q, p in run_results
            ],
            "aggregate": {},
        }
        sensitivity_rows = []
        keys = (
            "calibrated_brier",
            "calibrated_ece",
            "coverage_known",
            "selective_fpr",
            "selective_recall",
            "positive_capture_including_refusals",
        )
        for variant, name in zip(VARIANTS, NAMES, strict=True):
            aggregate = {}
            for key in keys:
                values = [q["models"][variant]["metrics"]["macro"][key] for _, q, _ in run_results]
                aggregate[key] = {
                    "mean": float(np.mean(values)),
                    "sample_sd": float(np.std(values, ddof=1)),
                    "count": len(values),
                }
            sensitivity["aggregate"][variant] = aggregate
            sensitivity_rows.append(
                [name]
                + [f"{aggregate[k]['mean']:.3f} ± {aggregate[k]['sample_sd']:.3f}" for k in keys]
            )
        summary["post_primary_sensitivity"] = sensitivity
        sensitivity_table = table(
            [
                "Variant",
                "Cal. Brier",
                "Cal. ECE",
                "Known coverage",
                "Selective FPR",
                "Selective recall",
                "Capture / all positives",
            ],
            sensitivity_rows,
        )
        sensitivity_section = f"""
### Post-primary sensitivity audit

After seeing the primary result, the four additional seeds 20261020–20261023
were fixed in a coordination message before their execution. The audit JSON was
materialized after execution began, before inspecting these additional results;
it is not represented as a pre-primary preregistration. All five seeds completed
with identical data, feature variants and implementation hashes. No model,
threshold rule or deployment candidate was changed or chosen using this audit.

{sensitivity_table}

Mean ± sample standard deviation (ddof=1) across five matched split seeds
describes partition variability on the same 199 materials. It is not a confidence
interval or five independent datasets. The deployment profile remains the original
seed 20261019 profile. The audit source is `{audit_path}`.
"""
    summary_path = destination.with_name(destination.stem + "_summary.json")
    summary_path.write_text(json.dumps(summary, indent=2, allow_nan=False) + "\n")
    command_arguments = [f"--run-dir {run_dir}"] + [
        f"--additional-run {path}" for path in (additional_runs or [])
    ]
    report_arguments = " \\\n  ".join(command_arguments)
    original = destination.read_text().split(MARKER)[0]
    original = original.replace("Results will be appended after the locked run completes.\n", "")
    tables = {
        "macro": table(
            [
                "Variant",
                "Raw Brier",
                "Calibrated Brier",
                "Cal. ECE",
                "Known coverage",
                "Selective FPR",
                "Selective recall",
                "Capture / all positives",
            ],
            macro_rows,
        ),
        "difference": table(
            ["Paired macro Brier difference (negative is better)", "Estimate [95% interval]"],
            difference_rows,
        ),
        "support": table(
            [
                "Element",
                "Positive",
                "Negative",
                "Unknown",
                "Known coverage [CI]",
                "Selective FPR [CI]",
                "Selective recall [CI]",
                "Capture / all positives [CI]",
            ],
            support_rows,
        ),
        "errors": table(
            [
                "Element",
                "Accepted TP",
                "Accepted FP",
                "Accepted FN",
                "Accepted TN",
                "Refused positive",
                "Refused negative",
                "Unknown-truth acceptance",
            ],
            error_rows,
        ),
        "campaign": table(
            [
                "Campaign",
                "Element",
                "Cal. Brier",
                "Known coverage",
                "Selective FPR",
                "Selective recall",
                "Capture / all positives",
            ],
            campaign_rows,
        ),
    }
    section = f"""{MARKER}
Run `{result["run_id"]}` completed in {result["seconds"]:.1f} seconds.
All 15 outer model/fold fits completed on the same five partitions, followed by
the separately partitioned deployment profile. Protocol SHA256:
`{result["protocol_sha256"]}`.

{tables["macro"]}

These macro values average the four elements. Raw balanced logistic scores are
shown as a diagnostic; they are not claimed to be calibrated probabilities.
The paired intervals below are exploratory development uncertainty, not final
qualification or evidence of performance on certified blanks.

{tables["difference"]}

The I + II feature package has lower development Brier than the calibrated baseline
in this primary run. The paired interval for Platt calibration itself crosses zero,
so it does not establish a separate calibration improvement. The refusal policy
is almost inactive: it rejects one known-negative Na material and no known positive
in the primary OOF evaluation. It does not demonstrate improved selective safety.
Ca's OOF FPR exceeds the tuning budget. These results do not qualify the model for
practical use or replace independent external/blank evaluation.

![Development comparison](assets/development_v3.png)

### Primary candidate: support and retained sensitivity

{tables["support"]}

Intervals resample 46 linked groups. Fe has only 25 known negative physical
materials across 12 linked groups. Unknown truth is excluded from FPR/recall;
acceptance on these rows can be counted but its correctness cannot be measured.
Mg has no observed false positives: its bootstrap interval collapses to [0, 0].
This degeneracy does not establish zero population FPR or bound unseen failures;
an independent negative-control evaluation is still required.

{tables["errors"]}

Confusion counts are physical-material weights: repeated measurements total one
per material. Selective recall divides accepted true positives by all accepted
positives, including accepted false negatives. Capture divides by all positives,
including refusals; this prevents refusal from concealing lost sensitivity.

### Calibration detail

{table(["Variant", "Element", "Raw Brier", "Cal. Brier", "Raw ECE", "Cal. ECE"], calibration_rows)}

The calibration population consists of the known extreme operational labels,
not all chemical compositions. Zero/missing and strictly 0.1 < x < 1 oxide wt%
remain unknown;
these probabilities are not concentration estimates or calibrated for those
unknown states. ECE uses ten fixed equal-width bins and is sample-size sensitive.

### Campaign slices of the primary candidate

{tables["campaign"]}

Campaign slices are descriptions of held-out group predictions within development,
not an untouched campaign transfer test. Campaign 0 means acquisition sessions
before 2016-01-01; campaign 1 means sessions on or after that date. These are
acquisition-date groups, not archive release directories: some calib_2021 files,
including additional disclosed Mg-low materials, were acquired before 2016.

{sensitivity_section}

### Reproduction and audit

The numeric deployment artifact is `{result["profile"]["path"]}`,
SHA256 `{result["profile"]["sha256"]}`. It contains the fixed feature plan,
logistic coefficients/scalers, monotonic calibrators, thresholds, refusal policy,
instrument identity and operational oxide policy. `LineAdapterV3.load` validates
one bounded no-follow JSON buffer, numeric dimensions, fixed semantics and digest.

`protocol.json` contains all source IDs/checksums and every outer/inner assignment.
Each fold saves its numeric profile, candidate fit/calibration/tuning scores and
selection audit; `development_oof.npz` saves per-record probabilities, decisions,
refusals, SNR, truth and cluster IDs. The same records/partitions support all models.
The deployment primary is fixed before outer scoring and is not chosen from this
table. Its in-sample scores are never used as reported validation performance.

Regenerate this report with:

```sh
scripts/uv.sh run --frozen --all-packages --no-sync python scripts/report_development_v3.py \\
  {report_arguments}
scripts/uv.sh run --frozen --all-packages --no-sync pytest -q \\
  ml/tests/test_line_adapter_v3.py ml/tests/test_development_v3.py
```

The model remains **development-only**. Real response calibration, independent
campaign transfer, certified low-Fe/Fe–Mg blanks and an untouched final assessment
remain separate data and qualification requirements. A tuning FPR budget does
not guarantee that OOF or future FPR satisfies that budget.
"""
    destination.write_text(original.rstrip() + "\n" + section)
    print(
        json.dumps(
            {"document": str(destination), "summary": str(summary_path), "figure": str(figure)},
            indent=2,
        )
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--destination", type=Path, default=Path("docs/development_v3.md"))
    parser.add_argument("--additional-run", type=Path, action="append", default=[])
    args = parser.parse_args()
    render(args.run_dir, args.destination, args.additional_run)


if __name__ == "__main__":
    main()
