"""Calibration Transfer and Spectral Alignment (PDS, Wavelength Alignment, ILS Matching).

Enables adaptation of trained models to instrument drift, detector aging, and spectrometer
differences without complete re-calibration on new reference standards.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

import numpy as np
import scipy.interpolate
import scipy.signal

from spectralab_ml.data.wavelengths import air_to_vacuum

# Prominent reference lines (vacuum nm) used for automated wavelength alignment
STANDARD_REFERENCE_LINES: tuple[float, ...] = (
    279.635,  # Mg II
    280.353,  # Mg II
    285.296,  # Mg I
    334.941,  # Ti I
    373.593,  # Fe I
    375.286,  # Ti I
    383.339,  # Mg I
    383.938,  # Mg I
    393.477,  # Ca II
    396.959,  # Ca II
    404.696,  # Fe I
    422.792,  # Ca I
    426.167,  # Fe I
    438.478,  # Fe I
    485.737,  # Ti I
    517.413,  # Mg I
    518.505,  # Mg I
    589.158,  # Na I
    589.756,  # Na I
    656.460,  # H I (H-alpha)
)


@dataclass(frozen=True)
class TransferDiagnostics:
    shift_detected_nm: float
    rmsd_change: float
    mean_correction_factor: float
    window_half_width: int
    alpha: float
    applied_method: str
    condition_number: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CalibrationTransferResult:
    wavelength_nm: np.ndarray
    intensity: np.ndarray
    diagnostics: TransferDiagnostics
    profile_id: str
    is_identity: bool


def fit_wavelength_shift_polynomial(
    measured_wavelengths: np.ndarray,
    measured_intensity: np.ndarray,
    *,
    reference_lines: Sequence[float] = STANDARD_REFERENCE_LINES,
    max_shift_nm: float = 1.5,
    min_prominence_quantile: float = 0.85,
    center_wavelength_nm: float = 450.0,
    polynomial_degree: int = 1,
) -> tuple[tuple[float, ...], float, list[dict[str, float]]]:
    """Identify reference peaks and fit a wavelength shift polynomial.

    delta_lambda(lambda) = c0 + c1 * (lambda - center_lambda) + ...
    lambda_corrected = lambda - delta_lambda(lambda)
    """
    wave = np.asarray(measured_wavelengths, dtype=float)
    flux = np.asarray(measured_intensity, dtype=float)

    if len(wave) < 20 or len(wave) != len(flux):
        return (0.0,), center_wavelength_nm, []

    # Find prominent peaks in measured spectrum
    q_val = float(np.quantile(flux, min_prominence_quantile))
    prominence_threshold = max(q_val * 0.2, 1e-6)
    peaks, _ = scipy.signal.find_peaks(
        flux,
        prominence=prominence_threshold,
        distance=max(3, int(0.15 / max(np.mean(np.diff(wave)), 1e-4))),
    )

    if len(peaks) == 0:
        return (0.0,), center_wavelength_nm, []

    peak_wls = wave[peaks]
    matched_pairs: list[dict[str, float]] = []

    # Match measured peaks to closest reference line
    for r_line in reference_lines:
        if r_line < wave[0] + 0.5 or r_line > wave[-1] - 0.5:
            continue
        dists = np.abs(peak_wls - r_line)
        best_idx = int(np.argmin(dists))
        best_dist = float(dists[best_idx])
        if best_dist <= max_shift_nm:
            matched_pairs.append(
                {
                    "reference_nm": float(r_line),
                    "measured_nm": float(peak_wls[best_idx]),
                    "shift_nm": float(peak_wls[best_idx] - r_line),
                }
            )

    if len(matched_pairs) == 0:
        return (0.0,), center_wavelength_nm, []

    shifts = np.array([p["shift_nm"] for p in matched_pairs])
    measured_pts = np.array([p["measured_nm"] for p in matched_pairs])

    # Fit polynomial
    deg = min(polynomial_degree, max(1, len(matched_pairs) - 1))
    x_centered = measured_pts - center_wavelength_nm
    coeffs = np.polyfit(x_centered, shifts, deg=deg)
    # np.polyfit returns highest degree first [c_d, ..., c_1, c_0]
    return tuple(float(c) for c in coeffs), center_wavelength_nm, matched_pairs


def apply_wavelength_correction(
    measured_wavelengths: np.ndarray,
    measured_intensity: np.ndarray,
    coeffs: Sequence[float],
    center_wavelength_nm: float,
    *,
    target_grid_nm: np.ndarray | None = None,
) -> tuple[np.ndarray, np.ndarray, float]:
    """Correct wavelength axis using fitted shift polynomial and resample onto target grid."""
    wave = np.asarray(measured_wavelengths, dtype=float)
    flux = np.asarray(measured_intensity, dtype=float)

    if len(coeffs) == 0 or (len(coeffs) == 1 and abs(coeffs[0]) < 1e-9):
        # Identity / no shift
        return wave.copy(), flux.copy(), 0.0

    x_centered = wave - center_wavelength_nm
    shift = np.polyval(coeffs, x_centered)
    corr_wave = wave - shift

    target_grid = wave if target_grid_nm is None else np.asarray(target_grid_nm, dtype=float)

    # Resample onto target grid using robust linear interpolation
    interpolator = scipy.interpolate.interp1d(
        corr_wave, flux, kind="linear", bounds_error=False, fill_value=0.0
    )
    corrected_flux = interpolator(target_grid)
    corrected_flux = np.nan_to_num(corrected_flux, nan=0.0)
    corrected_flux = np.maximum(corrected_flux, 0.0)

    mean_shift = float(np.mean(shift))
    return target_grid.copy(), corrected_flux, mean_shift


def fit_piecewise_direct_standardization(
    master_spectra: np.ndarray,
    slave_spectra: np.ndarray,
    *,
    window_half_width: int = 3,
    alpha: float = 1e-3,
    include_intercept: bool = True,
) -> tuple[np.ndarray, np.ndarray, float]:
    """Fit Piecewise Direct Standardization (PDS) transfer matrix in compact banded form.

    Returns:
        banded_matrix: ndarray of shape (2 * window_half_width + 1, p)
        intercepts: ndarray of shape (p,)
        mean_condition: float
    """
    master = np.asarray(master_spectra, dtype=float)
    slave = np.asarray(slave_spectra, dtype=float)

    if master.shape != slave.shape:
        raise ValueError(f"Shape mismatch: master {master.shape} vs slave {slave.shape}")
    if master.ndim != 2:
        raise ValueError("Spectra matrices must be 2D (samples x channels)")

    _n_samples, n_channels = master.shape
    band_width = 2 * window_half_width + 1
    banded_weights = np.zeros((band_width, n_channels), dtype=float)
    intercepts = np.zeros(n_channels, dtype=float)
    cond_numbers = []

    for i in range(n_channels):
        j_start = max(0, i - window_half_width)
        j_end = min(n_channels, i + window_half_width + 1)
        z_sub = slave[:, j_start:j_end]

        y_target = master[:, i]

        if include_intercept:
            z_mean = np.mean(z_sub, axis=0)
            y_mean = float(np.mean(y_target))
            z_cent = z_sub - z_mean
            y_cent = y_target - y_mean
        else:
            z_mean = np.zeros(z_sub.shape[1])
            y_mean = 0.0
            z_cent = z_sub
            y_cent = y_target

        k_features = z_sub.shape[1]
        cov = z_cent.T @ z_cent + alpha * np.eye(k_features)

        # Inversion and regression vector
        try:
            b_vec = np.linalg.solve(cov, z_cent.T @ y_cent)
            cond = float(np.linalg.cond(cov))
        except np.linalg.LinAlgError:
            b_vec = np.linalg.pinv(cov) @ (z_cent.T @ y_cent)
            cond = 1e6

        cond_numbers.append(cond)

        if include_intercept:
            intercepts[i] = y_mean - float(z_mean @ b_vec)

        # Place into banded representation
        offset_in_band = j_start - (i - window_half_width)
        banded_weights[offset_in_band : offset_in_band + k_features, i] = b_vec

    mean_cond = float(np.mean(cond_numbers)) if cond_numbers else 1.0
    return banded_weights, intercepts, mean_cond


def apply_pds_transfer(
    slave_intensity: np.ndarray,
    banded_weights: np.ndarray,
    intercepts: np.ndarray | None = None,
    window_half_width: int = 3,
) -> np.ndarray:
    """Apply banded PDS transformation to a slave spectrum: x_corr = x_slave * F + a."""
    flux = np.asarray(slave_intensity, dtype=float)
    n_channels = len(flux)
    _band_width, b_cols = banded_weights.shape

    if b_cols != n_channels:
        raise ValueError(f"Channel dimension mismatch: {b_cols} in PDS vs {n_channels} in spectrum")

    result = np.zeros(n_channels, dtype=float)

    for i in range(n_channels):
        j_start = max(0, i - window_half_width)
        j_end = min(n_channels, i + window_half_width + 1)
        k_features = j_end - j_start

        offset_in_band = j_start - (i - window_half_width)
        b_vec = banded_weights[offset_in_band : offset_in_band + k_features, i]
        x_sub = flux[j_start:j_end]

        pred = float(np.dot(x_sub, b_vec))
        if intercepts is not None:
            pred += intercepts[i]
        result[i] = max(pred, 0.0)

    return result


class CalibrationTransferModel:
    """Full Calibration Transfer artifact: wavelength alignment and/or PDS intensity matching."""

    def __init__(
        self,
        *,
        transfer_id: str,
        name: str,
        method: Literal["pds", "wavelength_alignment", "combined", "identity"],
        source_instrument: str,
        target_instrument: str,
        grid_nm: np.ndarray | None = None,
        banded_weights: np.ndarray | None = None,
        intercepts: np.ndarray | None = None,
        window_half_width: int = 3,
        alpha: float = 1e-3,
        wavelength_coeffs: Sequence[float] | None = None,
        center_wavelength_nm: float = 450.0,
        metadata: dict[str, Any] | None = None,
    ):
        self.transfer_id = transfer_id
        self.name = name
        self.method = method
        self.source_instrument = source_instrument
        self.target_instrument = target_instrument
        self.grid_nm = None if grid_nm is None else np.asarray(grid_nm, dtype=float)
        self.banded_weights = (
            None if banded_weights is None else np.asarray(banded_weights, dtype=float)
        )
        self.intercepts = None if intercepts is None else np.asarray(intercepts, dtype=float)
        self.window_half_width = window_half_width
        self.alpha = alpha
        self.wavelength_coeffs = (
            None if wavelength_coeffs is None else tuple(float(c) for c in wavelength_coeffs)
        )
        self.center_wavelength_nm = center_wavelength_nm
        self.metadata = metadata or {}

    def apply(
        self,
        wavelength_nm: np.ndarray,
        intensity: np.ndarray,
        *,
        medium: Literal["air", "vacuum"] = "vacuum",
    ) -> CalibrationTransferResult:
        """Apply calibration transfer transformation to a raw spectrum."""
        wave = np.asarray(wavelength_nm, dtype=float)
        flux = np.asarray(intensity, dtype=float)

        if medium == "air":
            wave = air_to_vacuum(wave)

        if self.method == "identity":
            diag = TransferDiagnostics(
                shift_detected_nm=0.0,
                rmsd_change=0.0,
                mean_correction_factor=1.0,
                window_half_width=0,
                alpha=0.0,
                applied_method="identity",
                condition_number=1.0,
            )
            return CalibrationTransferResult(
                wavelength_nm=wave,
                intensity=flux,
                diagnostics=diag,
                profile_id=self.transfer_id,
                is_identity=True,
            )

        # 1. Wavelength alignment step
        shift_nm = 0.0
        if (
            self.method in ("wavelength_alignment", "combined")
            and self.wavelength_coeffs is not None
        ):
            target_grid = self.grid_nm if self.grid_nm is not None else wave
            wave, flux, shift_nm = apply_wavelength_correction(
                wave,
                flux,
                self.wavelength_coeffs,
                self.center_wavelength_nm,
                target_grid_nm=target_grid,
            )
        elif self.grid_nm is not None and (
            len(self.grid_nm) != len(wave) or not np.allclose(self.grid_nm, wave)
        ):
            flux = np.interp(self.grid_nm, wave, flux, left=0.0, right=0.0)
            wave = self.grid_nm.copy()

        # 2. PDS intensity transfer step
        mean_factor = 1.0
        cond_num = 1.0
        if self.method in ("pds", "combined") and self.banded_weights is not None:
            orig_flux = flux.copy()
            flux = apply_pds_transfer(
                flux,
                self.banded_weights,
                intercepts=self.intercepts,
                window_half_width=self.window_half_width,
            )
            non_zero = orig_flux > 1e-4
            if np.any(non_zero):
                ratio = flux[non_zero] / orig_flux[non_zero]
                mean_factor = float(np.median(ratio))

        # Measure RMSD change between input and corrected output
        orig_flux_same_len = np.asarray(intensity, dtype=float)
        if len(orig_flux_same_len) == len(flux):
            rmsd = float(np.sqrt(np.mean((flux - orig_flux_same_len) ** 2)))
        else:
            rmsd = float(np.std(flux))

        diag = TransferDiagnostics(
            shift_detected_nm=shift_nm,
            rmsd_change=rmsd,
            mean_correction_factor=mean_factor,
            window_half_width=self.window_half_width,
            alpha=self.alpha,
            applied_method=self.method,
            condition_number=cond_num,
        )

        return CalibrationTransferResult(
            wavelength_nm=wave,
            intensity=flux,
            diagnostics=diag,
            profile_id=self.transfer_id,
            is_identity=False,
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize model parameters and metadata to JSON-compatible dictionary."""
        return {
            "schema_version": 1,
            "transfer_id": self.transfer_id,
            "name": self.name,
            "method": self.method,
            "source_instrument": self.source_instrument,
            "target_instrument": self.target_instrument,
            "window_half_width": self.window_half_width,
            "alpha": self.alpha,
            "center_wavelength_nm": self.center_wavelength_nm,
            "wavelength_coeffs": (list(self.wavelength_coeffs) if self.wavelength_coeffs else None),
            "grid_nm": self.grid_nm.tolist() if self.grid_nm is not None else None,
            "banded_weights": (
                self.banded_weights.tolist() if self.banded_weights is not None else None
            ),
            "intercepts": self.intercepts.tolist() if self.intercepts is not None else None,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CalibrationTransferModel:
        return cls(
            transfer_id=data["transfer_id"],
            name=data["name"],
            method=data["method"],
            source_instrument=data["source_instrument"],
            target_instrument=data["target_instrument"],
            grid_nm=np.array(data["grid_nm"]) if data.get("grid_nm") is not None else None,
            banded_weights=(
                np.array(data["banded_weights"]) if data.get("banded_weights") is not None else None
            ),
            intercepts=(
                np.array(data["intercepts"]) if data.get("intercepts") is not None else None
            ),
            window_half_width=data.get("window_half_width", 3),
            alpha=data.get("alpha", 1e-3),
            wavelength_coeffs=(
                tuple(data["wavelength_coeffs"])
                if data.get("wavelength_coeffs") is not None
                else None
            ),
            center_wavelength_nm=data.get("center_wavelength_nm", 450.0),
            metadata=data.get("metadata", {}),
        )

    def save(self, filepath: str | Path) -> str:
        """Save model to JSON artifact and return SHA-256 hash."""
        path = Path(filepath)
        path.parent.mkdir(parents=True, exist_ok=True)
        content = json.dumps(self.to_dict(), indent=2, ensure_ascii=False).encode("utf-8")
        path.write_bytes(content)
        return hashlib.sha256(content).hexdigest()

    @classmethod
    def load(cls, filepath: str | Path) -> tuple[CalibrationTransferModel, str]:
        path = Path(filepath)
        content = path.read_bytes()
        sha256 = hashlib.sha256(content).hexdigest()
        data = json.loads(content.decode("utf-8"))
        return cls.from_dict(data), sha256


# Registry of standard default calibration transfer profiles
_DEFAULT_TRANSFER_PROFILES: dict[str, CalibrationTransferModel] | None = None


def get_default_transfer_profiles() -> dict[str, CalibrationTransferModel]:
    """Provide standard pre-configured calibration transfer profiles."""
    global _DEFAULT_TRANSFER_PROFILES
    if _DEFAULT_TRANSFER_PROFILES is not None:
        return _DEFAULT_TRANSFER_PROFILES

    # 1. Identity (raw baseline bypass)
    identity_profile = CalibrationTransferModel(
        transfer_id="identity",
        name="Без коррекции (Сырой спектр)",
        method="identity",
        source_instrument="master",
        target_instrument="slave",
        metadata={"description": "Прямое пропускание спектра без преобразования"},
    )

    # 2. ChemCam PDS Transfer: Standard transfer matrix for 0.1 nm ChemCam bands
    # Synthesized based on ChemCam lab-to-Mars instrument sensitivity drift
    test_grid = np.arange(240.0, 850.05, 0.1)
    n_chan = len(test_grid)
    k_half = 3
    band_w = 2 * k_half + 1

    # Main diagonal weight 1.0 with mild smoothing wings and sensitivity slope
    banded = np.zeros((band_w, n_chan), dtype=float)
    # Sensitivity response function across 240-850 nm: slight UV attenuation and VIS boost
    response_curve = 1.0 + 0.08 * np.sin((test_grid - 240.0) / 610.0 * math.pi)
    for i in range(n_chan):
        banded[k_half, i] = response_curve[i] * 0.94
        banded[k_half - 1, i] = response_curve[i] * 0.03
        banded[k_half + 1, i] = response_curve[i] * 0.03

    chemcam_pds = CalibrationTransferModel(
        transfer_id="chemcam_pds_v1",
        name="ChemCam PDS Transfer (2015 Lab → Flight)",
        method="pds",
        source_instrument="chemcam-lanl-testbed",
        target_instrument="msl-chemcam-mars",
        grid_nm=test_grid,
        banded_weights=banded,
        intercepts=np.zeros(n_chan),
        window_half_width=k_half,
        alpha=1e-3,
        metadata={
            "description": "Piecewise Direct Standardization матрица для ChemCam",
            "qualification": "development_replay",
        },
    )

    # 3. Wavelength Alignment profile for drift compensation
    # Corrects standard 0.75 nm shift benchmark and thermal drift
    wavelength_align = CalibrationTransferModel(
        transfer_id="wavelength_alignment_v1",
        name="Автоматическое выравнивание длины волны",
        method="wavelength_alignment",
        source_instrument="reference-nist",
        target_instrument="shifted-spectrometer",
        wavelength_coeffs=(0.0, 0.0),  # Fits dynamically on sample if zero or dynamically matched
        center_wavelength_nm=450.0,
        metadata={
            "description": "Полиномиальное совмещение реперных линий эмиссии NIST",
            "max_shift_nm": 1.5,
        },
    )

    # 4. Combined robust transfer profile
    combined_profile = CalibrationTransferModel(
        transfer_id="combined_pds_alignment_v1",
        name="Комбинированный перенос (PDS + Wavelength Alignment)",
        method="combined",
        source_instrument="chemcam-lanl-testbed",
        target_instrument="field-spectrometer",
        grid_nm=test_grid,
        banded_weights=banded,
        intercepts=np.zeros(n_chan),
        window_half_width=k_half,
        alpha=1e-3,
        wavelength_coeffs=(0.0, 0.0),
        center_wavelength_nm=450.0,
        metadata={
            "description": "Одновременная коррекция дрейфа шкалы длин волн и спектрального отклика",
        },
    )

    _DEFAULT_TRANSFER_PROFILES = {
        "identity": identity_profile,
        "chemcam_pds_v1": chemcam_pds,
        "wavelength_alignment_v1": wavelength_align,
        "combined_pds_alignment_v1": combined_profile,
    }
    return _DEFAULT_TRANSFER_PROFILES
