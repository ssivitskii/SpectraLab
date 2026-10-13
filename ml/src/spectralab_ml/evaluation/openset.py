"""An abstention decision independent of per-element detection thresholds."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.optimize import nnls


@dataclass
class ResidualRejector:
    templates: np.ndarray
    threshold: float | None = None

    def novelty_scores(self, X: np.ndarray) -> np.ndarray:
        coefficients = np.stack([nnls(self.templates.T, row)[0] for row in X])
        residual = np.linalg.norm(X - coefficients @ self.templates, axis=1)
        norm = np.linalg.norm(X, axis=1)
        return np.divide(residual, norm, out=np.ones_like(residual), where=norm > 1e-12)

    def fit_known_development(self, X: np.ndarray, *, allowed_rejection: float = 0.05):
        if len(X) < 2 or not 0 < allowed_rejection < 1:
            raise ValueError("At least two development samples and a fraction in (0,1) required")
        self.threshold = float(
            np.quantile(self.novelty_scores(X), 1 - allowed_rejection, method="higher")
        )
        return self

    def abstain(self, X: np.ndarray) -> np.ndarray:
        if self.threshold is None:
            raise ValueError("Set rejection threshold using development only")
        return self.novelty_scores(X) > self.threshold


def rejection_metrics(rejected: np.ndarray, *, known: bool) -> dict:
    count = len(rejected)
    return {
        "count": count,
        "known_false_rejection_rate" if known else "unknown_false_acceptance_rate": float(
            np.mean(rejected if known else ~rejected)
        )
        if count
        else None,
    }
