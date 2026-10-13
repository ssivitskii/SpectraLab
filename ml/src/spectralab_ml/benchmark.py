"""Repeatable warm-process inference timing; wall-clock observations are retained."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

import numpy as np


def measure_inference(
    predict: Callable[[], Any], sample_count: int, *, warmups: int = 2, repeats: int = 7
) -> dict[str, Any]:
    if sample_count < 1 or warmups < 1 or repeats < 2:
        raise ValueError("Timing requires samples, at least one warmup and two repeats")
    for _ in range(warmups):
        predict()
    observations = []
    for _ in range(repeats):
        start = time.perf_counter_ns()
        predict()
        observations.append((time.perf_counter_ns() - start) / 1e6 / sample_count)
    median = float(np.median(observations))
    return {
        "inference_ms_per_spectrum": median,
        "timing_method": "warm-process batch; perf_counter_ns; median per spectrum",
        "timing": {
            "warmups": warmups,
            "repeats": repeats,
            "batch_size": sample_count,
            "unit": "ms_per_spectrum",
            "median": median,
            "p95": float(np.percentile(observations, 95)),
            "observations": observations,
        },
    }
