"""Quality control and robust aggregation for one series of LIBS shots.

Shots from one physical measurement are repeated observations, not independent
samples for model validation. This module keeps each decision in a receipt so a
caller can distinguish rejected shots from an insufficient series.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

import numpy as np

MAX_SERIES_POINTS = 100_000
MAX_SERIES_SHOTS = 1000
MAX_SERIES_VALUES = 2_000_000


@dataclass(frozen=True)
class ShotSeriesPolicy:
    """Explicit conservative policy for a series acquired on one wavelength axis."""

    min_snr: float = 3.0
    min_accepted_shots: int = 2
    saturation_ceiling: float | None = None
    outlier_mad_z: float = 6.0

    def validate(self) -> None:
        if not isinstance(self.min_snr, (int, float)) or isinstance(self.min_snr, bool):
            raise ValueError("min_snr must be a finite nonnegative number")
        if not np.isfinite(self.min_snr) or self.min_snr < 0:
            raise ValueError("min_snr must be a finite nonnegative number")
        if (
            not isinstance(self.min_accepted_shots, int)
            or isinstance(self.min_accepted_shots, bool)
            or self.min_accepted_shots < 1
        ):
            raise ValueError("min_accepted_shots must be a positive integer")
        if self.saturation_ceiling is not None and (
            not isinstance(self.saturation_ceiling, (int, float))
            or isinstance(self.saturation_ceiling, bool)
            or not np.isfinite(self.saturation_ceiling)
        ):
            raise ValueError("saturation_ceiling must be finite when declared")
        if not isinstance(self.outlier_mad_z, (int, float)) or isinstance(self.outlier_mad_z, bool):
            raise ValueError("outlier_mad_z must be a finite positive number")
        if not np.isfinite(self.outlier_mad_z) or self.outlier_mad_z <= 0:
            raise ValueError("outlier_mad_z must be a finite positive number")

    def receipt(self) -> dict:
        return {
            "min_snr": float(self.min_snr),
            "min_accepted_shots": self.min_accepted_shots,
            "saturation_ceiling": (
                None if self.saturation_ceiling is None else float(self.saturation_ceiling)
            ),
            "outlier_mad_z": float(self.outlier_mad_z),
        }


@dataclass(frozen=True)
class ShotSeriesResult:
    """Aggregated spectrum, per-shot diagnostics, and a portable receipt."""

    wavelength_nm: np.ndarray
    intensity: np.ndarray | None
    pointwise_mad: np.ndarray | None
    receipt: dict


DEFAULT_POLICY = ShotSeriesPolicy()


def _validate_arrays(wavelength_nm: np.ndarray, shots: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    wavelength = np.asarray(wavelength_nm, dtype=np.float64)
    values = np.asarray(shots, dtype=np.float64)
    if wavelength.ndim != 1 or len(wavelength) < 2:
        raise ValueError("wavelength_nm must be a one-dimensional axis with at least two points")
    if not np.isfinite(wavelength).all() or not np.all(np.diff(wavelength) > 0):
        raise ValueError("wavelength_nm must be finite and strictly increasing")
    if values.ndim != 2 or values.shape[0] < 1 or values.shape[1] != len(wavelength):
        raise ValueError("shots must have shape (shot_count, wavelength_count)")
    if (
        len(wavelength) > MAX_SERIES_POINTS
        or len(values) > MAX_SERIES_SHOTS
        or values.size > MAX_SERIES_VALUES
    ):
        raise ValueError("Series exceeds point, shot or total-value limits")
    if not np.isfinite(values).all():
        raise ValueError("shots must contain only finite values")
    return wavelength, values


def _snr(values: np.ndarray) -> float:
    """Robust signal span over adjacent-point noise; constants have zero SNR."""
    span = float(np.percentile(values, 95) - np.percentile(values, 5))
    differences = np.diff(values)
    noise = float(1.4826 * np.median(np.abs(differences - np.median(differences))))
    if noise == 0:
        return float("inf") if span > 0 else 0.0
    return span / noise


def _normalised_shape(values: np.ndarray) -> np.ndarray:
    centered = values - np.median(values)
    return centered / np.max(np.abs(centered))


def _source_sha256(wavelength: np.ndarray, shots: np.ndarray) -> str:
    digest = hashlib.sha256()
    digest.update(b"spectralab-shot-series-v2\0")
    digest.update(np.asarray(shots.shape, dtype="<i8").tobytes())
    digest.update(np.ascontiguousarray(wavelength, dtype="<f8").tobytes())
    digest.update(np.ascontiguousarray(shots, dtype="<f8").tobytes())
    return digest.hexdigest()


def aggregate_shot_series(
    wavelength_nm: np.ndarray,
    shots: np.ndarray,
    *,
    policy: ShotSeriesPolicy = DEFAULT_POLICY,
) -> ShotSeriesResult:
    """QC and median-aggregate shots acquired from one physical measurement.

    ``shots`` is shaped ``(shot_count, wavelength_count)``. Saturation is only
    evaluated when the caller declares the detector ceiling; an absent ceiling is
    recorded as an unavailable diagnostic rather than inferred from intensity.
    """
    policy.validate()
    wavelength, values = _validate_arrays(wavelength_nm, shots)
    # Unit scaling prevents intermediate overflow for finite detector values.
    scales = np.max(np.abs(values), axis=1)
    scaled = values / np.where(scales > 0, scales, 1.0)[:, None]
    diagnostics: list[dict] = []
    accepted: list[int] = []
    for index, shot in enumerate(scaled):
        snr = _snr(shot)
        reasons = []
        if (
            policy.saturation_ceiling is not None
            and np.max(values[index]) >= policy.saturation_ceiling
        ):
            reasons.append("saturated")
        if np.max(shot) == np.min(shot):
            reasons.append("zero_dynamic_range")
        if snr < policy.min_snr:
            reasons.append("low_snr")
        diagnostics.append(
            {
                "shot_index": index,
                "snr": None if not np.isfinite(snr) else float(snr),
                "snr_status": "zero_noise_estimate" if not np.isfinite(snr) else "finite",
                "normalised_shape_distance": None,
                "status": "rejected_quality" if reasons else "accepted",
                "reasons": reasons,
            }
        )
        if not reasons:
            accepted.append(index)

    if len(accepted) >= 3:
        shapes = np.vstack([_normalised_shape(scaled[index]) for index in accepted])
        reference = np.median(shapes, axis=0)
        distances = np.sqrt(np.mean((shapes - reference) ** 2, axis=1))
        center = float(np.median(distances))
        spread = float(1.4826 * np.median(np.abs(distances - center)))
        limit = center + policy.outlier_mad_z * spread
        limit = max(limit, 1e-6)
        for index, distance in zip(accepted.copy(), distances, strict=True):
            diagnostics[index]["normalised_shape_distance"] = float(distance)
            if distance > limit:
                diagnostics[index]["status"] = "rejected_quality"
                diagnostics[index]["reasons"].append("shape_outlier")
                accepted.remove(index)

    receipt = {
        "schema_version": 2,
        "source_sha256": _source_sha256(wavelength, values),
        "axis": {"units": "nm", "points": len(wavelength)},
        "shot_count": len(values),
        "policy": policy.receipt(),
        "shots": diagnostics,
        "accepted_shot_indices": accepted,
        "accepted_shot_count": len(accepted),
        "aggregation": "pointwise_median",
        "shape_distance": "rms_of_median_centered_max_abs_scaled_shots",
    }
    if len(accepted) < policy.min_accepted_shots:
        receipt["status"] = "insufficient_series"
        return ShotSeriesResult(wavelength, None, None, receipt)

    scale = float(np.max(np.abs(values[accepted])))
    accepted_values = values[accepted] / scale
    aggregate = np.median(accepted_values, axis=0)
    pointwise_mad = np.median(np.abs(accepted_values - aggregate), axis=0)
    with np.errstate(over="raise", invalid="raise"):
        try:
            aggregate = aggregate * scale
            pointwise_mad = pointwise_mad * scale
        except FloatingPointError as exc:
            raise ValueError("Series aggregation exceeds finite floating-point range") from exc
    receipt["status"] = "completed"
    receipt["accepted_shot_count"] = len(accepted)
    return ShotSeriesResult(wavelength, aggregate, pointwise_mad, receipt)
