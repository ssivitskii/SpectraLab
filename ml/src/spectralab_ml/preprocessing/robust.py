from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np
from scipy.linalg import solveh_banded


def als_baseline(
    values: np.ndarray, *, smoothness: float = 1e7, asymmetry: float = 0.01, iterations: int = 10
) -> np.ndarray:
    y = np.asarray(values, dtype=float)
    if y.ndim != 1 or len(y) < 5 or not np.all(np.isfinite(y)):
        raise ValueError("ALS needs a finite one-dimensional signal of at least 5 points")
    if not smoothness > 0 or not 0 < asymmetry < 0.5 or iterations < 1:
        raise ValueError("Invalid ALS parameters")
    n = len(y)
    main = np.full(n, 6.0)
    main[[0, -1]] = 1.0
    main[[1, -2]] = 5.0
    first = np.full(n - 1, -4.0)
    first[[0, -1]] = -2.0
    second = np.ones(n - 2)
    penalty = np.zeros((3, n))
    penalty[0, 2:] = smoothness * second
    penalty[1, 1:] = smoothness * first
    penalty[2, :] = smoothness * main
    weights = np.ones(n)
    baseline = y
    for _ in range(iterations):
        system = penalty.copy()
        system[2] += weights
        baseline = solveh_banded(system, weights * y, check_finite=False)
        updated = np.where(y > baseline, asymmetry, 1 - asymmetry)
        if np.array_equal(updated, weights):
            break
        weights = updated
    return baseline


def band_slices(mask: np.ndarray) -> list[slice]:
    mask = np.asarray(mask, dtype=bool)
    edges = np.flatnonzero(np.diff(np.r_[0, mask.astype(int), 0]))
    return [
        slice(int(start), int(stop)) for start, stop in zip(edges[::2], edges[1::2], strict=True)
    ]


@dataclass(frozen=True)
class RobustBandTransform:
    smoothness: float = 1e7
    asymmetry: float = 0.01
    iterations: int = 10
    normalization: str = "band_l1"

    def __post_init__(self) -> None:
        if self.normalization not in {"band_l1", "band_max"}:
            raise ValueError("normalization must be band_l1 or band_max")

    def transform(self, features: np.ndarray, mask: np.ndarray) -> np.ndarray:
        X = np.asarray(features, dtype=float)
        if X.ndim != 2 or X.shape[1] != len(mask) or not np.all(np.isfinite(X)):
            raise ValueError("Finite two-dimensional features aligned with the mask required")
        slices = band_slices(mask)
        if not slices:
            raise ValueError("At least one observed band is required")
        output = np.zeros((len(X), int(np.sum(mask))))
        for row, spectrum in enumerate(X):
            parts = []
            for band in slices:
                values = spectrum[band]
                corrected = values - als_baseline(
                    values,
                    smoothness=self.smoothness,
                    asymmetry=self.asymmetry,
                    iterations=self.iterations,
                )
                if self.normalization == "band_l1":
                    scale = float(np.sum(np.clip(corrected, 0, None)))
                else:
                    scale = float(np.max(corrected))
                parts.append(corrected / scale if scale > 0 else np.zeros_like(corrected))
            output[row] = np.concatenate(parts)
        return output

    def to_dict(self) -> dict:
        return asdict(self)
