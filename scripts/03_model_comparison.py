"""Plot each saved experiment separately, preserving protocols and seeds."""

import json

import matplotlib.pyplot as plt
import pandas as pd
from spectralab_ml.config import repository_root

root = repository_root()
found = 0
for path in sorted((root / "reports" / "experiments").glob("*/result.json")):
    result = json.loads(path.read_text(encoding="utf-8"))
    if result["status"] != "completed":
        continue
    found += 1
    out = root / "reports" / "analytics" / "03_model_comparison" / result["run_id"]
    out.mkdir(parents=True, exist_ok=True)
    rows = []
    errors = []
    for run in result["runs"]:
        identity = {"model_id": run["model_id"], "factor": run["factor"], "value": run["value"]}
        rows.append(
            identity
            | {
                k: run["metrics"][k]
                for k in ("micro_f1", "macro_f1", "hamming_loss", "exact_match_accuracy")
            }
        )
        for element, metrics in run["metrics"]["per_element"].items():
            errors.append(identity | {"element": element, **metrics})
    pd.DataFrame(rows).to_csv(out / "comparison.csv", index=False)
    pd.DataFrame(errors).to_csv(out / "per_element.csv", index=False)
    fig, axes = plt.subplots(1, 2, figsize=(12, 4))
    model_ids = list(dict.fromkeys(row["model_id"] for row in rows))
    for model_id in model_ids:
        runs = [run for run in result["runs"] if run["model_id"] == model_id]
        labels = [str(run["value"]) if run["value"] is not None else "clean" for run in runs]
        axes[0].plot(
            range(len(runs)),
            [run["metrics"]["micro_f1"] for run in runs],
            marker="o",
            label=model_id,
        )
        axes[0].set_xticks(range(len(runs)), labels)
        # A clearly identified first condition, not an average over incompatible conditions.
        metrics = runs[0]["metrics"]["per_element"]
        axes[1].plot(
            list(metrics),
            [
                float("nan") if item["recall"] is None else 1 - item["recall"]
                for item in metrics.values()
            ],
            marker="o",
            label=model_id,
        )
    axes[0].set(xlabel=result["runs"][0]["factor"], ylabel="micro-F1", ylim=(0, 1.05))
    axes[1].set(
        ylabel="Miss rate (1 - recall)",
        title="First recorded condition; undefined values omitted",
        ylim=(0, 1.05),
    )
    axes[0].legend(fontsize=8)
    fig.suptitle(
        f"{result['run_id']} · {result['reference_source']} · seed {result['seed']}", fontsize=9
    )
    fig.tight_layout()
    fig.savefig(out / "comparison.png", dpi=150)
    plt.close(fig)
    print(out)
if not found:
    raise SystemExit("No completed experiments. Run make demo first.")
