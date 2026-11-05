"""Spectral Line Attribution and Explainability for LIBS.

Extracts line-level evidence, evaluates Signal-to-Background Ratios (SBR), identifies
spectral blends / element interferences, and attributes peaks to quantitative oxides.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from typing import Any

import numpy as np

from spectralab_ml.data.wavelengths import air_to_vacuum

# Prominent analytical transitions from NIST with spectroscopic properties
LINE_CATALOG: list[dict[str, Any]] = [
    # Fe lines
    {
        "element": "Fe",
        "ion_stage": 1,
        "wl_vac": 373.593,
        "ek_ev": 4.18,
        "aki": 9.01e7,
        "target_oxide": "FeOT",
    },
    {
        "element": "Fe",
        "ion_stage": 1,
        "wl_vac": 374.693,
        "ek_ev": 3.42,
        "aki": 9.15e6,
        "target_oxide": "FeOT",
    },
    {
        "element": "Fe",
        "ion_stage": 1,
        "wl_vac": 382.151,
        "ek_ev": 4.14,
        "aki": 6.68e7,
        "target_oxide": "FeOT",
    },
    {
        "element": "Fe",
        "ion_stage": 1,
        "wl_vac": 404.696,
        "ek_ev": 4.55,
        "aki": 8.62e7,
        "target_oxide": "FeOT",
    },
    {
        "element": "Fe",
        "ion_stage": 1,
        "wl_vac": 426.167,
        "ek_ev": 5.31,
        "aki": 3.99e7,
        "target_oxide": "FeOT",
    },
    {
        "element": "Fe",
        "ion_stage": 1,
        "wl_vac": 438.478,
        "ek_ev": 4.31,
        "aki": 5.0e7,
        "target_oxide": "FeOT",
    },
    # Ti lines
    {
        "element": "Ti",
        "ion_stage": 1,
        "wl_vac": 334.941,
        "ek_ev": 3.75,
        "aki": 5.4e7,
        "target_oxide": "TiO2",
    },
    {
        "element": "Ti",
        "ion_stage": 1,
        "wl_vac": 375.286,
        "ek_ev": 3.32,
        "aki": 4.8e7,
        "target_oxide": "TiO2",
    },
    {
        "element": "Ti",
        "ion_stage": 1,
        "wl_vac": 429.696,
        "ek_ev": 3.73,
        "aki": 3.5e7,
        "target_oxide": "TiO2",
    },
    {
        "element": "Ti",
        "ion_stage": 1,
        "wl_vac": 485.737,
        "ek_ev": 4.41,
        "aki": 2.8e7,
        "target_oxide": "TiO2",
    },
    # Mg lines
    {
        "element": "Mg",
        "ion_stage": 2,
        "wl_vac": 279.635,
        "ek_ev": 4.43,
        "aki": 2.60e8,
        "target_oxide": "MgO",
    },
    {
        "element": "Mg",
        "ion_stage": 2,
        "wl_vac": 280.353,
        "ek_ev": 4.42,
        "aki": 2.57e8,
        "target_oxide": "MgO",
    },
    {
        "element": "Mg",
        "ion_stage": 1,
        "wl_vac": 285.296,
        "ek_ev": 4.35,
        "aki": 4.91e8,
        "target_oxide": "MgO",
    },
    {
        "element": "Mg",
        "ion_stage": 1,
        "wl_vac": 383.339,
        "ek_ev": 5.95,
        "aki": 1.62e8,
        "target_oxide": "MgO",
    },
    {
        "element": "Mg",
        "ion_stage": 1,
        "wl_vac": 517.413,
        "ek_ev": 5.11,
        "aki": 3.37e7,
        "target_oxide": "MgO",
    },
    {
        "element": "Mg",
        "ion_stage": 1,
        "wl_vac": 518.505,
        "ek_ev": 5.11,
        "aki": 5.61e7,
        "target_oxide": "MgO",
    },
    # Ca lines
    {
        "element": "Ca",
        "ion_stage": 2,
        "wl_vac": 393.477,
        "ek_ev": 3.15,
        "aki": 1.47e8,
        "target_oxide": "CaO",
    },
    {
        "element": "Ca",
        "ion_stage": 2,
        "wl_vac": 396.959,
        "ek_ev": 3.12,
        "aki": 1.40e8,
        "target_oxide": "CaO",
    },
    {
        "element": "Ca",
        "ion_stage": 1,
        "wl_vac": 422.792,
        "ek_ev": 2.93,
        "aki": 2.18e8,
        "target_oxide": "CaO",
    },
    {
        "element": "Ca",
        "ion_stage": 1,
        "wl_vac": 443.621,
        "ek_ev": 4.68,
        "aki": 1.5e7,
        "target_oxide": "CaO",
    },
    {
        "element": "Ca",
        "ion_stage": 1,
        "wl_vac": 445.603,
        "ek_ev": 4.68,
        "aki": 4.0e7,
        "target_oxide": "CaO",
    },
    # Si lines
    {
        "element": "Si",
        "ion_stage": 1,
        "wl_vac": 288.238,
        "ek_ev": 5.08,
        "aki": 2.17e8,
        "target_oxide": "SiO2",
    },
    {
        "element": "Si",
        "ion_stage": 1,
        "wl_vac": 390.663,
        "ek_ev": 5.08,
        "aki": 1.33e7,
        "target_oxide": "SiO2",
    },
    # Al lines
    {
        "element": "Al",
        "ion_stage": 1,
        "wl_vac": 394.512,
        "ek_ev": 3.14,
        "aki": 4.93e7,
        "target_oxide": "Al2O3",
    },
    {
        "element": "Al",
        "ion_stage": 1,
        "wl_vac": 396.264,
        "ek_ev": 3.14,
        "aki": 9.80e7,
        "target_oxide": "Al2O3",
    },
    # Na lines
    {
        "element": "Na",
        "ion_stage": 1,
        "wl_vac": 589.158,
        "ek_ev": 2.10,
        "aki": 6.16e7,
        "target_oxide": "Na2O",
    },
    {
        "element": "Na",
        "ion_stage": 1,
        "wl_vac": 589.756,
        "ek_ev": 2.10,
        "aki": 6.14e7,
        "target_oxide": "Na2O",
    },
    # H lines
    {
        "element": "H",
        "ion_stage": 1,
        "wl_vac": 656.460,
        "ek_ev": 12.09,
        "aki": 4.41e7,
        "target_oxide": "H2O",
    },
]


@dataclass(frozen=True)
class LineEvidence:
    element: str
    ion_stage: int
    line_name: str
    target_oxide: str
    wavelength_vacuum_nm: float
    measured_wavelength_nm: float
    peak_intensity: float
    background_intensity: float
    net_intensity: float
    signal_to_background_ratio: float
    detected: bool
    is_blend: bool
    interfering_elements: tuple[str, ...]
    relative_contribution_score: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ExplainabilityReport:
    lines: tuple[LineEvidence, ...]
    detected_elements: tuple[str, ...]
    spectral_purity_score: float
    blend_count: int
    mean_signal_to_background: float
    summary: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "lines": [line.to_dict() for line in self.lines],
            "detected_elements": list(self.detected_elements),
            "spectral_purity_score": self.spectral_purity_score,
            "blend_count": self.blend_count,
            "mean_signal_to_background": self.mean_signal_to_background,
            "summary": self.summary,
        }


def find_spectral_blends(
    reference_wl: float,
    all_lines: Sequence[dict[str, Any]],
    blend_window_nm: float = 0.15,
) -> tuple[str, ...]:
    """Identify elements with emission lines overlapping within blend_window_nm."""
    interfering = set()
    for item in all_lines:
        dist = abs(item["wl_vac"] - reference_wl)
        if 1e-4 < dist <= blend_window_nm:
            interfering.add(f"{item['element']} {item['ion_stage']}")
    return tuple(sorted(interfering))


def analyze_spectral_explainability(
    wavelength_nm: np.ndarray,
    intensity: np.ndarray,
    *,
    medium: str = "vacuum",
    detection_sbr_threshold: float = 1.35,
    half_width_nm: float = 0.08,
    side_inner_nm: float = 0.16,
    side_outer_nm: float = 0.28,
) -> ExplainabilityReport:
    """Analyze cataloged emission lines, quantify SBR, detect blends, and compute attribution."""
    wave = np.asarray(wavelength_nm, dtype=float)
    flux = np.asarray(intensity, dtype=float)

    if medium == "air":
        wave = air_to_vacuum(wave)

    evidences: list[LineEvidence] = []
    detected_elem_set = set()
    sbr_list: list[float] = []

    for entry in LINE_CATALOG:
        ref_wl = entry["wl_vac"]
        elem = entry["element"]
        stage = entry["ion_stage"]
        oxide = entry["target_oxide"]
        stage_str = "I" if stage == 1 else "II"
        line_name = f"{elem} {stage_str} {ref_wl:.2f}"

        # Check if line falls within spectrometer coverage
        if ref_wl < wave[0] + 0.3 or ref_wl > wave[-1] - 0.3:
            continue

        delta = np.abs(wave - ref_wl)
        core_mask = delta <= half_width_nm
        side_mask = (delta >= side_inner_nm) & (delta <= side_outer_nm)

        if not np.any(core_mask) or np.sum(core_mask) < 2:
            continue

        # Local background: robust median of wings
        if np.any(side_mask):
            bg = float(np.median(flux[side_mask]))
        else:
            bg = float(np.min(flux[core_mask]))

        bg = max(bg, 1e-9)

        # Peak intensity and net intensity
        peak_idx = int(np.argmax(flux[core_mask]))
        measured_wl = float(wave[core_mask][peak_idx])
        peak_val = float(flux[core_mask][peak_idx])
        net_val = max(peak_val - bg, 0.0)

        sbr = peak_val / bg
        sbr_list.append(sbr)

        detected = sbr >= detection_sbr_threshold and net_val > 0.01 * bg
        if detected:
            detected_elem_set.add(elem)

        # Check for nearby interfering lines
        blends = find_spectral_blends(ref_wl, LINE_CATALOG)
        is_blend = len(blends) > 0

        # Relative contribution score: proportional to Aki and net intensity, penalized by blends
        blend_penalty = 0.65 if is_blend else 1.0
        aki_weight = math.log10(max(entry["aki"], 1e5))
        contribution = float(net_val * aki_weight * blend_penalty)

        evidences.append(
            LineEvidence(
                element=elem,
                ion_stage=stage,
                line_name=line_name,
                target_oxide=oxide,
                wavelength_vacuum_nm=ref_wl,
                measured_wavelength_nm=measured_wl,
                peak_intensity=peak_val,
                background_intensity=bg,
                net_intensity=net_val,
                signal_to_background_ratio=sbr,
                detected=detected,
                is_blend=is_blend,
                interfering_elements=blends,
                relative_contribution_score=contribution,
            )
        )

    # Sort lines by wavelength
    evidences.sort(key=lambda x: x.wavelength_vacuum_nm)

    detected_elements = tuple(sorted(detected_elem_set))
    blend_count = sum(1 for e in evidences if e.detected and e.is_blend)
    mean_sbr = float(np.mean(sbr_list)) if sbr_list else 1.0

    # Purity score: ratio of non-blended detected lines to total detected lines
    detected_count = sum(1 for e in evidences if e.detected)
    purity = float((detected_count - blend_count) / detected_count) if detected_count > 0 else 1.0

    summary = (
        f"Обнаружено {len(detected_elements)} элементов по {detected_count} аналитическим линиям "
        f"(наложений/блендов: {blend_count}, чистота спектра: {purity:.0%})."
    )

    return ExplainabilityReport(
        lines=tuple(evidences),
        detected_elements=detected_elements,
        spectral_purity_score=purity,
        blend_count=blend_count,
        mean_signal_to_background=mean_sbr,
        summary=summary,
    )
