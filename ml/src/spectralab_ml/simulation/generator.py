from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass
from typing import Any

import numpy as np
from scipy.integrate import quad
from scipy.special import ndtr, voigt_profile

from spectralab_ml.data.reference import ReferenceCatalog

GENERATOR_UNITS = {
    "profile": "pixel-integrated unit-area profile; finite-window tails are lost",
    "instrumental_fwhm_nm": "nm, Gaussian FWHM",
    "lorentzian_fwhm_nm": "nm, Lorentzian FWHM (Voigt only)",
    "noise_model": "Gaussian prescribed SNR or Poisson photon counts plus read noise",
    "photon_scale": "expected photon counts per arbitrary signal unit",
    "read_noise_std": "arbitrary signal units, standard deviation",
    "response_slope": "dimensionless edge-to-center response change; center response=1",
    "saturation_level": "arbitrary signal units, upper clipping after noise; null disables",
    "snr_db": "dB (Gaussian only); null does not imply noiseless Poisson/read mode",
    "ion_stage": "I=1 neutral; II=2 singly ionized; element labels aggregate by presence",
    "component_weights": "relative simulated contributions, not concentrations",
}


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
    profile: str = "gaussian"
    lorentzian_fwhm_nm: float = 0.0
    noise_model: str = "gaussian"
    photon_scale: float = 1000.0
    read_noise_std: float = 0.0
    response_slope: float = 0.0
    saturation_level: float | None = None
    ion_stage: int = 1

    def validate(self) -> None:
        if self.profile not in {"gaussian", "voigt"}:
            raise ValueError("profile must be gaussian or voigt")
        if self.noise_model not in {"gaussian", "poisson_read"}:
            raise ValueError("noise_model must be gaussian or poisson_read")
        if type(self.ion_stage) is not int or not 1 <= self.ion_stage <= 5:
            raise ValueError("ion_stage must be an integer in [1, 5]")
        for name, low, high in (
            ("lorentzian_fwhm_nm", 0, 20),
            ("photon_scale", 1e-6, 1e9),
            ("read_noise_std", 0, 1e6),
            ("response_slope", -1, 1),
        ):
            value = getattr(self, name)
            if not math.isfinite(value) or not low <= value <= high:
                raise ValueError(f"{name} must be finite and in [{low}, {high}]")
        if self.profile == "gaussian" and self.lorentzian_fwhm_nm != 0:
            raise ValueError("lorentzian_fwhm_nm requires profile=voigt")
        if self.noise_model == "poisson_read" and self.snr_db is not None:
            raise ValueError("Poisson/read noise requires snr_db=null; SNR is not prescribed")
        if self.saturation_level is not None and (
            not math.isfinite(self.saturation_level) or self.saturation_level <= 0
        ):
            raise ValueError("saturation_level must be positive and finite, or null")
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


def pixel_integrated_voigt(
    centers_nm: np.ndarray,
    line_center_nm: float,
    gaussian_fwhm_nm: float,
    lorentzian_fwhm_nm: float,
) -> np.ndarray:
    """Pixel masses of a unit-area Voigt; lost out-of-window tails are NOT renormalized."""
    if lorentzian_fwhm_nm == 0:
        return pixel_integrated_gaussian(centers_nm, line_center_nm, gaussian_fwhm_nm)
    sigma = gaussian_fwhm_nm / (2 * math.sqrt(2 * math.log(2)))
    gamma = lorentzian_fwhm_nm / 2
    if len(centers_nm) < 2 or sigma <= 0 or gamma < 0:
        raise ValueError("Voigt requires positive Gaussian width and nonnegative Lorentz width")
    mids = (centers_nm[:-1] + centers_nm[1:]) / 2
    edges = np.r_[2 * centers_nm[0] - mids[0], mids, 2 * centers_nm[-1] - mids[-1]]
    half = np.diff(edges) / 2
    centers = (edges[1:] + edges[:-1]) / 2 - line_center_nm
    nodes, weights = np.polynomial.legendre.leggauss(16)
    masses = half * (
        voigt_profile(centers[:, None] + half[:, None] * nodes, sigma, gamma) @ weights
    )
    # Adaptive integration resolves subpixel narrow cores on a coarse detector grid.
    core = np.flatnonzero(
        (half > min(sigma, gamma) * 2) & (abs(centers) < 8 * (sigma + gamma) + half)
    )
    for index in core:
        lo, hi = edges[index] - line_center_nm, edges[index + 1] - line_center_nm
        points = [v for v in (-sigma, 0.0, sigma) if lo < v < hi]
        masses[index] = quad(
            lambda x: voigt_profile(x, sigma, gamma), lo, hi, points=points, epsabs=1e-11, limit=100
        )[0]
    return masses


def generate_spectrum(
    config: GenerationConfig,
    catalog: ReferenceCatalog,
    *,
    class_order: tuple[str, ...],
    base_sample_id: str | None = None,
) -> Spectrum:
    config.validate()
    if any(line.wavelength_medium != "vacuum" for line in catalog.lines):
        raise ValueError("Convert reference wavelengths explicitly to vacuum before generation")
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
        if line.ion_stage != config.ion_stage:
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
        if config.profile == "voigt":
            profile = pixel_integrated_voigt(
                wavelengths,
                line.wavelength_nm + config.calibration_shift_nm,
                config.instrumental_fwhm_nm,
                config.lorentzian_fwhm_nm,
            )
        else:
            profile = pixel_integrated_gaussian(
                wavelengths,
                line.wavelength_nm + config.calibration_shift_nm,
                config.instrumental_fwhm_nm,
            )
        clean += area * profile
    if not np.any(clean > 0):
        raise ValueError("No reference lines fall inside the selected wavelength range")
    center = (config.wavelength_start_nm + config.wavelength_end_nm) / 2
    response = 1 + config.response_slope * (wavelengths - center) / (
        (config.wavelength_end_nm - config.wavelength_start_nm) / 2
    )
    clean *= response
    background = config.background_level + config.background_slope * (wavelengths - center)
    observed = clean + background
    if config.noise_model == "poisson_read":
        if np.any(observed < 0) or np.max(observed) * config.photon_scale > 1e15:
            raise ValueError("Poisson expectations must be nonnegative and below 1e15 counts")
        observed = rng.poisson(observed * config.photon_scale) / config.photon_scale
    elif config.snr_db is not None:
        signal_power = float(np.mean(clean**2))
        noise_std = math.sqrt(signal_power) / (10 ** (config.snr_db / 20))
        observed = observed + rng.normal(0.0, noise_std, size=observed.shape)
    if config.read_noise_std:
        observed += rng.normal(0, config.read_noise_std, size=observed.shape)
    if config.saturation_level is not None:
        observed = np.minimum(observed, config.saturation_level)
        clean = np.minimum(clean, config.saturation_level)
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
