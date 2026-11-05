from __future__ import annotations

import math

import numpy as np
from spectralab_ml.simulation.self_absorption import (
    analyze_self_absorption,
    compute_cf_libs_inversion,
    cowan_dieke_sa_factor,
    solve_cowan_dieke_tau,
)


def test_cowan_dieke_math() -> None:
    # tau = 0 => SA = 1.0
    assert abs(cowan_dieke_sa_factor(0.0) - 1.0) < 1e-6
    assert abs(cowan_dieke_sa_factor(1e-7) - 1.0) < 1e-6

    # tau = 1.0 => SA = (1 - exp(-1)) / 1.0 = 0.63212
    sa1 = cowan_dieke_sa_factor(1.0)
    assert abs(sa1 - (1.0 - math.exp(-1.0))) < 1e-4

    # Large tau => SA -> 0
    assert cowan_dieke_sa_factor(20.0) < 0.10


def test_solve_cowan_dieke_tau() -> None:
    r_theo = 2.0
    # If r_obs == r_theo, tau = 0
    assert solve_cowan_dieke_tau(2.0, r_theo) == 0.0
    assert solve_cowan_dieke_tau(2.05, r_theo) == 0.0

    # If r_obs < r_theo, e.g. r_obs = 1.5, tau > 0
    tau = solve_cowan_dieke_tau(1.5, r_theo)
    assert tau > 0.5

    # Check forward model matches
    sa1 = cowan_dieke_sa_factor(tau)
    sa2 = cowan_dieke_sa_factor(tau / r_theo)
    r_reconstructed = r_theo * (sa1 / sa2)
    assert abs(r_reconstructed - 1.5) < 1e-3


def test_analyze_self_absorption_spectrum() -> None:
    wave = np.linspace(250.0, 650.0, 25000)
    flux = np.full_like(wave, 5.0)

    # Inject Ca II doublet at 393.477 nm and 396.959 nm with self-absorption
    # Theoretical ratio is ~2.10, but observed ratio is set to ~1.40
    flux[np.abs(wave - 393.477) < 0.04] += 28.0  # net ~ 28.0
    flux[np.abs(wave - 396.959) < 0.04] += 20.0  # net ~ 20.0 (ratio ~ 1.40)

    report = analyze_self_absorption(wave, flux, medium="vacuum")

    assert len(report.doublets) >= 3
    ca_res = next(d for d in report.doublets if d.element == "Ca")
    assert ca_res.status in ("absorbed", "thin")
    assert ca_res.observed_ratio > 1.2
    assert ca_res.tau_line1 > 0.0
    assert ca_res.sa_factor_line1 < 1.0
    assert ca_res.line1_i_corrected > ca_res.line1_i_obs

    as_dict = report.to_dict()
    assert "doublets" in as_dict
    assert "mean_self_absorption_factor" in as_dict


def test_cf_libs_inversion() -> None:
    wave = np.linspace(250.0, 650.0, 25000)
    flux = np.full_like(wave, 2.0)

    # Inject Fe and Mg lines
    flux[np.abs(wave - 373.593) < 0.04] += 50.0  # Fe I
    flux[np.abs(wave - 382.151) < 0.04] += 40.0  # Fe I
    flux[np.abs(wave - 285.296) < 0.04] += 60.0  # Mg I

    report = compute_cf_libs_inversion(wave, flux, temperature_k=9000.0, medium="vacuum")

    assert len(report.elements) >= 2
    elements_found = {e.element for e in report.elements}
    assert "Fe" in elements_found
    assert "Mg" in elements_found

    # Sum of atomic fractions must equal 1.0
    sum_atomic = sum(e.atomic_fraction for e in report.elements)
    assert abs(sum_atomic - 1.0) < 1e-4

    # Sum of weight fractions must equal 100.0%
    sum_weight = sum(e.weight_fraction_wt for e in report.elements)
    assert abs(sum_weight - 100.0) < 1e-3

    as_dict = report.to_dict()
    assert "temperature_k" in as_dict
    assert "elements" in as_dict
