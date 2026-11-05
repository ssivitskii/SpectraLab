"""Applicability Domain projection in feature space (PCA, Hotelling T2, Q-residuals).

Provides chemometric diagnostics for model extrapolation and multivariate domain control.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any

import numpy as np
from scipy import stats

from spectralab_ml.data.wavelengths import air_to_vacuum
from spectralab_ml.models.quantitative import (
    OXIDE_FACTORS,
    SPECIES_LINES,
    extract_species_line_features,
)


@dataclass(frozen=True)
class FeatureContribution:
    species: str
    line_wl_nm: float
    contribution_percent: float
    deviation_direction: str  # "high" or "low"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ApplicabilitySpaceReport:
    sample_pc1: float
    sample_pc2: float
    t_squared: float
    t_squared_limit_95: float
    t_squared_limit_99: float
    q_residual: float
    q_residual_limit_95: float
    status: str  # "in_domain", "near_boundary", "out_of_domain"
    variance_explained: tuple[float, float]
    calibration_scores: tuple[tuple[float, float, str], ...]
    ellipse_95: tuple[tuple[float, float], ...]
    ellipse_99: tuple[tuple[float, float], ...]
    top_contributions: tuple[FeatureContribution, ...]
    summary: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "sample_pc1": self.sample_pc1,
            "sample_pc2": self.sample_pc2,
            "t_squared": self.t_squared,
            "t_squared_limit_95": self.t_squared_limit_95,
            "t_squared_limit_99": self.t_squared_limit_99,
            "q_residual": self.q_residual,
            "q_residual_limit_95": self.q_residual_limit_95,
            "status": self.status,
            "variance_explained": list(self.variance_explained),
            "calibration_scores": [list(pt) for pt in self.calibration_scores],
            "ellipse_95": [list(pt) for pt in self.ellipse_95],
            "ellipse_99": [list(pt) for pt in self.ellipse_99],
            "top_contributions": [c.to_dict() for c in self.top_contributions],
            "summary": self.summary,
        }


def extract_multivariate_features(
    grid_nm: np.ndarray,
    radiance: np.ndarray,
) -> tuple[np.ndarray, list[tuple[str, float]]]:
    """Extract all 24 quantitative analytical line features across all standard oxides."""
    features = []
    metadata = []
    for sp in OXIDE_FACTORS:
        lines = SPECIES_LINES.get(sp, ())
        feats = extract_species_line_features(grid_nm, radiance, sp)
        for i, wl in enumerate(lines):
            val = float(feats[i]) if i < len(feats) else 0.0
            features.append(val)
            metadata.append((sp, wl))

    return np.asarray(features, dtype=float), metadata


class ApplicabilitySpaceModel:
    """Chemometric PCA, Hotelling T^2 and Q-residuals Applicability Domain model."""

    def __init__(self, seed: int = 2026, n_cal_samples: int = 80) -> None:
        self.n_cal = n_cal_samples
        self.seed = seed
        self._build_model()

    def _build_model(self) -> None:
        rng = np.random.default_rng(self.seed)

        # Build feature metadata
        self.feature_meta: list[tuple[str, float]] = []
        for sp in OXIDE_FACTORS:
            for wl in SPECIES_LINES.get(sp, ()):
                self.feature_meta.append((sp, wl))

        # Diverse calibration standards: simulated geological matrix clusters
        # (basalt, andesite, granite, peridotite)
        clusters = [
            # FeOT, MgO, CaO, TiO2, SiO2, Al2O3 base scales
            [12.0, 7.0, 10.0, 1.5, 50.0, 15.0],  # basalt
            [6.0, 3.0, 6.0, 0.8, 60.0, 17.0],  # andesite
            [2.0, 0.5, 1.5, 0.3, 72.0, 14.0],  # granite
            [9.0, 38.0, 3.0, 0.2, 42.0, 4.0],  # peridotite
        ]
        samples_per_cluster = self.n_cal // len(clusters)

        wave_grid = np.linspace(250.0, 650.0, 4000)
        X_list = []
        labels = ["Базальт", "Андезит", "Гранит", "Перидотит"]
        self.cal_labels: list[str] = []

        for c_idx, base_wt in enumerate(clusters):
            lbl = labels[c_idx]
            for _ in range(samples_per_cluster):
                flux = np.full_like(wave_grid, 1.0)
                for sp_idx, sp in enumerate(OXIDE_FACTORS):
                    lines = SPECIES_LINES.get(sp, ())
                    wt = base_wt[sp_idx] * rng.uniform(0.75, 1.25)
                    for j, wl in enumerate(lines):
                        amp = wt * (0.8 + 0.2 * j)
                        flux += amp * np.exp(-0.5 * ((wave_grid - wl) / 0.05) ** 2)
                feats, _ = extract_multivariate_features(wave_grid, flux)
                X_list.append(feats)
                self.cal_labels.append(lbl)

        self.X_cal = np.asarray(X_list, dtype=float)

        # Standardize
        self.mean = np.mean(self.X_cal, axis=0)
        self.std = np.std(self.X_cal, axis=0)
        self.std[self.std < 1e-6] = 1.0

        X_scaled = (self.X_cal - self.mean) / self.std

        # PCA via SVD
        _u, s, vt = np.linalg.svd(X_scaled, full_matrices=False)
        self.loadings = vt[:2].T  # shape (n_features, 2)
        total_var = np.sum(s**2)
        self.var_explained = (float(s[0] ** 2 / total_var), float(s[1] ** 2 / total_var))

        # Scores
        self.cal_scores = X_scaled @ self.loadings  # shape (n_cal, 2)

        # Variance of scores
        self.s1_sq = float(np.var(self.cal_scores[:, 0], ddof=1))
        self.s2_sq = float(np.var(self.cal_scores[:, 1], ddof=1))
        self.s1_sq = max(self.s1_sq, 1e-6)
        self.s2_sq = max(self.s2_sq, 1e-6)

        # Hotelling T^2 critical limits (k=2)
        n = self.n_cal
        k = 2
        f_mult = (k * (n - 1)) / (n - k)
        f_95 = stats.f.ppf(0.95, k, n - k)
        f_99 = stats.f.ppf(0.99, k, n - k)
        self.t2_limit_95 = float(f_mult * f_95)
        self.t2_limit_99 = float(f_mult * f_99)

        # Q-residuals of calibration standards
        recon = self.cal_scores @ self.loadings.T
        residuals = X_scaled - recon
        cal_q = np.sum(residuals**2, axis=1)
        self.q_limit_95 = float(np.percentile(cal_q, 95))
        self.q_limit_99 = float(np.percentile(cal_q, 99))

        # Build ellipses (64 points)
        theta = np.linspace(0.0, 2.0 * math.pi, 64)
        r1_95 = math.sqrt(self.t2_limit_95 * self.s1_sq)
        r2_95 = math.sqrt(self.t2_limit_95 * self.s2_sq)
        self.ellipse_95 = tuple(
            (float(r1_95 * math.cos(t)), float(r2_95 * math.sin(t))) for t in theta
        )

        r1_99 = math.sqrt(self.t2_limit_99 * self.s1_sq)
        r2_99 = math.sqrt(self.t2_limit_99 * self.s2_sq)
        self.ellipse_99 = tuple(
            (float(r1_99 * math.cos(t)), float(r2_99 * math.sin(t))) for t in theta
        )

    def analyze(
        self,
        wavelength_nm: np.ndarray,
        intensity: np.ndarray,
        *,
        medium: str = "vacuum",
    ) -> ApplicabilitySpaceReport:
        """Project query spectrum into PCA space and assess applicability domain."""
        wave = np.asarray(wavelength_nm, dtype=float)
        flux = np.asarray(intensity, dtype=float)

        if medium.lower() == "air":
            wave = air_to_vacuum(wave)

        feats, _ = extract_multivariate_features(wave, flux)
        x_scaled = (feats - self.mean) / self.std

        # Scores
        sample_pc = x_scaled @ self.loadings
        pc1, pc2 = float(sample_pc[0]), float(sample_pc[1])

        # Hotelling T^2
        t_squared = float((pc1**2) / self.s1_sq + (pc2**2) / self.s2_sq)

        # Q-residual
        recon = sample_pc @ self.loadings.T
        diff = x_scaled - recon
        q_res = float(np.sum(diff**2))

        # Status
        if t_squared > self.t2_limit_99 or q_res > self.q_limit_99:
            status = "out_of_domain"
            summary = (
                f"Образец находится вне калибровочной области (OOD): "
                f"T²={t_squared:.2f} (порог 99%: {self.t2_limit_99:.2f}), "
                f"Q-residual={q_res:.2f} (порог 99%: {self.q_limit_99:.2f})."
            )
        elif t_squared > self.t2_limit_95 or q_res > self.q_limit_95:
            status = "near_boundary"
            summary = (
                f"Образец находится на границе применимости (Near Boundary): "
                f"T²={t_squared:.2f} (порог 95%: {self.t2_limit_95:.2f}), "
                f"Q-residual={q_res:.2f}."
            )
        else:
            status = "in_domain"
            summary = (
                f"Образец находится внутри хемометрической области применимости: "
                f"T²={t_squared:.2f} ≤ {self.t2_limit_95:.2f}, "
                f"Q-residual={q_res:.2f} ≤ {self.q_limit_95:.2f}."
            )

        # Top feature contributions to Q-residual and deviation
        contributions: list[FeatureContribution] = []
        denom = max(q_res, 1e-9)
        squared_diff = diff**2
        top_indices = np.argsort(squared_diff)[::-1][:4]

        for idx in top_indices:
            sp, wl = self.feature_meta[idx]
            pct = float((squared_diff[idx] / denom) * 100.0)
            direction = "high" if diff[idx] > 0 else "low"
            contributions.append(
                FeatureContribution(
                    species=sp,
                    line_wl_nm=wl,
                    contribution_percent=pct,
                    deviation_direction=direction,
                )
            )

        cal_pts = tuple(
            (float(self.cal_scores[i, 0]), float(self.cal_scores[i, 1]), self.cal_labels[i])
            for i in range(len(self.cal_scores))
        )

        return ApplicabilitySpaceReport(
            sample_pc1=pc1,
            sample_pc2=pc2,
            t_squared=t_squared,
            t_squared_limit_95=self.t2_limit_95,
            t_squared_limit_99=self.t2_limit_99,
            q_residual=q_res,
            q_residual_limit_95=self.q_limit_95,
            status=status,
            variance_explained=self.var_explained,
            calibration_scores=cal_pts,
            ellipse_95=self.ellipse_95,
            ellipse_99=self.ellipse_99,
            top_contributions=tuple(contributions),
            summary=summary,
        )


_DEFAULT_APPLICABILITY_MODEL: ApplicabilitySpaceModel | None = None


def get_default_applicability_model() -> ApplicabilitySpaceModel:
    global _DEFAULT_APPLICABILITY_MODEL
    if _DEFAULT_APPLICABILITY_MODEL is None:
        _DEFAULT_APPLICABILITY_MODEL = ApplicabilitySpaceModel()
    return _DEFAULT_APPLICABILITY_MODEL
