from __future__ import annotations

import numpy as np
import pytest
from spectralab_ml.config import repository_root
from spectralab_ml.data.plasma_reference import PlasmaReference
from spectralab_ml.simulation.diagnostics import (
    analyze_plasma_diagnostics,
    check_mcwhirter_criterion,
    compute_boltzmann_fit,
    compute_stark_density,
    get_default_plasma_reference,
)
from spectralab_ml.simulation.plasma import (
    PlasmaConditions,
    PlasmaModel,
    PlasmaZone,
)


@pytest.fixture(scope="module")
def plasma_ref() -> PlasmaReference:
    return get_default_plasma_reference(repository_root())


def test_boltzmann_temperature_recovery(plasma_ref: PlasmaReference) -> None:
    """Verify Boltzmann plot temperature recovery within 15% on synthetic LTE spectra."""
    model = PlasmaModel(plasma_ref, 280.0, 530.0, fine_step_nm=0.01)

    for true_temp in (8000.0, 10000.0, 12000.0):
        cond = PlasmaConditions(
            zones=(PlasmaZone(temperature_k=true_temp, electron_density_cm3=5e16, weight=1.0),),
            heavy_density_cm3=5e16,
            path_length_cm=0.01,
            stark_fwhm_nm=0.02,
            continuum_factor=0.01,
        )
        fractions = {"Fe": 0.15, "Si": 0.45, "O": 0.40}
        radiance = model.radiance(fractions, cond)
        grid = model.grid_nm

        fit = compute_boltzmann_fit(grid, radiance, element="Fe", medium="vacuum")

        assert fit.status == "valid", f"Fit failed: {fit.status_message}"
        assert fit.r_squared >= 0.85, f"Low R^2={fit.r_squared} for T={true_temp}"
        assert len(fit.points) >= 8, f"Too few points: {len(fit.points)}"

        # Temperature relative error <= 15%
        rel_error = abs(fit.temperature_k - true_temp) / true_temp
        err_msg = f"T_true={true_temp}, T_est={fit.temperature_k}, err={rel_error:.1%}"
        assert rel_error <= 0.15, err_msg


def test_stark_electron_density_recovery(plasma_ref: PlasmaReference) -> None:
    """Verify Stark broadening electron density estimation from H-alpha."""
    model = PlasmaModel(plasma_ref, 645.0, 665.0, fine_step_nm=0.01)
    true_ne = 1e17
    cond = PlasmaConditions(
        zones=(PlasmaZone(temperature_k=11000.0, electron_density_cm3=true_ne, weight=1.0),),
        heavy_density_cm3=true_ne,
        path_length_cm=0.02,
        stark_fwhm_nm=0.05,
        hydrogen_stark_fwhm_nm=1.0,
        continuum_factor=0.01,
    )
    radiance = model.radiance({"H": 0.30, "O": 0.70}, cond)
    grid = model.grid_nm

    res = compute_stark_density(grid, radiance, instrumental_fwhm_nm=0.05, medium="vacuum")
    assert res.status == "detected"
    assert res.electron_density_cm3 is not None
    # Order of magnitude check: within factor of 3
    ratio = res.electron_density_cm3 / true_ne
    assert 0.3 <= ratio <= 3.0, f"n_e ratio {ratio:.2f} out of bounds"


def test_mcwhirter_criterion() -> None:
    """Verify McWhirter threshold calculation and status flags."""
    # Te = 10000 K, dE = 2.5 eV -> min_ne ~ 2.5e15 cm^-3
    res_sat = check_mcwhirter_criterion(10000.0, electron_density_cm3=1e17)
    assert res_sat.criterion_satisfied is True
    assert res_sat.status == "satisfied"
    assert res_sat.min_electron_density_cm3 == pytest.approx(2.5e15, rel=0.1)

    res_unsat = check_mcwhirter_criterion(10000.0, electron_density_cm3=1e14)
    assert res_unsat.criterion_satisfied is False
    assert res_unsat.status == "not_satisfied"

    res_no_ne = check_mcwhirter_criterion(10000.0, electron_density_cm3=None)
    assert res_no_ne.criterion_satisfied is None
    assert res_no_ne.status == "unverified"

    res_invalid_t = check_mcwhirter_criterion(0.0)
    assert res_invalid_t.status == "invalid_temperature"


def test_analyze_plasma_diagnostics_end_to_end(plasma_ref: PlasmaReference) -> None:
    """Verify complete plasma analysis pipeline on a multi-element spectrum."""
    model = PlasmaModel(plasma_ref, 300.0, 670.0, fine_step_nm=0.02)
    cond = PlasmaConditions(
        zones=(PlasmaZone(temperature_k=10500.0, electron_density_cm3=8e16, weight=1.0),),
        heavy_density_cm3=8e16,
        path_length_cm=0.015,
        stark_fwhm_nm=0.03,
        hydrogen_stark_fwhm_nm=1.0,
        continuum_factor=0.02,
    )
    radiance = model.radiance({"Fe": 0.15, "H": 0.20, "Si": 0.25, "O": 0.40}, cond)
    grid = model.grid_nm

    diag = analyze_plasma_diagnostics(grid, radiance, element="auto", instrumental_fwhm_nm=0.05)

    assert diag.boltzmann is not None
    assert diag.boltzmann.status == "valid"
    assert diag.stark is not None
    assert diag.stark.status == "detected"
    assert diag.mcwhirter is not None
    assert diag.mcwhirter.criterion_satisfied is True
    assert diag.summary["diagnostics_ready"] is True
    assert diag.summary["temperature_k"] > 8000.0

    # Serialization test
    as_dict = diag.to_dict()
    assert "boltzmann" in as_dict
    assert "stark" in as_dict
    assert "mcwhirter" in as_dict
    assert len(as_dict["boltzmann"]["points"]) > 0


def test_plasma_diagnostics_graceful_on_noise() -> None:
    """Verify diagnostics handles pure noise or blank data without throwing exceptions."""
    grid = np.linspace(300.0, 500.0, 2000)
    flux = np.random.default_rng(42).normal(0.1, 0.01, size=len(grid))

    fit = compute_boltzmann_fit(grid, flux, element="Fe")
    assert fit.status in ("insufficient_lines", "weak_fit", "unphysical_slope")

    diag = analyze_plasma_diagnostics(grid, flux, element="auto")
    assert diag.summary["diagnostics_ready"] is False
