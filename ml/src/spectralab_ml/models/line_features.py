from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from scipy.signal import find_peaks
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler

from spectralab_ml.models.validation import training_arrays
from spectralab_ml.preprocessing.robust import band_slices


def select_line_positions(
    templates: np.ndarray,
    grid_nm: np.ndarray,
    labels: tuple[str, ...],
    *,
    per_element: int = 10,
    min_separation_nm: float = 0.5,
    min_relative_height: float = 0.02,
) -> list[tuple[str, float]]:
    if templates.shape != (len(labels), len(grid_nm)):
        raise ValueError("Templates must be (elements, grid)")
    step = float(np.median(np.diff(grid_nm)))
    distance = max(1, round(min_separation_nm / step))
    selected: list[tuple[str, float]] = []
    for label, template in zip(labels, templates, strict=True):
        peaks, properties = find_peaks(
            template, height=min_relative_height * float(np.max(template)), distance=distance
        )
        order = np.argsort(properties["peak_heights"])[::-1][:per_element]
        selected.extend((label, float(grid_nm[index])) for index in sorted(peaks[order]))
    return selected


@dataclass
class LineWindowFeatures:
    positions_nm: tuple[float, ...]
    grid_nm: np.ndarray
    mask: np.ndarray
    peak_half_width_nm: float = 0.25
    side_inner_nm: float = 0.6
    side_outer_nm: float = 1.5
    scale: float = 1e3
    _windows: list[tuple[np.ndarray, np.ndarray]] = field(default_factory=list, repr=False)

    def __post_init__(self) -> None:
        if not 0 < self.peak_half_width_nm < self.side_inner_nm < self.side_outer_nm:
            raise ValueError("Line windows must satisfy 0 < peak < inner < outer")
        observed = np.flatnonzero(self.mask)
        wavelengths = self.grid_nm[observed]
        band_id = np.zeros(len(observed), dtype=int)
        offset = 0
        for number, band in enumerate(band_slices(self.mask)):
            width = band.stop - band.start
            band_id[offset : offset + width] = number
            offset += width
        self._windows = []
        for position in self.positions_nm:
            distance = np.abs(wavelengths - position)
            nearest = int(np.argmin(distance))
            if distance[nearest] > self.peak_half_width_nm:
                raise ValueError(f"Line {position:.3f} nm is outside the observed bands")
            same_band = band_id == band_id[nearest]
            peak = np.flatnonzero(same_band & (distance <= self.peak_half_width_nm))
            side = np.flatnonzero(
                same_band & (distance >= self.side_inner_nm) & (distance <= self.side_outer_nm)
            )
            if len(side) < 2:
                side = np.flatnonzero(same_band & (distance > self.peak_half_width_nm))[:4]
            self._windows.append((peak, side))

    def transform(self, masked_features: np.ndarray) -> np.ndarray:
        X = np.asarray(masked_features, dtype=float)
        if X.ndim != 2 or X.shape[1] != int(np.sum(self.mask)):
            raise ValueError("Features must contain exactly the observed pixels")
        columns = [
            X[:, peak].max(axis=1) - np.median(X[:, side], axis=1) for peak, side in self._windows
        ]
        return np.arcsinh(self.scale * np.column_stack(columns))


@dataclass
class ScaledLogisticOVR:
    seed: int = 42
    C: float = 1.0
    score_kind: str = "logistic_decision_function"
    calibrated: bool = False

    def fit(self, X: np.ndarray, y: np.ndarray) -> ScaledLogisticOVR:
        X, y = training_arrays(X, y)
        self.scaler = StandardScaler().fit(X)
        Z = self.scaler.transform(X)
        self.models = [
            LogisticRegression(
                C=self.C, max_iter=2000, class_weight="balanced", random_state=self.seed
            ).fit(Z, y[:, k])
            if len(np.unique(y[:, k])) == 2
            else None
            for k in range(y.shape[1])
        ]
        return self

    def scores(self, X: np.ndarray) -> np.ndarray:
        Z = self.scaler.transform(X)
        return np.column_stack(
            [
                model.decision_function(Z) if model is not None else np.full(len(Z), -10.0)
                for model in self.models
            ]
        )


@dataclass
class GradientBoostingOVR:
    seed: int = 42
    max_iter: int = 200
    learning_rate: float = 0.1
    score_kind: str = "gradient_boosting_uncalibrated_probability"
    calibrated: bool = False

    def fit(self, X: np.ndarray, y: np.ndarray) -> GradientBoostingOVR:
        X, y = training_arrays(X, y)
        self.models = [
            HistGradientBoostingClassifier(
                max_iter=self.max_iter,
                learning_rate=self.learning_rate,
                class_weight="balanced",
                early_stopping=False,
                random_state=self.seed,
            ).fit(X, y[:, k])
            if len(np.unique(y[:, k])) == 2
            else None
            for k in range(y.shape[1])
        ]
        return self

    def scores(self, X: np.ndarray) -> np.ndarray:
        return np.column_stack(
            [
                model.predict_proba(X)[:, 1] if model is not None else np.zeros(len(X))
                for model in self.models
            ]
        )
