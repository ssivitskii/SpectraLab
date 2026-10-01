from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass
from typing import Any

import numpy as np
from scipy.special import ndtr

from spectralab_ml.data.reference import ReferenceCatalog


@dataclass(frozen=True)
class GenerationConfig:
    elements: tuple[str, ...]
    component_weights: tuple[float, ...]
    wavelength_start_nm: float = 350.0
    wavelength_end_nm: float = 800.0
    instrumental_fwhm_nm: float = 0.5
    sampling_step_nm: float = 0.05
    snr_db: float | None = 20.0
    background_level: float = 0.0
    background_slope: float = 0.0
    calibration_shift_nm: float = 0.0
    amplitude_variation: float = 0.08
    seed: int = 42
    max_points: int = 100_000

    def validate(self) -> None:
        if not 1 <= len(self.elements) <= 3:
            raise ValueError("A mixture must contain between 1 and 3 elements")
        if len(self.elements) != len(set(self.elements)):
            raise ValueError("Elements must be unique")
        if len(self.component_weights) != len(self.elements):
            raise ValueError("component_weights must match elements")
        if any(not math.isfinite(value) or value <= 0 for value in self.component_weights):
            raise ValueError("component_weights must be positive and finite")
        finite_values = {
            "wavelength_start_nm": self.wavelength_start_nm,
            "wavelength_end_nm": self.wavelength_end_nm,
            "instrumental_fwhm_nm": self.instrumental_fwhm_nm,
            "sampling_step_nm": self.sampling_step_nm,
            "background_level": self.background_level,
            "background_slope": self.background_slope,
            "calibration_shift_nm": self.calibration_shift_nm,
            "amplitude_variation": self.amplitude_variation,
        }
        invalid = [name for name, value in finite_values.items() if not math.isfinite(value)]
        if invalid:
            raise ValueError(f"Generation parameters must be finite: {', '.join(invalid)}")
        if self.instrumental_fwhm_nm <= 0 or self.sampling_step_nm <= 0:
            raise ValueError("FWHM and sampling step must be positive")
        if self.wavelength_start_nm >= self.wavelength_end_nm:
            raise ValueError("Invalid wavelength range")
        if self.snr_db is not None and not math.isfinite(self.snr_db):
            raise ValueError("snr_db must be finite or null")
        if self.amplitude_variation < 0:
            raise ValueError("amplitude_variation must be non-negative")
        if self.snr_db is not None and not -40 <= self.snr_db <= 120:
            raise ValueError("snr_db must be within [-40, 120], or null for no noise")
        if not 0 <= self.amplitude_variation <= 2:
            raise ValueError("amplitude_variation must be within [0, 2]")
        if not 0.001 <= self.instrumental_fwhm_nm <= 20:
            raise ValueError("instrumental_fwhm_nm must be within [0.001, 20]")
        if (
            max(self.component_weights) > 1000
            or abs(self.background_level) > 1e6
            or abs(self.background_slope) > 1e4
        ):
            raise ValueError("Signal scale exceeds configured research bounds")
        if abs(self.calibration_shift_nm) > 5 or self.seed < 0:
            raise ValueError("Invalid calibration shift or seed")
        if not 2 <= self.max_points <= 100_000:
            raise ValueError("max_points must be within [2, 100000]")
        intervals = (self.wavelength_end_nm - self.wavelength_start_nm) / self.sampling_step_nm
        if not math.isfinite(intervals) or intervals + 1 > self.max_points:
            raise ValueError(f"Generated spectrum exceeds {self.max_points} points")
        if not math.isclose(intervals, round(intervals), rel_tol=0, abs_tol=1e-8):
            raise ValueError("sampling_step_nm must divide the selected wavelength span")


@dataclass
class Spectrum:
    wavelength_nm: np.ndarray
    intensity: np.ndarray
    clean_signal: np.ndarray
    true_labels: dict[str, int]
    parameters: dict[str, Any]
    base_sample_id: str
    reference_source: str

    def to_dict(self, include_truth: bool = True) -> dict[str, Any]:
        result: dict[str, Any] = {
            "wavelength_nm": self.wavelength_nm.tolist(),
            "intensity": self.intensity.tolist(),
            "clean_signal": self.clean_signal.tolist(),
            "parameters": self.parameters,
            "base_sample_id": self.base_sample_id,
            "reference_source": self.reference_source,
        }
        if include_truth:
            result["true_labels"] = self.true_labels
        return result


def pixel_integrated_gaussian(
    centers_nm: np.ndarray, line_center_nm: float, fwhm_nm: float
) -> np.ndarray:
    """Return Gaussian mass in each detector pixel; its in-range sum approaches one."""
    sigma = fwhm_nm / (2 * math.sqrt(2 * math.log(2)))
    if centers_nm.size < 2:
        raise ValueError("At least two detector pixels are required")
    midpoints = (centers_nm[:-1] + centers_nm[1:]) / 2
    edges = np.concatenate(
        (
            [centers_nm[0] - (midpoints[0] - centers_nm[0])],
            midpoints,
            [centers_nm[-1] + (centers_nm[-1] - midpoints[-1])],
        )
    )
    return ndtr((edges[1:] - line_center_nm) / sigma) - ndtr((edges[:-1] - line_center_nm) / sigma)


def generate_spectrum(
    config: GenerationConfig,
    catalog: ReferenceCatalog,
    *,
    class_order: tuple[str, ...],
    base_sample_id: str | None = None,
) -> Spectrum:
    config.validate()
    unsupported = set(config.elements) - set(class_order)
    if unsupported:
        raise ValueError(f"Unsupported elements: {', '.join(sorted(unsupported))}")
    point_count = (
        round((config.wavelength_end_nm - config.wavelength_start_nm) / config.sampling_step_nm) + 1
    )
    wavelengths = np.linspace(config.wavelength_start_nm, config.wavelength_end_nm, point_count)
    rng = np.random.default_rng(config.seed)
    clean = np.zeros_like(wavelengths)
    weights = dict(zip(config.elements, config.component_weights, strict=True))
    for line in catalog.for_elements(config.elements):
        if line.ion_stage != 1:
            continue
        if not config.wavelength_start_nm <= line.wavelength_nm <= config.wavelength_end_nm:
            continue
        prior = 1.0 if line.relative_intensity is None else line.relative_intensity
        # Log-normal variation remains positive and has expectation approximately one.
        variation = rng.lognormal(
            mean=-(config.amplitude_variation**2) / 2,
            sigma=config.amplitude_variation,
        )
        area = weights[line.element] * prior * variation
        clean += area * pixel_integrated_gaussian(
            wavelengths,
            line.wavelength_nm + config.calibration_shift_nm,
            config.instrumental_fwhm_nm,
        )
    if not np.any(clean > 0):
        raise ValueError("No reference lines fall inside the selected wavelength range")
    center = (config.wavelength_start_nm + config.wavelength_end_nm) / 2
    background = config.background_level + config.background_slope * (wavelengths - center)
    observed = clean + background
    if config.snr_db is not None:
        signal_power = float(np.mean(clean**2))
        noise_std = math.sqrt(signal_power) / (10 ** (config.snr_db / 20))
        observed = observed + rng.normal(0.0, noise_std, size=observed.shape)
    if not np.all(np.isfinite(observed)):
        raise ValueError("Generated spectrum contains non-finite values")
    labels = {element: int(element in config.elements) for element in class_order}
    base_identity = {
        "composition": sorted(zip(config.elements, config.component_weights, strict=True)),
        "intrinsic_seed": config.seed,
    }
    identifier = (
        base_sample_id
        or "synthetic-"
        + hashlib.sha256(json.dumps(base_identity, sort_keys=True).encode()).hexdigest()[:16]
    )
    return Spectrum(
        wavelength_nm=wavelengths,
        intensity=observed,
        clean_signal=clean,
        true_labels=labels,
        parameters=asdict(config),
        base_sample_id=identifier,
        reference_source=catalog.source_name,
    )
