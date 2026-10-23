"""Compact multi-scale line evidence for synthetic spectrum classification."""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler

from spectralab_ml.data.reference import ReferenceCatalog
from spectralab_ml.models.validation import finite_matrix, training_arrays


@dataclass
class LineLogisticOVR:
    """Train-only scaled logistic regression over reference-defined line windows.

    Scores are decision margins, not calibrated probabilities. Reference positions
    are selected without looking at observations or labels. All train-derived
    scaling and classifier parameters are serialized with the estimator.
    """

    grid_nm: np.ndarray
    positions_nm: tuple[float, ...]
    seed: int = 42
    C: float = 1.0
    half_widths_nm: tuple[float, ...] = (0.2, 0.6, 1.2)
    side_inner_nm: float = 1.6
    side_outer_nm: float = 2.5
    feature_groups: tuple[tuple[int, ...], ...] = ()
    score_kind: str = "line_logistic_decision_function"
    calibrated: bool = False
    _windows: list = field(default_factory=list, init=False, repr=False)

    def __post_init__(self) -> None:
        self.grid_nm = np.asarray(self.grid_nm, dtype=float).copy()
        if (
            self.grid_nm.ndim != 1
            or not 2 <= len(self.grid_nm) <= 100_000
            or not np.isfinite(self.grid_nm).all()
            or not np.all(np.diff(self.grid_nm) > 0)
        ):
            raise ValueError("Line classifier needs a finite increasing grid")
        if (
            not self.positions_nm
            or len(self.positions_nm) > 256
            or not np.isfinite(self.positions_nm).all()
            or not self.half_widths_nm
            or not np.isfinite(self.half_widths_nm).all()
            or not 0
            < min(self.half_widths_nm)
            <= max(self.half_widths_nm)
            < self.side_inner_nm
            < self.side_outer_nm
            or not np.isfinite([self.side_inner_nm, self.side_outer_nm, self.C]).all()
            or self.C <= 0
        ):
            raise ValueError("Invalid line positions, window geometry or regularisation")
        for position in self.positions_nm:
            distance = np.abs(self.grid_nm - position)
            side = np.flatnonzero(
                (distance >= self.side_inner_nm) & (distance <= self.side_outer_nm)
            )
            if len(side) < 2:
                raise ValueError("Line window needs at least two continuum pixels")
            peaks = [np.flatnonzero(distance <= width) for width in self.half_widths_nm]
            if any(len(peak) == 0 for peak in peaks):
                raise ValueError("Line position is outside the grid or narrower than the grid step")
            self._windows.append((peaks, side))

    @classmethod
    def from_catalog(
        cls,
        catalog: ReferenceCatalog,
        grid_nm: np.ndarray,
        class_order: tuple[str, ...],
        *,
        seed: int = 42,
        ion_stage: int = 1,
        element_local: bool = False,
    ) -> LineLogisticOVR:
        selected = []
        per_element = []
        for element in class_order:
            lines = sorted(
                (
                    line
                    for line in catalog.lines
                    if line.element == element
                    and line.ion_stage == ion_stage
                    and grid_nm[0] + 2.5 <= line.wavelength_nm <= grid_nm[-1] - 2.5
                ),
                key=lambda line: (-(line.relative_intensity or 0), line.wavelength_nm),
            )
            positions = []
            for line in lines:
                if all(abs(line.wavelength_nm - value) >= 0.4 for value in positions):
                    positions.append(line.wavelength_nm)
                if len(positions) == 6:
                    break
            if not positions:
                raise ValueError(f"No usable in-range reference lines for {element}")
            selected.extend(positions)
            per_element.append(positions)
        unique = tuple(dict.fromkeys(selected))
        groups = (
            tuple(
                tuple(
                    unique.index(position) * 6 + offset
                    for position in positions
                    for offset in range(6)
                )
                for positions in per_element
            )
            if element_local
            else ()
        )
        return cls(grid_nm, unique, seed=seed, feature_groups=groups)

    def features(self, X: np.ndarray) -> np.ndarray:
        X = finite_matrix(X, "Spectra")
        if X.shape[1] != len(self.grid_nm):
            raise ValueError("Spectra do not match the line classifier wavelength grid")
        if np.max(np.abs(X)) > 1e6:
            raise ValueError("Line classifier expects preprocessed normalised spectra")
        columns = []
        for peaks, side in self._windows:
            background = np.median(X[:, side], axis=1)
            for peak in peaks:
                columns.append(np.quantile(X[:, peak], 0.9, axis=1) - background)
                columns.append(np.mean(X[:, peak], axis=1) - background)
        return np.arcsinh(20 * np.column_stack(columns))

    def fit(self, X: np.ndarray, y: np.ndarray) -> LineLogisticOVR:
        X, y = training_arrays(X, y)
        features = self.features(X)
        if self.feature_groups and (
            len(self.feature_groups) != y.shape[1]
            or any(
                not group
                or any(type(i) is not int or not 0 <= i < features.shape[1] for i in group)
                for group in self.feature_groups
            )
        ):
            raise ValueError("Element-local feature groups do not match training classes/features")
        self.scaler = StandardScaler().fit(features)
        # Avoid magnifying a nearly constant training feature into huge OOD scores.
        self.scaler.scale_ = np.maximum(self.scaler.scale_, 0.05)
        Z = self.scaler.transform(features)
        self.constants = np.mean(y, axis=0)
        self.models = [
            LogisticRegression(
                C=self.C, max_iter=1000, class_weight="balanced", random_state=self.seed
            ).fit(Z[:, self.feature_groups[column]] if self.feature_groups else Z, y[:, column])
            if 0 < self.constants[column] < 1
            else None
            for column in range(y.shape[1])
        ]
        return self

    def scores(self, X: np.ndarray) -> np.ndarray:
        if not hasattr(self, "models"):
            raise ValueError("Line classifier has not been fitted")
        Z = self.scaler.transform(self.features(X))
        return np.column_stack(
            [
                model.decision_function(
                    Z[:, self.feature_groups[column]] if self.feature_groups else Z
                )
                if model is not None
                else np.full(len(Z), 20.0 if constant == 1 else -20.0)
                for column, (model, constant) in enumerate(
                    zip(self.models, self.constants, strict=True)
                )
            ]
        )

    def configuration(self) -> dict:
        result = {
            "feature_version": "multiscale-line-windows-v1",
            "positions_nm": list(self.positions_nm),
            "half_widths_nm": list(self.half_widths_nm),
            "side_inner_nm": self.side_inner_nm,
            "side_outer_nm": self.side_outer_nm,
            "transform": "arcsinh(20 * background_subtracted_height_and_mean)",
            "scaler": "StandardScaler fitted on train only; scale floor 0.05",
            "C": self.C,
            "seed": self.seed,
        }
        if self.feature_groups:
            result["feature_groups"] = [list(group) for group in self.feature_groups]
            result["classifier_scope"] = "each element uses only its own reference windows"
        return result
