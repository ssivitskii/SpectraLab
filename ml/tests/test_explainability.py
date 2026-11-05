from __future__ import annotations

import numpy as np
from spectralab_ml.models.explainability import (
    analyze_spectral_explainability,
    find_spectral_blends,
)


def test_analyze_spectral_explainability_detects_lines() -> None:
    """Verify explainability detects known analytical lines and calculates net SBR."""
    # Create grid spanning UV-Vis
    wave = np.linspace(250.0, 700.0, 25000)
    flux = np.full_like(wave, 10.0)  # baseline = 10.0

    # Add synthetic peaks for Fe and Mg
    # Fe I 373.593 nm
    flux[np.abs(wave - 373.593) < 0.04] += 50.0
    # Mg I 285.296 nm
    flux[np.abs(wave - 285.296) < 0.04] += 40.0

    report = analyze_spectral_explainability(wave, flux, medium="vacuum")

    assert "Fe" in report.detected_elements
    assert "Mg" in report.detected_elements
    assert len(report.lines) > 0

    fe_line = next(
        line
        for line in report.lines
        if line.element == "Fe" and abs(line.wavelength_vacuum_nm - 373.593) < 0.1
    )
    assert fe_line.detected is True
    assert fe_line.signal_to_background_ratio > 4.0
    assert fe_line.net_intensity > 30.0
    assert fe_line.target_oxide == "FeOT"

    as_dict = report.to_dict()
    assert "lines" in as_dict
    assert "detected_elements" in as_dict
    assert "summary" in as_dict


def test_find_spectral_blends() -> None:
    """Verify spectral blends are flagged when lines are closer than blend_window_nm."""
    # Test catalog with two artificially close lines
    test_lines = [
        {"element": "A", "ion_stage": 1, "wl_vac": 400.00},
        {"element": "B", "ion_stage": 1, "wl_vac": 400.08},  # 0.08 nm apart (< 0.15 nm)
        {"element": "C", "ion_stage": 1, "wl_vac": 400.80},  # 0.80 nm apart (> 0.15 nm)
    ]
    blends = find_spectral_blends(400.00, test_lines, blend_window_nm=0.15)
    assert "B 1" in blends
    assert "C 1" not in blends


def test_explainability_air_medium() -> None:
    """Verify air wavelength conversion shifts coordinate grid correctly."""
    from spectralab_ml.data.wavelengths import vacuum_to_air

    wave_vac = np.linspace(350.0, 500.0, 10000)
    wave_air = vacuum_to_air(wave_vac)

    flux = np.full_like(wave_air, 5.0)
    # Inject peak at Fe I 373.593 vacuum, which in air is ~373.49 nm
    target_vac = 373.593
    target_air = float(vacuum_to_air(np.array([target_vac]))[0])
    flux[np.abs(wave_air - target_air) < 0.04] += 30.0

    report = analyze_spectral_explainability(wave_air, flux, medium="air")
    fe_line = next(
        line
        for line in report.lines
        if line.element == "Fe" and abs(line.wavelength_vacuum_nm - target_vac) < 0.1
    )
    assert fe_line.detected is True
    assert fe_line.signal_to_background_ratio > 3.0
