from __future__ import annotations

import functools
from typing import Annotated

import anyio.to_thread
import numpy as np
from fastapi import APIRouter, Depends, HTTPException
from spectralab_ml.simulation.diagnostics import analyze_plasma_diagnostics

from app.api.dependencies import get_services
from app.schemas.api import PlasmaDiagnosticsRequest, PlasmaDiagnosticsResponse
from app.services.container import Services

router = APIRouter(tags=["plasma"])


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


@router.post("/plasma/diagnostics", response_model=PlasmaDiagnosticsResponse)
async def get_plasma_diagnostics(
    request: PlasmaDiagnosticsRequest,
    services: Annotated[Services, Depends(get_services)],
) -> dict:
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
        _execute_diagnostics_sync,
        wave=wave,
        flux=flux,
        element=request.element,
        instrumental_fwhm_nm=request.instrumental_fwhm_nm,
        medium=medium,
    )
    return await anyio.to_thread.run_sync(fn)
