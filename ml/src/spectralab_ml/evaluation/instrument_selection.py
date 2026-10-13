"""Development-only grouping and operational decision thresholds.

Thresholds maximize physical-sample recall under a development point FPR budget.
This is a selection rule, not a confidence bound or a promised population FPR.
"""

from __future__ import annotations

from collections import Counter

import numpy as np


def grouped_development_split(records: list[dict], classes: list[str], seed: int) -> dict:
    """First seeded 25% cluster split supporting both labels; use metadata only."""
    parent = list(range(len(records)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    seen = {}
    for i, record in enumerate(records):
        for field in ("physical_sample_id", "family_id", "session_id", "input_sha256"):
            if field not in record:
                continue
            key = field, record[field]
            if key in seen:
                parent[find(i)] = find(seen[key])
            else:
                seen[key] = i
    groups = np.asarray([find(i) for i in range(len(records))])
    unique = np.unique(groups)
    if len(unique) < 4:
        raise ValueError("At least four development clusters required")
    labels = np.asarray(
        [
            [
                {"positive": 1, "negative": 0, "unknown": -1}[r["labels"][c]["state"]]
                for c in classes
            ]
            for r in records
        ]
    )
    rng = np.random.default_rng(seed)
    for attempt in range(10_000):
        tune_groups = rng.permutation(unique)[: max(2, round(len(unique) * 0.25))]
        tune = np.isin(groups, tune_groups)
        # Require independent clusters as well as records for each known class.
        if all(
            len(np.unique(groups[part & (labels[:, column] == state)])) >= 2
            for part in (tune, ~tune)
            for column in range(len(classes))
            for state in (0, 1)
        ):
            return {
                "train_ids": [r["measurement_id"] for i, r in enumerate(records) if not tune[i]],
                "tune_ids": [r["measurement_id"] for i, r in enumerate(records) if tune[i]],
                "seed": seed,
                "metadata_partition_attempt": attempt,
                "training_clusters": len(np.unique(groups[~tune])),
                "tuning_clusters": len(np.unique(groups[tune])),
                "method": (
                    "first seeded 25%-cluster split with >=2 positive/negative clusters "
                    "per class in both partitions"
                ),
            }
    raise ValueError("Cannot construct supported train/tune split without leakage")


def select_operational_thresholds(
    scores: np.ndarray,
    labels: np.ndarray,
    sample_ids: list[str],
    *,
    maximum_fpr: float = 0.05,
) -> tuple[np.ndarray, dict]:
    scores, labels = np.asarray(scores), np.asarray(labels)
    if (
        scores.ndim != 2
        or scores.shape != labels.shape
        or not len(scores)
        or len(sample_ids) != len(scores)
        or not np.isfinite(scores).all()
        or not np.isin(labels, [-1, 0, 1]).all()
        or not 0 <= maximum_fpr <= 1
    ):
        raise ValueError("Finite aligned scores, three-state labels and physical IDs required")
    for sample in set(sample_ids):
        rows = np.asarray(sample_ids) == sample
        if np.any(labels[rows] != labels[rows][0]):
            raise ValueError("Physical replicates have conflicting labels")
    counts = Counter(sample_ids)
    weights = np.asarray([1 / counts[s] for s in sample_ids])
    thresholds, per_class = [], []
    for column in range(scores.shape[1]):
        values, truth = scores[:, column], labels[:, column]
        positive, negative = truth == 1, truth == 0
        if not positive.any() or not negative.any():
            raise ValueError("Threshold tuning requires positive and negative physical support")
        # Unknown rows must not influence even the candidate decision thresholds.
        known_scores = values[truth >= 0]
        candidates = np.r_[np.unique(known_scores), np.nextafter(known_scores.max(), np.inf)]
        best = None
        for threshold in candidates:
            detected = values >= threshold
            recall = float(weights[positive & detected].sum() / weights[positive].sum())
            fpr = float(weights[negative & detected].sum() / weights[negative].sum())
            if fpr <= maximum_fpr + 1e-12:
                key = recall, -fpr, float(threshold)
                if best is None or key > best[0]:
                    best = key, float(threshold), recall, fpr
        if best is None:
            raise ValueError("No finite reject-all threshold available")
        _, threshold, recall, fpr = best
        thresholds.append(threshold)
        per_class.append(
            {
                "threshold": threshold,
                "recall": recall,
                "false_positive_rate": fpr,
                "balanced_accuracy": (recall + 1 - fpr) / 2,
                "positive_physical_support": len(set(np.asarray(sample_ids)[positive])),
                "negative_physical_support": len(set(np.asarray(sample_ids)[negative])),
            }
        )
    return np.asarray(thresholds), {
        "method": (
            "maximum physical recall under tuning point FPR budget; "
            "ties lower FPR then higher threshold"
        ),
        "maximum_tuning_point_fpr": maximum_fpr,
        "macro_balanced_accuracy": float(np.mean([r["balanced_accuracy"] for r in per_class])),
        "per_class": per_class,
        "guarantees_population_fpr": False,
    }
