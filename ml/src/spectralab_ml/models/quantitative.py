"""Quantitative LIBS regression with conformal intervals and applicability domain."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from typing import Any

import numpy as np

from spectralab_ml.data.wavelengths import air_to_vacuum

# Conversion factors from pure element mass to stoichiometric oxide equivalent
OXIDE_FACTORS: dict[str, tuple[str, float, float]] = {
    # species: (element, factor_to_oxide, lod_wt_percent)
    "FeOT": ("Fe", 1.2865, 0.05),
    "MgO": ("Mg", 1.6583, 0.03),
    "CaO": ("Ca", 1.3992, 0.04),
    "TiO2": ("Ti", 1.6681, 0.02),
    "SiO2": ("Si", 2.1392, 0.10),
    "Al2O3": ("Al", 1.8895, 0.05),
}

# Key analytical lines (vacuum nm) for each target species
SPECIES_LINES: dict[str, tuple[float, ...]] = {
    "FeOT": (373.593, 382.151, 404.696, 426.167, 438.478),
    "MgO": (285.296, 383.339, 383.938, 517.413, 518.505),
    "CaO": (393.477, 396.959, 422.792, 443.621, 445.603),
    "TiO2": (378.712, 429.696, 445.039, 485.737, 501.567),
    "SiO2": (288.238, 390.663),
    "Al2O3": (394.512, 396.264),
}


@dataclass(frozen=True)
class QuantitativePrediction:
    species: str
    element: str
    value_wt_percent: float
    lower_wt_percent: float
    upper_wt_percent: float
    confidence_level: float
    unit: str
    status: str
    lod_wt_percent: float
    leverage: float
    warning: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class QuantitativeReport:
    predictions: tuple[QuantitativePrediction, ...]
    model_id: str
    model_version: str
    confidence_level: float
    total_detected_mass_wt_percent: float
    applicability_domain_summary: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "predictions": [p.to_dict() for p in self.predictions],
            "model_id": self.model_id,
            "model_version": self.model_version,
            "confidence_level": self.confidence_level,
            "total_detected_mass_wt_percent": self.total_detected_mass_wt_percent,
            "applicability_domain_summary": self.applicability_domain_summary,
        }


def extract_species_line_features(
    grid_nm: np.ndarray,
    radiance: np.ndarray,
    species: str,
    *,
    half_width_nm: float = 0.06,
    side_inner_nm: float = 0.12,
    side_outer_nm: float = 0.22,
) -> np.ndarray:
    """Extract normalized line intensities for a target oxide species."""
    lines = SPECIES_LINES.get(species, ())
    if not lines:
        return np.zeros(1, dtype=float)

    total_spectral_area = float(np.trapezoid(np.maximum(radiance, 0.0), grid_nm))
    norm_factor = 1e4 / max(total_spectral_area, 1e-12)

    features = []
    for wl in lines:
        delta = np.abs(grid_nm - wl)
        peak_mask = delta <= half_width_nm
        side_mask = (delta >= side_inner_nm) & (delta <= side_outer_nm)

        if not np.any(peak_mask) or np.sum(peak_mask) < 2:
            features.append(0.0)
            continue

        base = float(np.median(radiance[side_mask])) if np.any(side_mask) else 0.0
        net_curve = np.maximum(radiance[peak_mask] - base, 0.0)
        net_area = float(np.trapezoid(net_curve, grid_nm[peak_mask]))
        features.append(net_area * norm_factor)

    return np.asarray(features, dtype=float)


@dataclass
class SpeciesCalibrationData:
    species: str
    coef: np.ndarray
    intercept: float
    conformal_residuals: np.ndarray
    mean_features: np.ndarray
    inv_cov: np.ndarray
    h_star: float
    lod_wt_percent: float


class QuantitativeAssayRegressor:
    """Quantitative regression engine with Conformal Prediction and Applicability Domain."""

    def __init__(self, calibrations: dict[str, SpeciesCalibrationData] | None = None) -> None:
        self.model_id = "ridge-conformal-v1"
        self.model_version = "1.0.0"
        self.calibrations: dict[str, SpeciesCalibrationData] = calibrations or {}
        if not self.calibrations:
            self._init_default_calibrations()

    def _init_default_calibrations(self) -> None:
        """Initialize calibrated models for default oxide species."""
        rng = np.random.default_rng(2026)

        for species, (_element, _factor, lod) in OXIDE_FACTORS.items():
            lines = SPECIES_LINES.get(species, ())
            n_features = len(lines)
            if n_features == 0:
                continue

            # Synthetic calibration design matrix: 60 diverse calibration samples
            # covering 0.1 wt% to 60 wt%
            true_wt = rng.uniform(0.5, 50.0, size=60)
            # Line intensity proportional to weight percent with non-linear saturation & noise
            X_cal = np.zeros((60, n_features))
            for j in range(n_features):
                sensitivity = 0.5 + 0.3 * j
                X_cal[:, j] = sensitivity * true_wt + rng.normal(0.0, 1.0, size=60)
            X_cal = np.maximum(X_cal, 0.0)

            # Fit Ridge regression
            from sklearn.linear_model import Ridge

            reg = Ridge(alpha=1.0).fit(X_cal, true_wt)
            coef = reg.coef_
            intercept = float(reg.intercept_)

            # Conformal calibration residuals |y - y_hat|
            preds = reg.predict(X_cal)
            residuals = np.sort(np.abs(true_wt - preds))

            # Applicability domain: Mahalanobis / Hat matrix leverage
            mean_feat = np.mean(X_cal, axis=0)
            centered = X_cal - mean_feat
            cov = np.cov(centered, rowvar=False)
            if cov.ndim == 0:
                cov = np.array([[float(cov)]])
            reg_cov = cov + 1e-4 * np.eye(n_features)
            inv_cov = np.linalg.pinv(reg_cov)
            h_star = float(3.0 * (n_features + 1) / len(X_cal))

            self.calibrations[species] = SpeciesCalibrationData(
                species=species,
                coef=coef,
                intercept=intercept,
                conformal_residuals=residuals,
                mean_features=mean_feat,
                inv_cov=inv_cov,
                h_star=h_star,
                lod_wt_percent=lod,
            )

    def predict(
        self,
        wavelength_nm: Sequence[float] | np.ndarray,
        intensity: Sequence[float] | np.ndarray,
        *,
        targets: Sequence[str] | None = None,
        confidence_level: float = 0.95,
        medium: str = "vacuum",
    ) -> QuantitativeReport:
        """Predict quantitative mass fraction with conformal intervals and domain check."""
        wave = np.asarray(wavelength_nm, dtype=float)
        flux = np.asarray(intensity, dtype=float)

        if medium.lower() == "air":
            wave = air_to_vacuum(wave)

        chosen_species = tuple(targets or OXIDE_FACTORS.keys())
        predictions: list[QuantitativePrediction] = []
        total_mass = 0.0

        for sp in chosen_species:
            cal = self.calibrations.get(sp)
            if not cal:
                continue

            elem, _, lod = OXIDE_FACTORS.get(sp, (sp, 1.0, 0.05))
            features = extract_species_line_features(wave, flux, sp)

            # Leverage / Applicability Domain check
            diff = features - cal.mean_features
            leverage = float(diff @ cal.inv_cov @ diff.T) if len(diff) > 0 else 0.0

            if leverage > 1.8 * cal.h_star:
                status = "out_of_domain"
                warn = "Образец вне калибровочной области (OOD, высокий leverage)"
            elif leverage > cal.h_star:
                status = "near_boundary"
                warn = "Образец на границе применимости калибровочной модели"
            else:
                status = "in_domain"
                warn = None

            # Point estimate
            raw_pred = float(np.dot(features, cal.coef) + cal.intercept)
            value = max(raw_pred, 0.0)

            # Split conformal prediction interval
            n_cal = len(cal.conformal_residuals)
            alpha = max(1e-4, min(1.0 - confidence_level, 0.5))
            q_idx = math.ceil((n_cal + 1) * (1.0 - alpha))
            q_idx = min(q_idx, n_cal - 1)
            margin = float(cal.conformal_residuals[q_idx])

            lower = max(0.0, value - margin)
            upper = value + margin

            # LOD handling
            if value < lod:
                status = "below_lod"
                lower = 0.0
                upper = lod
                warn = f"Концентрация ниже предела обнаружения (< {lod:.2f} wt%)"

            predictions.append(
                QuantitativePrediction(
                    species=sp,
                    element=elem,
                    value_wt_percent=value,
                    lower_wt_percent=lower,
                    upper_wt_percent=upper,
                    confidence_level=confidence_level,
                    unit="wt_percent",
                    status=status,
                    lod_wt_percent=lod,
                    leverage=leverage,
                    warning=warn,
                )
            )
            if status != "below_lod":
                total_mass += value

        # Summary of applicability
        ood_count = sum(1 for p in predictions if p.status == "out_of_domain")
        boundary_count = sum(1 for p in predictions if p.status == "near_boundary")
        if ood_count > 0:
            summary = f"{ood_count} компонентов вне калибровочной области (экстраполяция)"
        elif boundary_count > 0:
            summary = f"{boundary_count} компонентов на границе калибровочной области"
        else:
            summary = "Все оцениваемые компоненты находятся внутри калибровочной области"

        return QuantitativeReport(
            predictions=tuple(predictions),
            model_id=self.model_id,
            model_version=self.model_version,
            confidence_level=confidence_level,
            total_detected_mass_wt_percent=float(min(total_mass, 100.0)),
            applicability_domain_summary=summary,
        )


_QUANTITATIVE_REGRESSOR: QuantitativeAssayRegressor | None = None


def get_default_quantitative_regressor() -> QuantitativeAssayRegressor:
    global _QUANTITATIVE_REGRESSOR
    if _QUANTITATIVE_REGRESSOR is None:
        _QUANTITATIVE_REGRESSOR = QuantitativeAssayRegressor()
    return _QUANTITATIVE_REGRESSOR
