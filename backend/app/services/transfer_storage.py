from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import numpy as np
from spectralab_ml.models.calibration_transfer import (
    CalibrationTransferModel,
    CalibrationTransferResult,
    fit_piecewise_direct_standardization,
    fit_wavelength_shift_polynomial,
    get_default_transfer_profiles,
)

from app.schemas.api import TransferProfileSummaryDto

logger = logging.getLogger("spectralab.transfer_storage")


class TransferStorage:
    def __init__(self, storage_dir: Path):
        self.storage_dir = storage_dir
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self._profiles: dict[str, CalibrationTransferModel] = {}
        self._refresh()

    def _refresh(self) -> None:
        # Load defaults
        self._profiles = dict(get_default_transfer_profiles())

        # Load any persisted custom profiles
        for p in self.storage_dir.glob("*.json"):
            if p.is_file() and not p.is_symlink():
                try:
                    model, _ = CalibrationTransferModel.load(p)
                    self._profiles[model.transfer_id] = model
                except Exception as exc:
                    logger.warning("Failed to load transfer profile from %s: %s", p, exc)

    def list_profiles(self) -> list[TransferProfileSummaryDto]:
        results: list[TransferProfileSummaryDto] = []
        for model in self._profiles.values():
            results.append(
                TransferProfileSummaryDto(
                    profile_id=model.transfer_id,
                    name=model.name,
                    method=model.method,
                    source_instrument=model.source_instrument,
                    target_instrument=model.target_instrument,
                    window_half_width=model.window_half_width,
                    alpha=model.alpha,
                    description=model.metadata.get("description"),
                )
            )
        return results

    def get_profile(self, profile_id: str) -> CalibrationTransferModel | None:
        return self._profiles.get(profile_id)

    def apply(
        self,
        profile_id: str,
        wave: np.ndarray,
        flux: np.ndarray,
        medium: str = "vacuum",
    ) -> CalibrationTransferResult:
        model = self.get_profile(profile_id)
        if model is None:
            raise KeyError(f"Transfer profile '{profile_id}' not found")
        med = "air" if medium == "air" else "vacuum"
        return model.apply(wave, flux, medium=med)

    def fit_and_register(
        self,
        *,
        transfer_id: str,
        name: str,
        method: str,
        master_wave: np.ndarray,
        master_flux: np.ndarray,
        slave_wave: np.ndarray,
        slave_flux: np.ndarray,
        window_half_width: int = 3,
        alpha: float = 1e-3,
        source_instrument: str = "master",
        target_instrument: str = "slave",
        description: str | None = None,
    ) -> tuple[TransferProfileSummaryDto, str]:
        # Resample master onto slave grid if they differ
        if len(master_wave) != len(slave_wave) or not np.allclose(master_wave, slave_wave):
            interp_master = np.zeros_like(slave_flux)
            for i in range(len(master_flux)):
                interp_master[i] = np.interp(
                    slave_wave, master_wave, master_flux[i], left=0.0, right=0.0
                )
            master_aligned = interp_master
            grid_nm = slave_wave.copy()
        else:
            master_aligned = master_flux.copy()
            grid_nm = slave_wave.copy()

        banded_weights = None
        intercepts = None
        wavelength_coeffs = None
        center_wl = 450.0

        if method in ("wavelength_alignment", "combined"):
            # Estimate shift on mean slave spectrum relative to mean master spectrum
            mean_slave = np.mean(slave_flux, axis=0)
            coeffs, center_wl, _ = fit_wavelength_shift_polynomial(
                grid_nm, mean_slave, center_wavelength_nm=float(np.median(grid_nm))
            )
            wavelength_coeffs = coeffs

        if method in ("pds", "combined"):
            banded_weights, intercepts, _ = fit_piecewise_direct_standardization(
                master_aligned,
                slave_flux,
                window_half_width=window_half_width,
                alpha=alpha,
            )

        metadata: dict[str, Any] = {
            "description": description,
            "fitted_samples_count": len(master_flux),
        }

        model = CalibrationTransferModel(
            transfer_id=transfer_id,
            name=name,
            method=method,  # type: ignore[arg-type]
            source_instrument=source_instrument,
            target_instrument=target_instrument,
            grid_nm=grid_nm,
            banded_weights=banded_weights,
            intercepts=intercepts,
            window_half_width=window_half_width,
            alpha=alpha,
            wavelength_coeffs=wavelength_coeffs,
            center_wavelength_nm=center_wl,
            metadata=metadata,
        )

        save_path = self.storage_dir / f"{transfer_id}.json"
        sha256 = model.save(save_path)
        self._profiles[transfer_id] = model

        dto = TransferProfileSummaryDto(
            profile_id=model.transfer_id,
            name=model.name,
            method=model.method,
            source_instrument=model.source_instrument,
            target_instrument=model.target_instrument,
            window_half_width=model.window_half_width,
            alpha=model.alpha,
            description=description,
        )
        return dto, sha256
