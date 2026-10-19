"""Shared numeric contracts for generic multi-label training and scoring."""

from __future__ import annotations

import numpy as np


def finite_matrix(value: np.ndarray, name: str) -> np.ndarray:
    array = np.asarray(value)
    if (
        array.ndim != 2
        or 0 in array.shape
        or array.dtype.kind not in "biuf"
        or not np.isfinite(array).all()
    ):
        raise ValueError(f"{name} must be a nonempty finite numeric matrix")
    return array


def binary_labels(value: np.ndarray) -> np.ndarray:
    labels = finite_matrix(value, "Labels")
    if not np.isin(labels, (0, 1)).all():
        raise ValueError("Labels must be binary (0 or 1); unknown labels need a separate protocol")
    return labels


def training_arrays(X: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    features, labels = finite_matrix(X, "Features"), binary_labels(y)
    if len(features) != len(labels):
        raise ValueError("Features and labels must have matching row counts")
    return features, labels
