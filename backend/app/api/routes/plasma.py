from __future__ import annotations

import functools
from typing import Annotated, Any

import anyio.to_thread
import numpy as np
from fastapi import APIRouter, Depends, HTTPException
from spectralab_ml.simulation.diagnostics import analyze_plasma_diagnostics
from spectralab_ml.simulation.self_absorption import (
    analyze_self_absorption,
    compute_cf_libs_inversion,
)

from app.api.dependencies import get_services
from app.schemas.api import (
    CFLibsRequest,
    CFLibsResponse,
    PlasmaDiagnosticsRequest,
    PlasmaDiagnosticsResponse,
    SelfAbsorptionRequest,
    SelfAbsorptionResponse,
)
from app.services.container import Services

router = APIRouter(tags=["plasma"])


def _extract_wave_flux_medium(
    request: Any,
    services: Services,
) -> tuple[np.ndarray, np.ndarray, str]:
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
    return wave, flux, medium


def _execute_diagnostics_sync(
    wave: np.ndarray,
    flux: np.ndarray,
    element: str,
    instrumental_fwhm_nm: float,
    medium: str,
) -> dict:
    result = analyze_plasma_diagnostics(
        wave,
        flux,
        element=element,
        instrumental_fwhm_nm=instrumental_fwhm_nm,
        medium=medium,
    )
    return result.to_dict()


def _execute_self_absorption_sync(
    wave: np.ndarray,
    flux: np.ndarray,
    medium: str,
) -> dict:
    result = analyze_self_absorption(wave, flux, medium=medium)
    return result.to_dict()


def _execute_cf_libs_sync(
    wave: np.ndarray,
    flux: np.ndarray,
    medium: str,
    temperature_k: float | None,
) -> dict:
    if temperature_k is not None:
        temp = float(temperature_k)
    else:
        diag = analyze_plasma_diagnostics(
            wave, flux, element="auto", instrumental_fwhm_nm=0.10, medium=medium
        )
        temp = float(diag.summary.get("temperature_k", 8000.0)) if diag.summary else 8000.0

    result = compute_cf_libs_inversion(wave, flux, temperature_k=temp, medium=medium)
    return result.to_dict()


@router.post("/plasma/diagnostics", response_model=PlasmaDiagnosticsResponse)
async def get_plasma_diagnostics(
    request: PlasmaDiagnosticsRequest,
    services: Annotated[Services, Depends(get_services)],
) -> dict:
    wave, flux, medium = _extract_wave_flux_medium(request, services)

    fn = functools.partial(
        _execute_diagnostics_sync,
        wave=wave,
        flux=flux,
        element=request.element,
        instrumental_fwhm_nm=request.instrumental_fwhm_nm,
        medium=medium,
    )
    return await anyio.to_thread.run_sync(fn)


@router.post("/plasma/self-absorption", response_model=SelfAbsorptionResponse)
async def get_self_absorption(
    request: SelfAbsorptionRequest,
    services: Annotated[Services, Depends(get_services)],
) -> dict:
    wave, flux, medium = _extract_wave_flux_medium(request, services)

    fn = functools.partial(
        _execute_self_absorption_sync,
        wave=wave,
        flux=flux,
        medium=medium,
    )
    return await anyio.to_thread.run_sync(fn)


@router.post("/plasma/cf-libs", response_model=CFLibsResponse)
async def get_cf_libs(
    request: CFLibsRequest,
    services: Annotated[Services, Depends(get_services)],
) -> dict:
    wave, flux, medium = _extract_wave_flux_medium(request, services)

    fn = functools.partial(
        _execute_cf_libs_sync,
        wave=wave,
        flux=flux,
        medium=medium,
        temperature_k=request.temperature_k,
    )
    return await anyio.to_thread.run_sync(fn)
