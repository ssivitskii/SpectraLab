from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import numpy as np
from scipy.optimize import nnls
from sklearn.ensemble import RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.multiclass import OneVsRestClassifier


class MultiLabelEstimator(Protocol):
    score_kind: str
    calibrated: bool

    def fit(self, X: np.ndarray, y: np.ndarray) -> MultiLabelEstimator: ...

    def scores(self, X: np.ndarray) -> np.ndarray: ...


@dataclass
class BaselineNNLS:
    templates: np.ndarray
    score_kind: str = "nnls_coefficient_times_fit_quality"
    calibrated: bool = False

    def fit(self, X: np.ndarray, y: np.ndarray) -> BaselineNNLS:
        del X, y
        return self

    def scores(self, X: np.ndarray) -> np.ndarray:
        coefficients = np.stack([nnls(self.templates.T, row)[0] for row in X])
        reconstruction = coefficients @ self.templates
        input_norm = np.linalg.norm(X, axis=1)
        residual = np.linalg.norm(X - reconstruction, axis=1)
        evidence = np.clip(
            1 - np.divide(residual, input_norm, out=np.ones_like(residual), where=input_norm > 0),
            0,
            1,
        )
        # Absolute coefficients retain a rejection region. Multiplying by global fit quality
        # prevents a weak projection of an off-template signal from being promoted to 1.
        return coefficients * evidence[:, None]


@dataclass
class LogisticOVR:
    seed: int = 42
    score_kind: str = "logistic_decision_function"
    calibrated: bool = False

    def __post_init__(self) -> None:
        self.estimator = OneVsRestClassifier(
            LogisticRegression(max_iter=500, random_state=self.seed, class_weight="balanced")
        )

    def fit(self, X: np.ndarray, y: np.ndarray) -> LogisticOVR:
        self.estimator.fit(X, y)
        return self

    def scores(self, X: np.ndarray) -> np.ndarray:
        result = np.asarray(self.estimator.decision_function(X), dtype=float)
        return result.reshape(len(X), -1)


@dataclass
class RandomForestMultiLabel:
    seed: int = 42
    n_estimators: int = 80
    score_kind: str = "random_forest_uncalibrated_vote_fraction"
    calibrated: bool = False

    def __post_init__(self) -> None:
        self.estimator = RandomForestClassifier(
            n_estimators=self.n_estimators,
            random_state=self.seed,
            class_weight="balanced_subsample",
            n_jobs=1,
        )

    def fit(self, X: np.ndarray, y: np.ndarray) -> RandomForestMultiLabel:
        self.estimator.fit(X, y)
        return self

    def scores(self, X: np.ndarray) -> np.ndarray:
        probabilities = self.estimator.predict_proba(X)
        columns: list[np.ndarray] = []
        for class_values, values in zip(self.estimator.classes_, probabilities, strict=True):
            classes = np.asarray(class_values)
            positive = np.flatnonzero(classes == 1)
            columns.append(values[:, positive[0]] if positive.size else np.zeros(len(X)))
        return np.column_stack(columns)


def select_thresholds(
    scores: np.ndarray, y_true: np.ndarray, *, default: float
) -> tuple[np.ndarray, dict[str, object]]:
    if scores.ndim != 2 or scores.shape != y_true.shape or not np.all(np.isfinite(scores)):
        raise ValueError("Finite scores and matching two-dimensional labels are required")
    candidates = np.r_[
        np.linspace(float(np.min(scores)), float(np.max(scores)), 51),
        np.nextafter(np.max(scores), np.inf),
    ]
    thresholds = np.full(scores.shape[1], default, dtype=float)
    chosen_f1: list[float | None] = []
    for column in range(scores.shape[1]):
        truth = y_true[:, column]
        if len(np.unique(truth)) < 2:
            chosen_f1.append(None)
            continue
        best = (-1.0, default)
        for threshold in candidates:
            predicted = scores[:, column] >= threshold
            tp = int(np.sum(predicted & (truth == 1)))
            fp = int(np.sum(predicted & (truth == 0)))
            fn = int(np.sum(~predicted & (truth == 1)))
            f1 = 2 * tp / (2 * tp + fp + fn) if (2 * tp + fp + fn) else 0.0
            if f1 > best[0]:
                best = (f1, float(threshold))
        chosen_f1.append(best[0])
        thresholds[column] = best[1]
    return thresholds, {
        "split": "validation",
        "criterion": "per-class F1 over 51 evenly spaced score thresholds",
        "validation_f1": chosen_f1,
        "fallback_columns": [i for i, value in enumerate(chosen_f1) if value is None],
        "fallback_reason": "Single-class validation: fixed default retained, not tuned",
    }
