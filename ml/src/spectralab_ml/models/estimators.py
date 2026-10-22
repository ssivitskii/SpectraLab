from __future__ import annotations

from dataclasses import dataclass
from numbers import Real
from typing import Protocol

import numpy as np
from scipy.optimize import nnls
from sklearn.ensemble import RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.multiclass import OneVsRestClassifier

from spectralab_ml.models.validation import binary_labels, finite_matrix, training_arrays


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
        X, y = training_arrays(X, y)
        self.estimator.fit(X, y[:, 0] if y.shape[1] == 1 else y)
        return self

    def scores(self, X: np.ndarray) -> np.ndarray:
        probabilities = self.estimator.predict_proba(X)
        classes_by_output = self.estimator.classes_
        if isinstance(probabilities, np.ndarray):
            probabilities = [probabilities]
            classes_by_output = [classes_by_output]
        columns: list[np.ndarray] = []
        for class_values, values in zip(classes_by_output, probabilities, strict=True):
            classes = np.asarray(class_values)
            positive = np.flatnonzero(classes == 1)
            columns.append(values[:, positive[0]] if positive.size else np.zeros(len(X)))
        return np.column_stack(columns)


def select_thresholds(
    scores: np.ndarray, y_true: np.ndarray, *, default: float
) -> tuple[np.ndarray, dict[str, object]]:
    scores, y_true = finite_matrix(scores, "Scores"), binary_labels(y_true)
    if scores.shape != y_true.shape:
        raise ValueError("Finite scores and matching two-dimensional labels are required")
    if isinstance(default, bool) or not isinstance(default, Real):
        raise ValueError("Default threshold must be finite")
    try:
        default = float(default)
    except OverflowError as exc:
        raise ValueError("Default threshold exceeds the finite numeric range") from exc
    if not np.isfinite(default):
        raise ValueError("Default threshold must be finite")
    thresholds = np.full(scores.shape[1], default, dtype=float)
    chosen_f1: list[float | None] = []
    for column in range(scores.shape[1]):
        truth = y_true[:, column]
        if len(np.unique(truth)) < 2:
            chosen_f1.append(None)
            continue
        # Every distinct score is one realizable >= decision boundary. Sort once,
        # including all tied rows together, rather than using another class's scale.
        order = np.argsort(scores[:, column], kind="stable")[::-1]
        ordered_scores = scores[order, column]
        ends = np.r_[np.flatnonzero(ordered_scores[:-1] != ordered_scores[1:]), len(truth) - 1]
        tp = np.cumsum(truth[order], dtype=np.int64)[ends]
        predicted_count = ends + 1
        f1 = 2 * tp / (predicted_count + int(truth.sum()))
        # Match the previous tie policy: prefer the lowest threshold with maximum F1.
        best = np.flatnonzero(f1 == np.max(f1))[-1]
        chosen_f1.append(float(f1[best]))
        included = float(ordered_scores[ends[best]])
        threshold = included
        if ends[best] + 1 < len(ordered_scores):
            excluded = float(ordered_scores[ends[best] + 1])
            midpoint = excluded + (included - excluded) / 2
            if excluded < midpoint <= included:
                threshold = midpoint
        thresholds[column] = threshold
    return thresholds, {
        "split": "validation",
        "schema_version": 3,
        "criterion": (
            "per-class F1 over every distinct score boundary; lowest boundary on ties; "
            "threshold at the midpoint between the lowest accepted and highest rejected score"
        ),
        "validation_f1": chosen_f1,
        "fallback_columns": [i for i, value in enumerate(chosen_f1) if value is None],
        "fallback_reason": "Single-class validation: fixed default retained, not tuned",
    }
