"""LTE plasma diagnostics: Boltzmann temperature, Stark electron density and McWhirter check."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np

from spectralab_ml.config import repository_root
from spectralab_ml.data.plasma_reference import (
    BOLTZMANN_EV_PER_K,
    PlasmaReference,
)
from spectralab_ml.data.wavelengths import air_to_vacuum

# Speed of light in vacuum (nm/s) and Planck constant (eV*s)
PLANCK_EV_S = 4.135667696e-15
LIGHT_SPEED_NM_S = 2.99792458e17

# Curated reference transitions for Boltzmann plot (vacuum nm, Aki s^-1, Ek eV, Ei eV, gk)
# Chosen for low self-absorption (non-resonant where possible), strong Aki, and broad Ek spread.
CURATED_BOLTZMANN_LINES: dict[str, tuple[tuple[float, float, float, float, float], ...]] = {
    "Fe": (
        (386.101, 9.69e6, 3.21, 0.00, 9.0),
        (344.159, 1.71e7, 3.60, 0.00, 7.0),
        (382.151, 6.67e7, 4.10, 0.86, 9.0),
        (373.593, 9.01e7, 4.18, 0.86, 11.0),
        (375.055, 7.63e7, 4.22, 0.91, 9.0),
        (381.692, 1.12e8, 4.73, 1.48, 7.0),
        (426.167, 3.99e7, 5.31, 2.40, 11.0),
        (495.898, 4.22e7, 5.31, 2.81, 11.0),
        (523.440, 1.94e7, 5.31, 2.94, 11.0),
        (411.971, 4.96e7, 6.58, 3.57, 13.0),
        (376.661, 9.51e7, 6.53, 3.24, 15.0),
        (380.642, 8.60e7, 6.56, 3.30, 11.0),
        (374.453, 6.05e7, 6.88, 3.57, 11.0),
        (368.329, 1.50e8, 6.91, 3.55, 5.0),
        (323.398, 4.19e7, 7.07, 3.24, 15.0),
    ),
    "Ti": (
        (501.567, 6.80e7, 3.28, 0.81, 5.0),
        (453.451, 8.83e7, 3.58, 0.85, 11.0),
        (429.696, 1.30e8, 3.70, 0.81, 1.0),
        (378.712, 1.40e8, 4.17, 0.90, 3.0),
        (445.039, 9.70e7, 4.67, 1.89, 11.0),
        (485.737, 5.20e7, 4.81, 2.26, 15.0),
        (388.399, 1.49e8, 5.23, 2.04, 15.0),
        (321.887, 1.25e8, 5.82, 1.97, 5.0),
        (322.705, 1.56e8, 5.88, 2.04, 13.0),
    ),
    "Mg": (
        (518.505, 5.61e7, 5.11, 2.72, 3.0),
        (517.413, 3.37e7, 5.11, 2.71, 3.0),
        (383.339, 1.21e8, 5.95, 2.71, 5.0),
        (383.938, 1.61e8, 5.95, 2.72, 7.0),
        (309.779, 4.96e7, 6.72, 2.72, 7.0),
        (291.631, 4.09e8, 10.00, 5.75, 5.0),
        (389.134, 1.31e8, 10.35, 7.17, 3.0),
        (281.059, 2.50e8, 10.36, 5.95, 7.0),
        (389.668, 2.35e8, 10.36, 7.18, 7.0),
    ),
    "Ca": (
        (443.621, 6.70e7, 4.68, 1.89, 5.0),
        (445.603, 8.70e7, 4.68, 1.90, 7.0),
        (430.895, 1.99e8, 4.76, 1.89, 1.0),
        (431.986, 7.40e7, 4.77, 1.90, 3.0),
        (430.374, 1.36e8, 4.78, 1.90, 5.0),
        (551.451, 1.10e8, 5.18, 2.93, 1.0),
        (410.969, 9.00e7, 5.73, 2.71, 7.0),
        (300.173, 1.58e8, 6.02, 1.89, 1.0),
        (300.774, 7.50e7, 6.02, 1.90, 5.0),
    ),
}

_PLASMA_REF_CACHE: PlasmaReference | None = None


def get_default_plasma_reference(root: Path | None = None) -> PlasmaReference:
    """Load or return cached NIST Plasma Reference v3."""
    global _PLASMA_REF_CACHE
    if _PLASMA_REF_CACHE is not None:
        return _PLASMA_REF_CACHE
    base = root or repository_root()
    path = base / "data" / "reference" / "nist_plasma_v3" / "plasma_reference.json"
    _PLASMA_REF_CACHE = PlasmaReference.load(path)
    return _PLASMA_REF_CACHE


@dataclass(frozen=True)
class BoltzmannPoint:
    element: str
    ion_stage: int
    wavelength_nm: float
    ek_ev: float
    ei_ev: float
    gk: float
    aki: float
    net_intensity: float
    y_value: float
    residual: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class BoltzmannFitResult:
    temperature_k: float
    temperature_uncertainty_k: float
    r_squared: float
    slope: float
    intercept: float
    slope_stderr: float
    element: str
    ion_stage: int
    points: tuple[BoltzmannPoint, ...]
    confidence_score: float
    status: str
    status_message: str

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["points"] = [p.to_dict() for p in self.points]
        return data


@dataclass(frozen=True)
class StarkDensityResult:
    electron_density_cm3: float | None
    line_name: str
    center_wavelength_nm: float
    observed_fwhm_nm: float
    instrumental_fwhm_nm: float
    stark_fwhm_nm: float
    status: str
    status_message: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class McWhirterResult:
    criterion_satisfied: bool | None
    min_electron_density_cm3: float
    temperature_k: float
    energy_gap_ev: float
    status: str
    notes: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PlasmaDiagnosticsResult:
    boltzmann: BoltzmannFitResult | None
    stark: StarkDensityResult | None
    mcwhirter: McWhirterResult | None
    summary: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "boltzmann": self.boltzmann.to_dict() if self.boltzmann else None,
            "stark": self.stark.to_dict() if self.stark else None,
            "mcwhirter": self.mcwhirter.to_dict() if self.mcwhirter else None,
            "summary": self.summary,
        }


def _measure_line_intensity(
    grid_nm: np.ndarray,
    intensity: np.ndarray,
    target_wl_nm: float,
    *,
    half_width_nm: float = 0.06,
    side_inner_nm: float = 0.10,
    side_outer_nm: float = 0.22,
) -> tuple[float, float, float]:
    """Measure net integrated intensity, peak height, and local SNR of an emission line.

    Returns (net_integrated_area, net_peak_height, snr).
    """
    delta = np.abs(grid_nm - target_wl_nm)
    peak_mask = delta <= half_width_nm
    if not np.any(peak_mask) or np.sum(peak_mask) < 2:
        return 0.0, 0.0, 0.0

    side_mask = (delta >= side_inner_nm) & (delta <= side_outer_nm)
    if not np.any(side_mask):
        baseline = float(np.min(intensity[peak_mask]))
        noise = 1.0
    else:
        side_vals = intensity[side_mask]
        baseline = float(np.median(side_vals))
        mad = float(np.median(np.abs(side_vals - baseline)))
        noise = max(mad * 1.4826, 1e-12)

    peak_intensity = float(np.max(intensity[peak_mask]))
    net_peak = max(peak_intensity - baseline, 0.0)
    snr = net_peak / noise

    net_curve = np.maximum(intensity[peak_mask] - baseline, 0.0)
    net_area = float(np.trapezoid(net_curve, grid_nm[peak_mask]))
    return net_area, net_peak, snr


def compute_boltzmann_fit(
    wavelength_nm: Sequence[float] | np.ndarray,
    intensity: Sequence[float] | np.ndarray,
    *,
    element: str = "Fe",
    ion_stage: int = 1,
    medium: str = "vacuum",
    reference_lines: Sequence[tuple[float, float, float, float, float]] | None = None,
    min_points: int = 4,
    min_snr: float = 3.0,
    half_width_nm: float = 0.06,
) -> BoltzmannFitResult:
    """Compute Boltzmann temperature regression Te from atomic emission lines.

    Equation:
        ln( (I_ki * lambda_ki) / (g_k * A_ki) ) = - E_k / (k_B * T_e) + C
    where E_k is in eV, k_B = 8.617333262e-5 eV/K.
    """
    wave = np.asarray(wavelength_nm, dtype=float)
    flux = np.asarray(intensity, dtype=float)

    if medium.lower() == "air":
        wave = air_to_vacuum(wave)

    lines = reference_lines or CURATED_BOLTZMANN_LINES.get(element)
    if not lines:
        return BoltzmannFitResult(
            temperature_k=0.0,
            temperature_uncertainty_k=0.0,
            r_squared=0.0,
            slope=0.0,
            intercept=0.0,
            slope_stderr=0.0,
            element=element,
            ion_stage=ion_stage,
            points=(),
            confidence_score=0.0,
            status="insufficient_lines",
            status_message=f"No curated reference lines for {element} {ion_stage}",
        )

    w_min, w_max = float(wave[0]), float(wave[-1])
    candidates = [line for line in lines if w_min + 0.3 <= line[0] <= w_max - 0.3]

    raw_points: list[dict[str, Any]] = []
    for wl, aki, ek, ei, gk in candidates:
        net_area, _, snr = _measure_line_intensity(wave, flux, wl, half_width_nm=half_width_nm)
        if snr >= min_snr and net_area > 0:
            y = float(math.log((net_area * wl) / (gk * aki)))
            raw_points.append(
                {
                    "wl": wl,
                    "aki": aki,
                    "ek": ek,
                    "ei": ei,
                    "gk": gk,
                    "net_area": net_area,
                    "y": y,
                }
            )

    if len(raw_points) < min_points:
        return BoltzmannFitResult(
            temperature_k=0.0,
            temperature_uncertainty_k=0.0,
            r_squared=0.0,
            slope=0.0,
            intercept=0.0,
            slope_stderr=0.0,
            element=element,
            ion_stage=ion_stage,
            points=(),
            confidence_score=0.0,
            status="insufficient_lines",
            status_message=(
                f"Found {len(raw_points)} valid lines for {element} {ion_stage}; "
                f"minimum required: {min_points}"
            ),
        )

    x_arr = np.array([p["ek"] for p in raw_points])
    y_arr = np.array([p["y"] for p in raw_points])

    ek_spread = float(np.max(x_arr) - np.min(x_arr))
    if ek_spread < 0.6:
        return BoltzmannFitResult(
            temperature_k=0.0,
            temperature_uncertainty_k=0.0,
            r_squared=0.0,
            slope=0.0,
            intercept=0.0,
            slope_stderr=0.0,
            element=element,
            ion_stage=ion_stage,
            points=(),
            confidence_score=0.0,
            status="weak_fit",
            status_message=f"Upper level energy spread ({ek_spread:.2f} eV) is too narrow",
        )

    # Linear regression: y = m * x + c
    poly, cov = np.polyfit(x_arr, y_arr, 1, cov=True)
    m, c = float(poly[0]), float(poly[1])
    slope_var = float(cov[0, 0])
    slope_stderr = math.sqrt(max(slope_var, 0.0))

    residuals = y_arr - (m * x_arr + c)
    ss_res = float(np.sum(residuals**2))
    ss_tot = float(np.sum((y_arr - np.mean(y_arr)) ** 2))
    r_squared = float(1.0 - (ss_res / ss_tot)) if ss_tot > 0 else 0.0

    points = tuple(
        BoltzmannPoint(
            element=element,
            ion_stage=ion_stage,
            wavelength_nm=p["wl"],
            ek_ev=p["ek"],
            ei_ev=p["ei"],
            gk=p["gk"],
            aki=p["aki"],
            net_intensity=p["net_area"],
            y_value=p["y"],
            residual=float(res),
        )
        for p, res in zip(raw_points, residuals, strict=False)
    )

    if m >= 0:
        return BoltzmannFitResult(
            temperature_k=0.0,
            temperature_uncertainty_k=0.0,
            r_squared=max(r_squared, 0.0),
            slope=m,
            intercept=c,
            slope_stderr=slope_stderr,
            element=element,
            ion_stage=ion_stage,
            points=points,
            confidence_score=0.0,
            status="unphysical_slope",
            status_message=(
                f"Fitted slope m={m:.4f} is nonnegative, indicating self-absorption, "
                "strong blending, or non-LTE condition"
            ),
        )

    t_est = float(-1.0 / (BOLTZMANN_EV_PER_K * m))
    t_unc = float(t_est * (slope_stderr / abs(m))) if abs(m) > 0 else 0.0

    # Composite confidence score [0.0, 1.0]
    r2_clamped = max(r_squared, 0.0)
    n_factor = min(len(points) / 10.0, 1.0)
    spread_factor = min(ek_spread / 2.5, 1.0)
    t_factor = 1.0 if 3500 <= t_est <= 35000 else 0.3
    score_raw = r2_clamped * 0.5 + n_factor * 0.2 + spread_factor * 0.3
    confidence = float(np.clip(score_raw, 0.0, 1.0) * t_factor)

    status = "valid" if (r_squared >= 0.70 and 3000 <= t_est <= 40000) else "weak_fit"
    msg = (
        f"Recovered Te = {t_est:.0f} ± {t_unc:.0f} K (N={len(points)}, R²={r_squared:.3f})"
        if status == "valid"
        else f"Borderline fit: Te = {t_est:.0f} K (R²={r_squared:.3f}, spread={ek_spread:.2f} eV)"
    )

    return BoltzmannFitResult(
        temperature_k=t_est,
        temperature_uncertainty_k=t_unc,
        r_squared=max(r_squared, 0.0),
        slope=m,
        intercept=c,
        slope_stderr=slope_stderr,
        element=element,
        ion_stage=ion_stage,
        points=points,
        confidence_score=confidence,
        status=status,
        status_message=msg,
    )


def compute_stark_density(
    wavelength_nm: Sequence[float] | np.ndarray,
    intensity: Sequence[float] | np.ndarray,
    *,
    instrumental_fwhm_nm: float = 0.10,
    medium: str = "vacuum",
) -> StarkDensityResult:
    """Estimate electron density n_e from Stark broadening of H-alpha.

    Supports 656.46 nm (vacuum) or 656.28 nm (air).
    Voigt deconvolution:
        FWHM_obs^2 ~ FWHM_Stark * FWHM_obs + FWHM_inst^2
        FWHM_Stark ~ (FWHM_obs^2 - FWHM_inst^2) / FWHM_obs
    Stark scaling:
        n_e = 10^17 * (FWHM_Stark / 1.0 nm)^1.5 cm^-3
    """
    wave = np.asarray(wavelength_nm, dtype=float)
    flux = np.asarray(intensity, dtype=float)

    if medium.lower() == "air":
        wave = air_to_vacuum(wave)

    # Vacuum center of H-alpha: 656.46 nm
    h_alpha_vac = 656.46
    if not (wave[0] + 3.0 <= h_alpha_vac <= wave[-1] - 3.0):
        return StarkDensityResult(
            electron_density_cm3=None,
            line_name="H-alpha",
            center_wavelength_nm=h_alpha_vac,
            observed_fwhm_nm=0.0,
            instrumental_fwhm_nm=instrumental_fwhm_nm,
            stark_fwhm_nm=0.0,
            status="not_in_range",
            status_message="H-alpha (656.46 nm vac) is outside the spectral coverage",
        )

    # Core window within +/- 0.45 nm of H-alpha to avoid nearby lines of other elements
    core_mask = np.abs(wave - h_alpha_vac) <= 0.45
    if not np.any(core_mask):
        return StarkDensityResult(
            electron_density_cm3=None,
            line_name="H-alpha",
            center_wavelength_nm=h_alpha_vac,
            observed_fwhm_nm=0.0,
            instrumental_fwhm_nm=instrumental_fwhm_nm,
            stark_fwhm_nm=0.0,
            status="below_detection_limit",
            status_message="No data points in H-alpha line core",
        )

    # Baseline from outer wings [h - 4.0, h - 2.0] and [h + 2.0, h + 4.0]
    side_mask = ((wave >= h_alpha_vac - 4.0) & (wave <= h_alpha_vac - 2.0)) | (
        (wave >= h_alpha_vac + 2.0) & (wave <= h_alpha_vac + 4.0)
    )
    if not np.any(side_mask):
        side_mask = np.abs(wave - h_alpha_vac) >= 1.5

    if np.any(side_mask):
        base = float(np.median(flux[side_mask]))
        mad = float(np.median(np.abs(flux[side_mask] - base)))
    else:
        base = float(np.min(flux[core_mask]))
        mad = 1e-12
    noise = max(mad * 1.4826, 1e-12)

    core_indices = np.where(core_mask)[0]
    core_peak_local = int(np.argmax(flux[core_mask]))
    peak_idx = int(core_indices[core_peak_local])
    peak_wl = float(wave[peak_idx])
    net_peak = float(flux[peak_idx] - base)

    if net_peak <= 0 or (net_peak / noise) < 2.5:
        return StarkDensityResult(
            electron_density_cm3=None,
            line_name="H-alpha",
            center_wavelength_nm=peak_wl,
            observed_fwhm_nm=0.0,
            instrumental_fwhm_nm=instrumental_fwhm_nm,
            stark_fwhm_nm=0.0,
            status="below_detection_limit",
            status_message="No significant H-alpha emission peak above background",
        )

    half_max = float(base + net_peak / 2.0)

    # Step left and right from peak_idx to locate half-maximum crossing
    left_side = np.where((wave < peak_wl) & (flux <= half_max))[0]
    right_side = np.where((wave > peak_wl) & (flux <= half_max))[0]

    if len(left_side) == 0 or len(right_side) == 0:
        return StarkDensityResult(
            electron_density_cm3=None,
            line_name="H-alpha",
            center_wavelength_nm=peak_wl,
            observed_fwhm_nm=0.0,
            instrumental_fwhm_nm=instrumental_fwhm_nm,
            stark_fwhm_nm=0.0,
            status="below_detection_limit",
            status_message="Cannot isolate half-maximum wings of H-alpha",
        )

    left_wl = float(wave[left_side[-1]])
    right_wl = float(wave[right_side[0]])
    obs_fwhm = float(max(right_wl - left_wl, 0.001))

    if obs_fwhm <= instrumental_fwhm_nm:
        return StarkDensityResult(
            electron_density_cm3=None,
            line_name="H-alpha",
            center_wavelength_nm=peak_wl,
            observed_fwhm_nm=obs_fwhm,
            instrumental_fwhm_nm=instrumental_fwhm_nm,
            stark_fwhm_nm=0.0,
            status="below_detection_limit",
            status_message=(
                f"Observed FWHM ({obs_fwhm:.3f} nm) is within instrumental width "
                f"({instrumental_fwhm_nm:.3f} nm); Stark broadening is unresolved"
            ),
        )

    # Voigt deconvolution: Olivero-Longbothum approximation
    stark_fwhm = float(max((obs_fwhm**2 - instrumental_fwhm_nm**2) / obs_fwhm, 0.001))
    ne_est = float(1e17 * (stark_fwhm / 1.0) ** 1.5)

    return StarkDensityResult(
        electron_density_cm3=ne_est,
        line_name="H-alpha",
        center_wavelength_nm=peak_wl,
        observed_fwhm_nm=obs_fwhm,
        instrumental_fwhm_nm=instrumental_fwhm_nm,
        stark_fwhm_nm=stark_fwhm,
        status="detected",
        status_message=(f"Stark FWHM = {stark_fwhm:.3f} nm -> ne = {ne_est:.2e} cm^-3"),
    )


def check_mcwhirter_criterion(
    temperature_k: float,
    electron_density_cm3: float | None = None,
    *,
    energy_gap_ev: float = 2.5,
) -> McWhirterResult:
    """Evaluate McWhirter criterion for LTE condition.

    Condition:
        n_e >= 1.6e12 * T_e^0.5 * (Delta E)^3  [cm^-3]
    """
    if temperature_k <= 0 or not math.isfinite(temperature_k):
        return McWhirterResult(
            criterion_satisfied=None,
            min_electron_density_cm3=0.0,
            temperature_k=0.0,
            energy_gap_ev=energy_gap_ev,
            status="invalid_temperature",
            notes="Requires valid plasma temperature Te > 0 K",
        )

    min_ne = float(1.6e12 * math.sqrt(temperature_k) * (energy_gap_ev**3))

    if electron_density_cm3 is None or electron_density_cm3 <= 0:
        return McWhirterResult(
            criterion_satisfied=None,
            min_electron_density_cm3=min_ne,
            temperature_k=temperature_k,
            energy_gap_ev=energy_gap_ev,
            status="unverified",
            notes=(
                f"LTE requires n_e >= {min_ne:.2e} cm^-3 (ΔE = {energy_gap_ev:.1f} eV); "
                "electron density is not measured"
            ),
        )

    satisfied = bool(electron_density_cm3 >= min_ne)
    status = "satisfied" if satisfied else "not_satisfied"
    notes = (
        f"LTE verified: measured n_e ({electron_density_cm3:.2e} cm^-3) >= "
        f"threshold ({min_ne:.2e} cm^-3)"
        if satisfied
        else f"LTE borderline or violated: measured n_e ({electron_density_cm3:.2e} cm^-3) < "
        f"threshold ({min_ne:.2e} cm^-3)"
    )

    return McWhirterResult(
        criterion_satisfied=satisfied,
        min_electron_density_cm3=min_ne,
        temperature_k=temperature_k,
        energy_gap_ev=energy_gap_ev,
        status=status,
        notes=notes,
    )


def analyze_plasma_diagnostics(
    wavelength_nm: Sequence[float] | np.ndarray,
    intensity: Sequence[float] | np.ndarray,
    *,
    element: str = "auto",
    instrumental_fwhm_nm: float = 0.10,
    medium: str = "vacuum",
    energy_gap_ev: float = 2.5,
) -> PlasmaDiagnosticsResult:
    """Perform full LTE plasma diagnostics (Boltzmann plot, Stark density, McWhirter criterion)."""
    wave = np.asarray(wavelength_nm, dtype=float)
    flux = np.asarray(intensity, dtype=float)

    if medium.lower() == "air":
        wave = air_to_vacuum(wave)

    boltzmann: BoltzmannFitResult | None = None
    if element == "auto":
        candidates = ("Fe", "Ti", "Mg", "Ca")
        best_fit: BoltzmannFitResult | None = None
        for el in candidates:
            fit = compute_boltzmann_fit(wave, flux, element=el, medium="vacuum")
            if fit.status == "valid" and (
                best_fit is None or fit.confidence_score > best_fit.confidence_score
            ):
                best_fit = fit
        boltzmann = best_fit or compute_boltzmann_fit(wave, flux, element="Fe", medium="vacuum")
    else:
        boltzmann = compute_boltzmann_fit(wave, flux, element=element, medium="vacuum")

    stark = compute_stark_density(
        wave, flux, instrumental_fwhm_nm=instrumental_fwhm_nm, medium="vacuum"
    )

    t_val = boltzmann.temperature_k if (boltzmann and boltzmann.status == "valid") else 0.0
    ne_val = stark.electron_density_cm3 if (stark and stark.status == "detected") else None

    mcwhirter = (
        check_mcwhirter_criterion(t_val, ne_val, energy_gap_ev=energy_gap_ev) if t_val > 0 else None
    )

    summary = {
        "temperature_k": t_val,
        "temperature_uncertainty_k": boltzmann.temperature_uncertainty_k if boltzmann else 0.0,
        "electron_density_cm3": ne_val,
        "element": boltzmann.element if boltzmann else element,
        "boltzmann_r_squared": boltzmann.r_squared if boltzmann else 0.0,
        "boltzmann_points_count": len(boltzmann.points) if boltzmann else 0,
        "lte_valid": mcwhirter.criterion_satisfied if mcwhirter else None,
        "diagnostics_ready": bool(t_val > 0),
    }

    return PlasmaDiagnosticsResult(
        boltzmann=boltzmann,
        stark=stark,
        mcwhirter=mcwhirter,
        summary=summary,
    )
