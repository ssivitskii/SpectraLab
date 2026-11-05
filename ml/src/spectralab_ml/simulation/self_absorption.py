"""Physical self-absorption correction (Cowan-Dieke model) and Calibration-Free LIBS.

Quantifies optical thickness tau_0 from doublets, restores unabsorbed line intensities,
and computes calibration-free elemental concentrations from plasma Saha-Boltzmann LTE invariants.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any

import numpy as np
import scipy.optimize

from spectralab_ml.config import repository_root
from spectralab_ml.data.plasma_reference import (
    ATOMIC_MASS_U,
    BOLTZMANN_EV_PER_K,
    PlasmaReference,
)
from spectralab_ml.data.wavelengths import air_to_vacuum
from spectralab_ml.simulation.diagnostics import get_default_plasma_reference


@dataclass(frozen=True)
class DoubletSpec:
    name: str
    element: str
    ion_stage: int
    line1_wl_vac: float
    line1_aki: float
    line1_gk: float
    line2_wl_vac: float
    line2_aki: float
    line2_gk: float

    @property
    def r_theo(self) -> float:
        return float((self.line1_aki * self.line1_gk) / (self.line2_aki * self.line2_gk))


CURATED_DOUBLETS: tuple[DoubletSpec, ...] = (
    DoubletSpec(
        name="Mg II 279.64 / 280.35",
        element="Mg",
        ion_stage=2,
        line1_wl_vac=279.635,
        line1_aki=2.60e8,
        line1_gk=4.0,
        line2_wl_vac=280.353,
        line2_aki=2.57e8,
        line2_gk=2.0,
    ),
    DoubletSpec(
        name="Ca II 393.48 / 396.96",
        element="Ca",
        ion_stage=2,
        line1_wl_vac=393.477,
        line1_aki=1.47e8,
        line1_gk=4.0,
        line2_wl_vac=396.959,
        line2_aki=1.40e8,
        line2_gk=2.0,
    ),
    DoubletSpec(
        name="Na I 589.16 / 589.76",
        element="Na",
        ion_stage=1,
        line1_wl_vac=589.158,
        line1_aki=6.16e7,
        line1_gk=4.0,
        line2_wl_vac=589.756,
        line2_aki=6.14e7,
        line2_gk=2.0,
    ),
)


def cowan_dieke_sa_factor(tau: float) -> float:
    """Compute self-absorption factor SA = (1 - exp(-tau)) / tau."""
    if tau <= 1e-6:
        return 1.0 - 0.5 * tau + (tau * tau) / 6.0
    return float((1.0 - math.exp(-tau)) / tau)


def solve_cowan_dieke_tau(r_obs: float, r_theo: float) -> float:
    """Solve for line 1 optical depth tau_0 from observed doublet ratio r_obs <= r_theo."""
    if not math.isfinite(r_obs) or r_obs <= 0:
        return 0.0
    if r_obs >= r_theo - 1e-4:
        return 0.0

    # f(tau) = (1 - exp(-tau)) / (1 - exp(-tau / r_theo)) - r_obs
    def objective(tau: float) -> float:
        num = 1.0 - math.exp(-tau)
        den = 1.0 - math.exp(-tau / r_theo)
        if den <= 1e-12:
            return r_theo - r_obs
        return (num / den) - r_obs

    val_at_0 = r_theo - r_obs
    val_at_50 = objective(50.0)

    if val_at_0 * val_at_50 > 0:
        return 50.0 if r_obs < 1.0 else 0.0

    try:
        sol = scipy.optimize.brentq(objective, 1e-4, 50.0, xtol=1e-5)
        return float(sol)
    except (ValueError, RuntimeError):
        return 0.0


@dataclass(frozen=True)
class DoubletAbsorptionResult:
    name: str
    element: str
    ion_stage: int
    status: str
    observed_ratio: float
    theoretical_ratio: float
    tau_line1: float
    tau_line2: float
    sa_factor_line1: float
    sa_factor_line2: float
    line1_wl_nm: float
    line2_wl_nm: float
    line1_i_obs: float
    line2_i_obs: float
    line1_i_corrected: float
    line2_i_corrected: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SelfAbsorptionReport:
    doublets: tuple[DoubletAbsorptionResult, ...]
    mean_self_absorption_factor: float
    summary: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "doublets": [d.to_dict() for d in self.doublets],
            "mean_self_absorption_factor": self.mean_self_absorption_factor,
            "summary": self.summary,
        }


def _extract_net_peak(
    wave: np.ndarray,
    flux: np.ndarray,
    target_wl: float,
    half_width_nm: float = 0.08,
) -> tuple[float, float, bool]:
    mask = np.abs(wave - target_wl) <= half_width_nm
    if not np.any(mask) or np.sum(mask) < 2:
        return 0.0, 0.0, False

    bg_mask = (np.abs(wave - target_wl) >= 0.16) & (np.abs(wave - target_wl) <= 0.30)
    bg = float(np.median(flux[bg_mask])) if np.any(bg_mask) else float(np.min(flux[mask]))
    bg = max(bg, 1e-9)

    peak = float(np.max(flux[mask]))
    net = max(peak - bg, 0.0)
    detected = (peak / bg >= 1.25) and net > 0.05 * bg
    return net, peak, detected


def analyze_self_absorption(
    wavelength_nm: np.ndarray,
    intensity: np.ndarray,
    *,
    medium: str = "vacuum",
) -> SelfAbsorptionReport:
    """Analyze all reference spectroscopic doublets and quantify optical depth tau_0 and SA."""
    wave = np.asarray(wavelength_nm, dtype=float)
    flux = np.asarray(intensity, dtype=float)

    if medium.lower() == "air":
        wave = air_to_vacuum(wave)

    results: list[DoubletAbsorptionResult] = []
    sa_factors: list[float] = []

    for d in CURATED_DOUBLETS:
        # Check wavelength coverage
        if (
            d.line1_wl_vac < wave[0] + 0.3
            or d.line1_wl_vac > wave[-1] - 0.3
            or d.line2_wl_vac < wave[0] + 0.3
            or d.line2_wl_vac > wave[-1] - 0.3
        ):
            results.append(
                DoubletAbsorptionResult(
                    name=d.name,
                    element=d.element,
                    ion_stage=d.ion_stage,
                    status="not_covered",
                    observed_ratio=0.0,
                    theoretical_ratio=d.r_theo,
                    tau_line1=0.0,
                    tau_line2=0.0,
                    sa_factor_line1=1.0,
                    sa_factor_line2=1.0,
                    line1_wl_nm=d.line1_wl_vac,
                    line2_wl_nm=d.line2_wl_vac,
                    line1_i_obs=0.0,
                    line2_i_obs=0.0,
                    line1_i_corrected=0.0,
                    line2_i_corrected=0.0,
                )
            )
            continue

        net1, _peak1, det1 = _extract_net_peak(wave, flux, d.line1_wl_vac)
        net2, _peak2, det2 = _extract_net_peak(wave, flux, d.line2_wl_vac)

        if not (det1 and det2) or net1 <= 0 or net2 <= 0:
            results.append(
                DoubletAbsorptionResult(
                    name=d.name,
                    element=d.element,
                    ion_stage=d.ion_stage,
                    status="not_detected",
                    observed_ratio=0.0,
                    theoretical_ratio=d.r_theo,
                    tau_line1=0.0,
                    tau_line2=0.0,
                    sa_factor_line1=1.0,
                    sa_factor_line2=1.0,
                    line1_wl_nm=d.line1_wl_vac,
                    line2_wl_nm=d.line2_wl_vac,
                    line1_i_obs=net1,
                    line2_i_obs=net2,
                    line1_i_corrected=net1,
                    line2_i_corrected=net2,
                )
            )
            continue

        r_obs = net1 / net2
        r_theo = d.r_theo
        tau1 = solve_cowan_dieke_tau(r_obs, r_theo)
        tau2 = tau1 / r_theo if r_theo > 0 else 0.0

        sa1 = cowan_dieke_sa_factor(tau1)
        sa2 = cowan_dieke_sa_factor(tau2)

        sa_factors.append(sa1)

        i1_corr = net1 / sa1 if sa1 > 0 else net1
        i2_corr = net2 / sa2 if sa2 > 0 else net2

        status = "absorbed" if tau1 >= 0.2 else "thin"

        results.append(
            DoubletAbsorptionResult(
                name=d.name,
                element=d.element,
                ion_stage=d.ion_stage,
                status=status,
                observed_ratio=r_obs,
                theoretical_ratio=r_theo,
                tau_line1=tau1,
                tau_line2=tau2,
                sa_factor_line1=sa1,
                sa_factor_line2=sa2,
                line1_wl_nm=d.line1_wl_vac,
                line2_wl_nm=d.line2_wl_vac,
                line1_i_obs=net1,
                line2_i_obs=net2,
                line1_i_corrected=i1_corr,
                line2_i_corrected=i2_corr,
            )
        )

    mean_sa = float(np.mean(sa_factors)) if sa_factors else 1.0
    detected_count = sum(1 for r in results if r.status in ("absorbed", "thin"))
    absorbed_count = sum(1 for r in results if r.status == "absorbed")

    summary = (
        f"Анализ {len(results)} реперных дублетов: {detected_count} обнаружено, "
        f"{absorbed_count} с оптической толщиной tau_0 >= 0.2 (средний SA={mean_sa:.1%})."
    )

    return SelfAbsorptionReport(
        doublets=tuple(results),
        mean_self_absorption_factor=mean_sa,
        summary=summary,
    )


@dataclass(frozen=True)
class CFLibsElementResult:
    element: str
    atomic_fraction: float
    weight_fraction_wt: float
    intercept_q: float
    lines_used_count: int
    uncertainty_wt: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CFLibsReport:
    temperature_k: float
    elements: tuple[CFLibsElementResult, ...]
    normalization_factor_f: float
    summary: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "temperature_k": self.temperature_k,
            "elements": [e.to_dict() for e in self.elements],
            "normalization_factor_f": self.normalization_factor_f,
            "summary": self.summary,
        }


# Analytical lines for multi-element CF-LIBS inversion (NIST vac nm, Aki, Ek, gk)
CF_LIBS_LINES: dict[str, tuple[tuple[float, float, float, float], ...]] = {
    "Fe": (
        (373.593, 9.01e7, 4.18, 11.0),
        (374.693, 9.15e6, 3.42, 9.0),
        (382.151, 6.68e7, 4.14, 9.0),
        (404.696, 8.62e7, 4.55, 9.0),
    ),
    "Mg": (
        (285.296, 4.91e8, 4.35, 3.0),
        (383.339, 1.62e8, 5.95, 7.0),
        (517.413, 3.37e7, 5.11, 3.0),
        (518.505, 5.61e7, 5.11, 5.0),
    ),
    "Ca": (
        (422.792, 2.18e8, 2.93, 3.0),
        (443.621, 1.50e7, 4.68, 3.0),
        (445.603, 4.00e7, 4.68, 5.0),
    ),
    "Ti": (
        (334.941, 5.40e7, 3.75, 7.0),
        (375.286, 4.80e7, 3.32, 5.0),
        (429.696, 3.50e7, 3.73, 5.0),
    ),
    "Si": (
        (288.238, 2.17e8, 5.08, 3.0),
        (390.663, 1.33e7, 5.08, 3.0),
    ),
    "Al": (
        (394.512, 4.93e7, 3.14, 2.0),
        (396.264, 9.80e7, 3.14, 2.0),
    ),
    "Na": (
        (589.158, 6.16e7, 2.10, 4.0),
        (589.756, 6.14e7, 2.10, 2.0),
    ),
}


def compute_cf_libs_inversion(
    wavelength_nm: np.ndarray,
    intensity: np.ndarray,
    temperature_k: float,
    *,
    medium: str = "vacuum",
    plasma_ref: PlasmaReference | None = None,
) -> CFLibsReport:
    """Perform multi-element calibration-free LIBS inversion using closure sum sum(C_i) = 1.0."""
    wave = np.asarray(wavelength_nm, dtype=float)
    flux = np.asarray(intensity, dtype=float)

    if medium.lower() == "air":
        wave = air_to_vacuum(wave)

    if plasma_ref is None:
        plasma_ref = get_default_plasma_reference(repository_root())

    temp = max(temperature_k, 3000.0)
    kb_t = BOLTZMANN_EV_PER_K * temp

    elem_estimates: dict[str, list[float]] = {}

    for elem, lines in CF_LIBS_LINES.items():
        try:
            u_t = plasma_ref.partition_function(elem, 1, temp)
        except (KeyError, ValueError):
            u_t = 1.0
        u_t = max(u_t, 1e-4)

        estimates = []
        for wl_vac, aki, ek, gk in lines:
            if wl_vac < wave[0] + 0.3 or wl_vac > wave[-1] - 0.3:
                continue
            net, _peak, detected = _extract_net_peak(wave, flux, wl_vac)
            if not detected or net <= 0:
                continue

            # y = ln( (I * U(T)) / (A * g) ) + Ek / (k_B * T) = ln(F * C)
            val = (net * u_t) / (aki * gk)
            if val <= 0:
                continue
            y_i = math.log(val) + (ek / kb_t)
            estimates.append(y_i)

        if estimates:
            elem_estimates[elem] = estimates

    if not elem_estimates:
        return CFLibsReport(
            temperature_k=temp,
            elements=(),
            normalization_factor_f=0.0,
            summary="Не удалось выделить аналитические линии для CF-LIBS инверсии.",
        )

    # Median intercept q_alpha for each element
    q_vals: dict[str, float] = {}
    q_stds: dict[str, float] = {}
    for elem, est_list in elem_estimates.items():
        q_vals[elem] = float(np.median(est_list))
        q_stds[elem] = float(np.std(est_list)) if len(est_list) > 1 else 0.15

    # Max shift normalization for numeric stability
    max_q = max(q_vals.values())
    exp_q = {elem: math.exp(q - max_q) for elem, q in q_vals.items()}
    sum_exp = sum(exp_q.values())

    # Atomic / molar fractions C_alpha
    atomic_fractions = {elem: exp_q[elem] / sum_exp for elem in exp_q}

    # Convert to weight percent wt% using atomic mass
    mass_factors = {
        elem: atomic_fractions[elem] * ATOMIC_MASS_U.get(elem, 30.0) for elem in atomic_fractions
    }
    total_mass_factor = sum(mass_factors.values())
    weight_fractions = {
        elem: float((mass_factors[elem] / total_mass_factor) * 100.0) for elem in mass_factors
    }

    elem_results: list[CFLibsElementResult] = []
    for elem in sorted(q_vals.keys()):
        # Uncertainty approximation from line scatter
        std_q = q_stds[elem]
        unc_wt = weight_fractions[elem] * math.tanh(std_q)
        elem_results.append(
            CFLibsElementResult(
                element=elem,
                atomic_fraction=float(atomic_fractions[elem]),
                weight_fraction_wt=float(weight_fractions[elem]),
                intercept_q=float(q_vals[elem]),
                lines_used_count=len(elem_estimates[elem]),
                uncertainty_wt=float(unc_wt),
            )
        )

    # Sort by weight percent descending
    elem_results.sort(key=lambda x: x.weight_fraction_wt, reverse=True)

    summary_parts = [f"{e.element}: {e.weight_fraction_wt:.1f} wt%" for e in elem_results[:4]]
    summary = (
        f"CF-LIBS инверсия при T={temp:.0f} K: "
        + ", ".join(summary_parts)
        + f" (всего элементов: {len(elem_results)})."
    )

    return CFLibsReport(
        temperature_k=temp,
        elements=tuple(elem_results),
        normalization_factor_f=float(sum_exp * math.exp(max_q)),
        summary=summary,
    )
