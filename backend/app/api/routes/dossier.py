from __future__ import annotations

import functools
import json
from typing import Annotated, Literal

import anyio.to_thread
import numpy as np
from fastapi import APIRouter, Depends, HTTPException, Query, Response

from app.api.dependencies import get_services
from app.schemas.api import DossierGenerateRequest, MeasurementDossierResponse
from app.services.container import Services

router = APIRouter(tags=["dossier"])


def _execute_dossier_sync(
    services: Services,
    wave: np.ndarray,
    flux: np.ndarray,
    spectrum_id: str | None,
    transfer_profile_id: str,
    plasma_element: str,
    plasma_fwhm_nm: float,
    quantitative_targets: list[str] | None,
    confidence_level: float,
    medium: str,
) -> dict:
    transfer_res = None
    if transfer_profile_id != "identity":
        transfer_model = services.transfer.get(transfer_profile_id)
        if not transfer_model:
            raise HTTPException(404, detail=f"Transfer profile '{transfer_profile_id}' not found")
        transfer_res = transfer_model.apply(wave, flux)
        wave = transfer_res.corrected_wavelength_nm
        flux = transfer_res.corrected_intensity

    return services.dossier.generate_dossier(
        wave,
        flux,
        spectrum_id=spectrum_id,
        transfer_profile_id=transfer_profile_id,
        plasma_element=plasma_element,
        plasma_fwhm_nm=plasma_fwhm_nm,
        quantitative_targets=quantitative_targets,
        confidence_level=confidence_level,
        medium=medium,
        transfer_result=transfer_res,
    )


@router.post("/dossier/generate", response_model=MeasurementDossierResponse)
async def generate_dossier(
    request: DossierGenerateRequest,
    services: Annotated[Services, Depends(get_services)],
) -> dict:
    if request.spectrum_id:
        stored = services.spectra.get(request.spectrum_id)
        if not stored:
            raise HTTPException(404, detail="Spectrum not found")
        wave = np.asarray(stored["wavelength_nm"], dtype=float)
        flux = np.asarray(stored["intensity"], dtype=float)
        medium = stored.get("wavelength_medium", request.medium)
        spectrum_id = request.spectrum_id
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
        spectrum_id = None
    else:
        raise HTTPException(
            422,
            detail="Either 'spectrum_id' or both 'wavelength_nm' and 'intensity' must be provided",
        )

    fn = functools.partial(
        _execute_dossier_sync,
        services=services,
        wave=wave,
        flux=flux,
        spectrum_id=spectrum_id,
        transfer_profile_id=request.transfer_profile_id,
        plasma_element=request.plasma_element,
        plasma_fwhm_nm=request.plasma_fwhm_nm,
        quantitative_targets=request.quantitative_targets,
        confidence_level=request.confidence_level,
        medium=medium,
    )
    return await anyio.to_thread.run_sync(fn)


@router.get("/dossier/{dossier_id}", response_model=MeasurementDossierResponse)
async def get_dossier(
    dossier_id: str,
    services: Annotated[Services, Depends(get_services)],
) -> dict:
    dossier = services.dossier.get(dossier_id)
    if not dossier:
        raise HTTPException(404, detail=f"Dossier '{dossier_id}' not found")
    return dossier


@router.get("/dossier/{dossier_id}/download")
async def download_dossier(
    dossier_id: str,
    services: Annotated[Services, Depends(get_services)],
    format: Literal["json", "html", "csv"] = Query(default="json"),
) -> Response:
    dossier = services.dossier.get(dossier_id)
    if not dossier:
        raise HTTPException(404, detail=f"Dossier '{dossier_id}' not found")

    if format == "json":
        content = json.dumps(dossier, ensure_ascii=False, indent=2)
        return Response(
            content=content,
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="{dossier_id}.json"'},
        )
    elif format == "html":
        content = services.dossier.render_html(dossier)
        return Response(
            content=content,
            media_type="text/html; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{dossier_id}.html"'},
        )
    elif format == "csv":
        content = services.dossier.render_csv(dossier)
        return Response(
            content=content,
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{dossier_id}_lines.csv"'},
        )
    raise HTTPException(400, detail="Unsupported download format")
