"""Development-only sigmoid calibration, with explicit single-class fallbacks."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
from scipy.special import expit
from sklearn.linear_model import LogisticRegression


def probability_view(estimator: Any, scores: np.ndarray) -> np.ndarray:
    if estimator.score_kind == "logistic_decision_function":
        return expit(scores)
    return np.clip(scores, 0, 1)


def reliability_metrics(y: np.ndarray, probabilities: np.ndarray, bins: int = 10) -> dict:
    if y.shape != probabilities.shape or not len(y):
        raise ValueError("Reliability requires matching nonempty labels and probabilities")
    if not np.isfinite(probabilities).all() or np.any((probabilities < 0) | (probabilities > 1)):
        raise ValueError("Reliability requires probabilities in [0, 1]")
    assignments = np.minimum((probabilities.ravel() * bins).astype(int), bins - 1)
    truth = y.ravel()
    values = probabilities.ravel()
    result = []
    ece = 0.0
    for index in range(bins):
        mask = assignments == index
        count = int(mask.sum())
        confidence = float(values[mask].mean()) if count else None
        frequency = float(truth[mask].mean()) if count else None
        if count:
            ece += count / len(values) * abs(confidence - frequency)
        result.append(
            {
                "lower": index / bins,
                "upper": (index + 1) / bins,
                "count": count,
                "mean_score": confidence,
                "positive_fraction": frequency,
            }
        )
    return {
        "brier": float(np.mean((probabilities - y) ** 2)),
        "ece": float(ece),
        "bin_count": bins,
        "sample_count": len(y),
        "bins": result,
    }


@dataclass
class CalibratedEstimator:
    estimator: Any
    score_kind: str = "sigmoid_calibrated_probability_synthetic_development"
    calibrated: bool = True
    calibrators: list[Any] = field(default_factory=list)
    calibration_metadata: dict = field(default_factory=dict)

    def fit(self, X: np.ndarray, y: np.ndarray) -> CalibratedEstimator:
        self.estimator.fit(X, y)
        return self

    def fit_calibration(self, X: np.ndarray, y: np.ndarray) -> CalibratedEstimator:
        scores = self.estimator.scores(X)
        if scores.shape != y.shape or not len(X):
            raise ValueError("Nonempty, aligned calibration split required")
        self.calibrators = []
        fallback = []
        for column in range(y.shape[1]):
            if len(np.unique(y[:, column])) < 2:
                self.calibrators.append(float((y[:, column].sum() + 1) / (len(y) + 2)))
                fallback.append(column)
            else:
                model = LogisticRegression(C=1.0, solver="lbfgs", random_state=0)
                model.fit(scores[:, [column]], y[:, column])
                self.calibrators.append(model)
        self.calibration_metadata = {
            "schema_version": 1,
            "method": "per-class sigmoid, C=1",
            "scope": "synthetic development split only; real spectra unvalidated",
            "constant_fallback_columns": fallback,
            "fallback_method": "Laplace-smoothed development prevalence",
            "sample_count": len(X),
        }
        return self

    def scores(self, X: np.ndarray) -> np.ndarray:
        raw = self.estimator.scores(X)
        if len(self.calibrators) != raw.shape[1]:
            raise ValueError("Calibrator has not been fitted for every class")
        return np.column_stack(
            [
                np.full(len(X), calibration)
                if isinstance(calibration, float)
                else calibration.predict_proba(raw[:, [index]])[:, 1]
                for index, calibration in enumerate(self.calibrators)
            ]
        )
