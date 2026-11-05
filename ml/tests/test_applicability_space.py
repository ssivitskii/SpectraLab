from __future__ import annotations

import numpy as np
from spectralab_ml.models.applicability_space import (
    ApplicabilitySpaceModel,
    get_default_applicability_model,
)
from spectralab_ml.models.quantitative import SPECIES_LINES


def _generate_synthetic_spectrum(
    species_weights: dict[str, float],
) -> tuple[np.ndarray, np.ndarray]:
    wave = np.linspace(250.0, 650.0, 4000)
    flux = np.full_like(wave, 1.0)  # baseline

    for sp, wt in species_weights.items():
        lines = SPECIES_LINES.get(sp, ())
        for j, wl in enumerate(lines):
            amp = wt * (0.8 + 0.2 * j)
            flux += amp * np.exp(-0.5 * ((wave - wl) / 0.05) ** 2)

    return wave, flux


def test_applicability_space_model_initialization():
    model = ApplicabilitySpaceModel(seed=42, n_cal_samples=40)
    assert model.loadings.shape == (24, 2)
    assert len(model.cal_scores) == 40
    assert model.t2_limit_95 > 0
    assert model.t2_limit_99 > model.t2_limit_95
    assert len(model.ellipse_95) == 64
    assert len(model.ellipse_99) == 64
    assert sum(model.var_explained) > 0.3


def test_applicability_space_in_domain_sample():
    model = get_default_applicability_model()
    # Typical basalt composition
    wave, flux = _generate_synthetic_spectrum(
        {"FeOT": 12.0, "MgO": 7.0, "CaO": 10.0, "TiO2": 1.5, "SiO2": 50.0, "Al2O3": 15.0}
    )
    report = model.analyze(wave, flux, medium="vacuum")

    assert report.status in ("in_domain", "near_boundary")
    assert report.t_squared < report.t_squared_limit_99
    assert len(report.calibration_scores) >= 80
    assert len(report.top_contributions) > 0


def test_applicability_space_out_of_domain_sample():
    model = get_default_applicability_model()
    # Create extreme unphysical spike on single Ti line with zero other elements
    wave = np.linspace(250.0, 650.0, 4000)
    flux = np.full_like(wave, 0.1)
    flux[np.abs(wave - 429.696) < 0.05] += 500.0  # Massive Ti spike

    report = model.analyze(wave, flux, medium="vacuum")
    assert report.status == "out_of_domain"
    assert (
        report.t_squared > report.t_squared_limit_95
        or report.q_residual > report.q_residual_limit_95
    )

    # Ti line should be in top contributions
    top_species = [c.species for c in report.top_contributions]
    assert "TiO2" in top_species


def test_applicability_space_air_vs_vacuum():
    model = get_default_applicability_model()
    wave, flux = _generate_synthetic_spectrum({"FeOT": 10.0, "MgO": 5.0})

    report_vac = model.analyze(wave, flux, medium="vacuum")
    report_air = model.analyze(wave, flux, medium="air")

    assert np.isfinite(report_vac.sample_pc1)
    assert np.isfinite(report_air.sample_pc1)
