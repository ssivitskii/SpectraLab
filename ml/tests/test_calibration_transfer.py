from __future__ import annotations

import numpy as np
from spectralab_ml.models.calibration_transfer import (
    CalibrationTransferModel,
    apply_pds_transfer,
    apply_wavelength_correction,
    fit_piecewise_direct_standardization,
    fit_wavelength_shift_polynomial,
    get_default_transfer_profiles,
)


def test_fit_pds_reduces_reconstruction_error():
    """Verify that Piecewise Direct Standardization recovers master spectra from degraded slave."""
    rng = np.random.default_rng(42)
    n_channels = 200
    grid = np.linspace(300.0, 500.0, n_channels)

    # Reference standards with varying concentrations across known emission lines
    line_centers = [320.0, 350.0, 380.0, 410.0, 440.0, 470.0]
    n_standards = 12
    weights = rng.uniform(0.5, 5.0, size=(n_standards, len(line_centers)))

    master = np.zeros((n_standards, n_channels))
    for s in range(n_standards):
        for idx, c in enumerate(line_centers):
            master[s] += weights[s, idx] * np.exp(-0.5 * ((grid - c) / 1.5) ** 2)

    # Slave: distorted with local blurring (band convolution) and sensitivity tilt
    sensitivity = 0.8 + 0.4 * (grid - 300.0) / 200.0
    slave = np.zeros_like(master)
    for s in range(n_standards):
        blurred = np.convolve(master[s], [0.1, 0.8, 0.1], mode="same")
        slave[s] = blurred * sensitivity + rng.normal(0, 0.01, n_channels)
        slave[s] = np.maximum(slave[s], 0.0)

    # Uncorrected error on test set (standards 8..11)
    uncorrected_rmsd = float(np.sqrt(np.mean((slave[8:] - master[8:]) ** 2)))

    # Fit PDS on standards 0..7
    window_k = 3
    banded_weights, intercepts, _ = fit_piecewise_direct_standardization(
        master[:8], slave[:8], window_half_width=window_k, alpha=1e-3
    )

    # Apply to test standards
    corrected_test = np.zeros_like(slave[8:])
    for idx, sample in enumerate(slave[8:]):
        corrected_test[idx] = apply_pds_transfer(
            sample, banded_weights, intercepts=intercepts, window_half_width=window_k
        )

    corrected_rmsd = float(np.sqrt(np.mean((corrected_test - master[8:]) ** 2)))

    # PDS should reduce error by at least 70%
    assert corrected_rmsd < uncorrected_rmsd * 0.3


def test_wavelength_alignment_recovers_known_shift():
    """Verify that automated wavelength alignment detects and corrects 0.75 nm shift."""
    grid = np.arange(250.0, 660.0, 0.05)
    true_lines = [279.635, 373.593, 404.696, 422.792, 518.505, 589.158, 656.460]

    # Generate reference spectrum
    intensity = np.zeros_like(grid)
    for line in true_lines:
        intensity += 50.0 * np.exp(-0.5 * ((grid - line) / 0.12) ** 2)

    # Apply a known shift of +0.75 nm
    known_shift = 0.75
    shifted_grid = grid + known_shift

    coeffs, center, pairs = fit_wavelength_shift_polynomial(
        shifted_grid,
        intensity,
        reference_lines=true_lines,
        max_shift_nm=1.2,
        polynomial_degree=1,
    )

    assert len(pairs) >= 5
    # The constant term (c0) evaluated at center should be close to +0.75 nm
    recovered_shift = float(np.polyval(coeffs, 0.0))
    assert abs(recovered_shift - known_shift) < 0.05

    # Apply correction and check peak alignment
    corr_grid, corr_flux, mean_shift = apply_wavelength_correction(
        shifted_grid, intensity, coeffs, center, target_grid_nm=grid
    )
    assert abs(mean_shift - known_shift) < 0.05

    # Verify peak at 404.696 nm is restored
    peak_idx = int(np.argmin(np.abs(corr_grid - 404.696)))
    assert corr_flux[peak_idx] > 35.0


def test_calibration_transfer_model_serialization(tmp_path):
    """Verify artifact serialization and SHA-256 roundtrip."""
    test_grid = np.linspace(350.0, 450.0, 100)
    banded = np.ones((7, 100)) * 0.14
    intercepts = np.zeros(100)

    model = CalibrationTransferModel(
        transfer_id="test_model_v1",
        name="Test Transfer Model",
        method="combined",
        source_instrument="inst_a",
        target_instrument="inst_b",
        grid_nm=test_grid,
        banded_weights=banded,
        intercepts=intercepts,
        window_half_width=3,
        alpha=1e-3,
        wavelength_coeffs=(0.01, 0.25),
        center_wavelength_nm=400.0,
        metadata={"author": "Antigravity", "notes": "unit test"},
    )

    save_path = tmp_path / "transfer_model.json"
    sha1 = model.save(save_path)
    assert save_path.exists()

    loaded_model, sha2 = CalibrationTransferModel.load(save_path)
    assert sha1 == sha2
    assert loaded_model.transfer_id == "test_model_v1"
    assert loaded_model.method == "combined"
    assert loaded_model.window_half_width == 3
    assert np.allclose(loaded_model.banded_weights, banded)
    assert np.allclose(loaded_model.wavelength_coeffs, (0.01, 0.25))


def test_default_profiles_and_identity():
    """Verify default profiles are present and identity does not mutate input."""
    profiles = get_default_transfer_profiles()
    assert "identity" in profiles
    assert "chemcam_pds_v1" in profiles
    assert "wavelength_alignment_v1" in profiles
    assert "combined_pds_alignment_v1" in profiles

    identity_model = profiles["identity"]
    wave = np.array([400.0, 401.0, 402.0])
    flux = np.array([10.0, 20.0, 15.0])

    res = identity_model.apply(wave, flux)
    assert res.is_identity is True
    assert np.allclose(res.wavelength_nm, wave)
    assert np.allclose(res.intensity, flux)
    assert res.diagnostics.shift_detected_nm == 0.0
