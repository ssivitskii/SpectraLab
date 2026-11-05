from __future__ import annotations

import functools
from typing import Annotated

import anyio.to_thread
import numpy as np
from fastapi import APIRouter, Depends, HTTPException

from app.api.dependencies import get_services
from app.schemas.api import (
    TransferApplyRequest,
    TransferApplyResponse,
    TransferDiagnosticsDto,
    TransferFitRequest,
    TransferFitResponse,
    TransferProfilesListResponse,
    TransferProfileSummaryDto,
)
from app.services.container import Services

router = APIRouter(tags=["transfer"])


@router.get("/transfer/profiles", response_model=TransferProfilesListResponse)
def list_transfer_profiles(
    services: Annotated[Services, Depends(get_services)],
) -> TransferProfilesListResponse:
    profiles = services.transfer.list_profiles()
    return TransferProfilesListResponse(profiles=profiles)


@router.get("/transfer/profiles/{profile_id}", response_model=TransferProfileSummaryDto)
def get_transfer_profile(
    profile_id: str,
    services: Annotated[Services, Depends(get_services)],
) -> TransferProfileSummaryDto:
    model = services.transfer.get_profile(profile_id)
    if model is None:
        raise HTTPException(404, detail=f"Transfer profile '{profile_id}' not found")
    return TransferProfileSummaryDto(
        profile_id=model.transfer_id,
        name=model.name,
        method=model.method,
        source_instrument=model.source_instrument,
        target_instrument=model.target_instrument,
        window_half_width=model.window_half_width,
        alpha=model.alpha,
        description=model.metadata.get("description"),
    )


def _execute_apply_sync(
    services: Services,
    profile_id: str,
    wave: np.ndarray,
    flux: np.ndarray,
    medium: str,
) -> TransferApplyResponse:
    try:
        res = services.transfer.apply(profile_id, wave, flux, medium=medium)
    except KeyError as exc:
        raise HTTPException(404, detail=str(exc)) from exc

    diag = TransferDiagnosticsDto(
        shift_detected_nm=res.diagnostics.shift_detected_nm,
        rmsd_change=res.diagnostics.rmsd_change,
        mean_correction_factor=res.diagnostics.mean_correction_factor,
        window_half_width=res.diagnostics.window_half_width,
        alpha=res.diagnostics.alpha,
        applied_method=res.diagnostics.applied_method,
        condition_number=res.diagnostics.condition_number,
    )

    return TransferApplyResponse(
        wavelength_nm=res.wavelength_nm.tolist(),
        intensity=res.intensity.tolist(),
        profile_id=res.profile_id,
        method=res.diagnostics.applied_method,
        is_identity=res.is_identity,
        diagnostics=diag,
        original_point_count=len(wave),
        corrected_point_count=len(res.wavelength_nm),
    )


@router.post("/transfer/apply", response_model=TransferApplyResponse)
async def apply_calibration_transfer(
    request: TransferApplyRequest,
    services: Annotated[Services, Depends(get_services)],
) -> TransferApplyResponse:
    if request.spectrum_id:
        stored = services.spectra.get(request.spectrum_id)
        if not stored:
            raise HTTPException(404, detail="Spectrum not found")
        wave = np.asarray(stored["wavelength_nm"], dtype=float)
        flux = np.asarray(stored["intensity"], dtype=float)
        medium = stored.get("wavelength_medium", request.medium)
    elif request.wavelength_nm is not None and request.intensity is not None:
        if len(request.wavelength_nm) != len(request.intensity):
            raise HTTPException(
                422, detail="wavelength_nm and intensity arrays must match in length"
            )
        if len(request.wavelength_nm) < 10:
            raise HTTPException(422, detail="At least 10 spectrum points required")
        wave = np.asarray(request.wavelength_nm, dtype=float)
        flux = np.asarray(request.intensity, dtype=float)
        medium = request.medium
    else:
        raise HTTPException(
            422,
            detail="Either 'spectrum_id' or both 'wavelength_nm' and 'intensity' must be provided",
        )

    fn = functools.partial(
        _execute_apply_sync,
        services=services,
        profile_id=request.profile_id,
        wave=wave,
        flux=flux,
        medium=medium,
    )
    return await anyio.to_thread.run_sync(fn)


def _execute_fit_sync(services: Services, request: TransferFitRequest) -> TransferFitResponse:
    m_wave = np.asarray(request.master_wavelength_nm, dtype=float)
    s_wave = np.asarray(request.slave_wavelength_nm, dtype=float)
    m_flux = np.asarray(request.master_spectra, dtype=float)
    s_flux = np.asarray(request.slave_spectra, dtype=float)

    if m_flux.ndim != 2 or s_flux.ndim != 2:
        raise HTTPException(422, detail="Spectra must be 2D matrices (samples x channels)")
    if len(m_flux) != len(s_flux):
        raise HTTPException(422, detail="Master and slave must contain equal numbers of samples")
    if m_flux.shape[1] != len(m_wave):
        raise HTTPException(
            422, detail="Master spectra channel count does not match master_wavelength_nm length"
        )
    if s_flux.shape[1] != len(s_wave):
        raise HTTPException(
            422, detail="Slave spectra channel count does not match slave_wavelength_nm length"
        )

    dto, sha256 = services.transfer.fit_and_register(
        transfer_id=request.transfer_id,
        name=request.name,
        method=request.method,
        master_wave=m_wave,
        master_flux=m_flux,
        slave_wave=s_wave,
        slave_flux=s_flux,
        window_half_width=request.window_half_width,
        alpha=request.alpha,
        source_instrument=request.source_instrument,
        target_instrument=request.target_instrument,
        description=request.description,
    )

    return TransferFitResponse(
        profile=dto,
        sha256=sha256,
        message=f"Calibration transfer profile '{request.transfer_id}' fitted successfully",
    )


@router.post("/transfer/fit", response_model=TransferFitResponse)
async def fit_calibration_transfer(
    request: TransferFitRequest,
    services: Annotated[Services, Depends(get_services)],
) -> TransferFitResponse:
    fn = functools.partial(_execute_fit_sync, services=services, request=request)
    return await anyio.to_thread.run_sync(fn)
