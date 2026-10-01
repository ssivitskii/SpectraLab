from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

import numpy as np
from sklearn.metrics import accuracy_score, f1_score, hamming_loss, precision_score, recall_score


def evaluate_multilabel(
    y_true: np.ndarray,
    scores: np.ndarray,
    thresholds: np.ndarray,
    class_order: tuple[str, ...],
    *,
    timed_predict: Callable[[], np.ndarray] | None = None,
) -> dict[str, Any]:
    predicted = (scores >= thresholds).astype(int)
    result: dict[str, Any] = {
        "micro_f1": float(f1_score(y_true, predicted, average="micro", zero_division=0)),
        "macro_f1": float(f1_score(y_true, predicted, average="macro", zero_division=0)),
        "exact_match_accuracy": float(accuracy_score(y_true, predicted)),
        "hamming_loss": float(hamming_loss(y_true, predicted)),
        "per_element": {},
        "undefined_metric_policy": (
            "Undefined per-element precision/recall are null; F1 uses zero_division=0, "
            "including absent classes in macro average."
        ),
    }
    for index, element in enumerate(class_order):
        support = int(np.sum(y_true[:, index]))
        result["per_element"][element] = {
            "support": support,
            "precision": (
                float(precision_score(y_true[:, index], predicted[:, index], zero_division=0))
                if np.any(predicted[:, index])
                else None
            ),
            "recall": (
                float(recall_score(y_true[:, index], predicted[:, index], zero_division=0))
                if support
                else None
            ),
        }
    for name, mask in {
        "single": np.sum(y_true, axis=1) == 1,
        "mixture": np.sum(y_true, axis=1) > 1,
    }.items():
        result[name] = (
            {
                "count": int(np.sum(mask)),
                "micro_f1": float(
                    f1_score(y_true[mask], predicted[mask], average="micro", zero_division=0)
                ),
                "exact_match_accuracy": float(accuracy_score(y_true[mask], predicted[mask])),
            }
            if np.any(mask)
            else {"count": 0, "micro_f1": None, "exact_match_accuracy": None}
        )
    if timed_predict:
        started = time.perf_counter()
        timed_predict()
        result["inference_ms_per_spectrum"] = (time.perf_counter() - started) * 1000 / len(y_true)
        result["timing_method"] = "single warm-process batch measured with time.perf_counter"
    return result
