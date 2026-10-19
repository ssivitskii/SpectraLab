"""Summarize every fixed adaptive calcium attempt without selecting an outer winner."""

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

ROUND_NAMES = ("verified_round1", "crossfit_round2", "zero_budget_round3")


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def describe(values):
    return {
        "mean": float(np.mean(values)),
        "sample_std": float(np.std(values, ddof=1)),
        "count": len(values),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--round1", required=True, type=Path)
    parser.add_argument("--round2", required=True, type=Path)
    parser.add_argument("--round3", required=True, type=Path)
    args = parser.parse_args()
    root = repository_root()
    original = root / "reports/adaptation_v4/calcium-20261019T184555-b17b9d8d"
    paths = {
        "original_round1": original,
        "verified_round1": args.round1.resolve(),
        "crossfit_round2": args.round2.resolve(),
        "zero_budget_round3": args.round3.resolve(),
    }
    results = {k: json.loads((p / "result.json").read_text()) for k, p in paths.items()}
    if any(r["status"] != "completed" for r in results.values()):
        raise ValueError("Every reported attempt must have completed")
    if results["original_round1"]["seeds"] != results["verified_round1"]["seeds"]:
        raise ValueError("I/O-only verification rerun changed scientific results")
    summary = {
        "schema_version": 4,
        "scope": "adaptive_development_only",
        "final_test_status": "not_performed",
        "independent_final_claim": False,
        "source_spectra": 202,
        "physical_materials": 199,
        "ca_known_positive_physical_weight": 93,
        "ca_known_negative_physical_weight": 51,
        "original_round1_rerun_exact_seed_results": True,
        "primary_selector": "global@0.025 in rounds1/2; global@0.0 in round3",
        "rounds": {},
    }
    for name in ROUND_NAMES:
        run, path = results[name], paths[name]
        per_seed = []
        for seed in run["seeds"]:
            m, base = seed["selectors"][run["primary_selector"]], seed["baseline"]
            per_seed.append(
                {
                    "seed": seed["seed"],
                    "baseline": base,
                    "primary": m,
                    "matched_recall_delta": m["recall"] - base["recall"],
                    "matched_fpr_delta": m["fpr"] - base["fpr"],
                }
            )
        summary["rounds"][name] = {
            "path": str(path.relative_to(root)),
            "result_sha256": sha(path / "result.json"),
            "lock_sha256": sha(path / "lock.json"),
            "seconds": run["seconds"],
            "research_profile": run["research_profile"],
            "deployment_selection": json.loads((path / "research_profile.json").read_text())[
                "provenance"
            ]["candidate"],
            "seeds": per_seed,
            "strict_absolute_recall_target_all_seeds": run["robust_point_target_achieved"],
            "secondary_matched_target_all_seeds": (
                per_seed[0]["primary"]["true_positive"] >= 83
                and all(
                    s["primary"]["fpr"] <= 0.05 and s["matched_recall_delta"] >= -0.02
                    for s in per_seed
                )
            ),
            "primary_split_variation": {
                key: describe([s["primary"][key] for s in per_seed]) for key in ("recall", "fpr")
            },
            "all_inner_selectors": {
                name: {
                    key: describe([s["selectors"][name][key] for s in run["seeds"]])
                    for key in ("recall", "fpr")
                }
                for name in run["seeds"][0]["selectors"]
            },
            "all_fixed_candidate_attempts": [
                {"seed": s["seed"], "metrics": s["all_fixed_attempts"]} for s in run["seeds"]
            ],
        }
    summary["original_unverified_input_run"] = {
        "path": str(original.relative_to(root)),
        "result_sha256": sha(original / "result.json"),
        "limitation": (
            "Initial candidate lock did not pin OOF/profile bytes. Amended rerun "
            "verifies original v3 result profile SHA, all OOF identities/labels/groups "
            "and feature plan; all seed result objects are exactly equal."
        ),
    }
    summary["uncertainty"] = (
        "Paired 2000 cluster resamples of fixed OOF errors; conditional on fitted models, "
        "not full-refit uncertainty. Split seeds reuse the same materials. Zero-FP bootstrap "
        "intervals are not population upper bounds. No outer winner was promoted."
    )
    out = root / "docs/calcium_v4_summary.json"
    out.write_text(json.dumps(summary, indent=2, allow_nan=False) + "\n")
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.5), constrained_layout=True)
    x = np.arange(5)
    base = results["verified_round1"]["seeds"]
    for ax, metric in zip(axes, ("fpr", "recall"), strict=True):
        ax.plot(
            x,
            [100 * s["baseline"][metric] for s in base],
            "o--",
            color="#6b7280",
            label="v3 matched",
        )
        for name, label, color in (
            ("verified_round1", "Round 1: one model", "#c2802d"),
            ("crossfit_round2", "Round 2: mean of 4", "#176b91"),
            ("zero_budget_round3", "Round 3: zero inner FP", "#39753c"),
        ):
            ax.plot(
                x,
                [
                    100 * s["selectors"][results[name]["primary_selector"]][metric]
                    for s in results[name]["seeds"]
                ],
                "o-",
                color=color,
                label=label,
            )
        ax.set_xticks(x, [str(s["seed"])[-2:] for s in base])
        ax.set_xlabel("Matched seed (202610xx)")
        ax.set_ylabel(metric.upper() + " (%)")
        ax.spines[["top", "right"]].set_visible(False)
        ax.grid(axis="y", alpha=0.2)
    axes[0].axhline(5, color="#a43432", linestyle=":", label="FPR target 5%")
    axes[1].plot(
        x,
        [100 * (s["baseline"]["recall"] - 0.02) for s in base],
        ":",
        color="#a43432",
        label="Matched recall −2 points",
    )
    axes[0].legend(fontsize=8)
    axes[1].legend(fontsize=8)
    fig.suptitle("Adaptive development: Ca decisions are unconditional; no independent final test")
    fig.savefig(root / "docs/calcium_v4_results.png", dpi=170)
    plt.close(fig)
    lines = [
        "# Calcium specificity: adaptive development",
        "",
        "All three rounds use only the already disclosed 202 spectra / 199 physical materials. "
        "Ca support is 93 positive and 51 negative physical weights. These are adaptive "
        "development results, not an independent final test. Ca decisions are unconditional; "
        "refusal cannot remove false negatives from recall.",
        "",
        "| Seed | v3 TP / FP | Round 1 TP / FP | Round 2 TP / FP | Round 3 TP / FP |",
        "|---|---:|---:|---:|---:|",
    ]
    for a, b, c in zip(
        summary["rounds"]["verified_round1"]["seeds"],
        summary["rounds"]["crossfit_round2"]["seeds"],
        summary["rounds"]["zero_budget_round3"]["seeds"],
        strict=True,
    ):
        cells = [a["baseline"], a["primary"], b["primary"], c["primary"]]
        lines.append(
            f"| {a['seed']} | "
            + " | ".join(f"{v['true_positive']:g} / {v['false_positive']:g}" for v in cells)
            + " |"
        )
    lines += [
        "",
        "True-positive counts can be fractional because replicates of one physical "
        "material share weight one. All negatives here are unique. The primary target is "
        "at least 83/93 positives and at most 2/51 negatives detected. The original strict "
        "five-seed criterion requires this absolute target on all seeds. A separately "
        "declared secondary criterion preserves each seed's own v3 recall within 2 percentage "
        "points, requires FPR ≤5% on each seed, and retains ≥83 TP on the primary seed.",
        "",
    ]
    for key, label in (
        ("verified_round1", "Round 1"),
        ("crossfit_round2", "Round 2"),
        ("zero_budget_round3", "Round 3"),
    ):
        r = summary["rounds"][key]
        p = r["seeds"][0]["primary"]
        ci = p["paired_cluster_intervals"]
        lines += [
            f"{label}: primary recall {p['recall']:.2%}, FPR {p['fpr']:.2%}; "
            f"strict five-seed target **{r['strict_absolute_recall_target_all_seeds']}**, "
            f"matched secondary target **{r['secondary_matched_target_all_seeds']}**. "
            f"Primary paired 95% conditional interval for recall change "
            f"[{100 * ci['recall_delta']['lower']:.2f}, "
            f"{100 * ci['recall_delta']['upper']:.2f}] percentage points, "
            f"FPR change [{100 * ci['fpr_delta']['lower']:.2f}, "
            f"{100 * ci['fpr_delta']['upper']:.2f}] percentage points.",
            f"Absolute primary FPR interval [{ci['fpr']['lower']:.2%}, "
            f"{ci['fpr']['upper']:.2%}]. Across five matched seeds, mean ± sample SD: "
            f"recall {r['primary_split_variation']['recall']['mean']:.2%} ± "
            f"{100 * r['primary_split_variation']['recall']['sample_std']:.2f} pp; "
            f"FPR {r['primary_split_variation']['fpr']['mean']:.2%} ± "
            f"{100 * r['primary_split_variation']['fpr']['sample_std']:.2f} pp.",
            "",
        ]
    lines += [
        "![Matched development outcomes](calcium_v4_results.png)",
        "",
        "Round 1 preregistered 45 combinations of three reference-defined feature families "
        "and logistic/RBF regularization grids. Its existing fit/calibrate/tune and outer "
        "partitions exactly match v3. Calcium blue-line agreement uses Ca I/II lines "
        "393.477, 396.959, 422.792 and 445.603 nm with local continuum/noise features. "
        "The global primary selector maximizes inner recall under FPR budget .025, "
        "then specificity, Brier and complexity. Budget .05 and family selectors are "
        "fully reported as alternatives; no best outer result selects the artifact.",
        "",
        "Round 2 was explicitly designed after round 1 outcomes. It keeps the same 45 "
        "candidates and five outer partitions, and uses four inner group folds. Each "
        "inner-heldout probability averages four independently fit/calibrated models "
        "excluding the heldout groups. Outer prediction also averages four members. "
        "Calibration takes 25% of available groups with support checks. Inner members "
        "still train on fewer groups than outer members; averaging alignment does not "
        "remove training-size effects or guarantee population FPR.",
        "",
        "Round 3 was declared after round 2 still produced three false positives on seed "
        "20261020. It changes only the inner empirical FPR budget to zero and reruns the "
        "same candidate/threshold rule on pinned saved inner OOF probabilities. No models "
        "or calibrators are retrained; no outer labels enter selection. All previous attempts "
        "remain disclosed. This last planned round meets the matched five-seed point "
        "criterion, while the original stricter absolute-recall criterion fails on seed23. "
        "The search stops here. These adaptive outcomes cannot guarantee FPR ≤5% on new data.",
        "",
        "Grouping is transitive over physical material, family, acquisition session and "
        "input checksum. Unknown, missing, zero and intermediate assays never become "
        "negative labels. Calibration and threshold fitting use only known internal labels. "
        "All candidate scores, partitions, calibration states and outer metrics are retained. "
        "The JSON summary includes all fixed candidate attempts and selector summaries.",
        "",
        "The original round 1 lock omitted explicit OOF/profile byte pins. An immutable "
        "amended protocol adds them, verifies ordered IDs, groups, labels and feature plans "
        "against the original v3 source, and checks the profile hash in the original completed "
        "result. Its full repeated experiment produced exactly the same per-seed result "
        "objects. The original run remains available with that disclosed integrity limitation. "
        "Known prior development OOF/profiles (and round2 cached features) are read before "
        "the new candidate-scoring lock. Locked-before-scoring does not mean all numerical "
        "inputs were unread: no new final intensities or final predictions were consumed.",
        "",
        "Paired intervals resample connected clusters from fixed predictions (2000 draws), "
        "without retraining. They are conditional, not independent campaign uncertainty. "
        "Five seeds reuse the same samples. No error-free bootstrap can establish a zero "
        "population error rate. All numeric research artifacts preserve original v3 "
        "Na/Mg/Fe and use unconditional Ca. None is automatically promoted. The four-member "
        "mean combines Platt-calibrated scores; it has no independent final recalibration.",
        "",
        "Reproduction requires the pinned historical v3 runs, source development raw files "
        "and the preceding round2 cache. Reports/raw inputs are ignored local artifacts, "
        "so a fresh checkout alone does not contain all research inputs. Do not silently "
        "replace pinned inputs with newly generated runs. BLAS threads must remain 1:",
        "",
        "```sh",
        "export OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1",
        "export MKL_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1",
        "scripts/uv.sh run --frozen --all-packages --no-sync python scripts/study_calcium_v4.py",
        (
            "scripts/uv.sh run --frozen --all-packages --no-sync python "
            "scripts/study_calcium_crossfit_v4.py"
        ),
        (
            "scripts/uv.sh run --frozen --all-packages --no-sync python "
            "scripts/study_calcium_operating_v4.py"
        ),
        "```",
        "",
        "Pinned declarations: `data/reference/calcium_v4_round1_protocol.json`, "
        "`data/reference/calcium_v4_round1_verified_protocol.json`, "
        "`data/reference/calcium_v4_round2_protocol.json`, "
        "`data/reference/calcium_v4_round3_protocol.json`. "
        "Numeric results: `docs/calcium_v4_summary.json`.",
        "",
    ]
    (root / "docs/calcium_v4.md").write_text("\n".join(lines))
    print(out)


if __name__ == "__main__":
    main()
