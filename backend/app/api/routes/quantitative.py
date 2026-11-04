from __future__ import annotations

import functools
from typing import Annotated

import anyio.to_thread
import numpy as np
from fastapi import APIRouter, Depends, HTTPException
from spectralab_ml.models.quantitative import get_default_quantitative_regressor

from app.api.dependencies import get_services
from app.schemas.api import QuantitativePredictRequest, QuantitativeReportResponse
from app.services.container import Services

router = APIRouter(tags=["quantitative"])


def _execute_quantitative_sync(
    wave: np.ndarray,
    flux: np.ndarray,
    targets: list[str] | None,
    confidence_level: float,
    medium: str,
) -> dict:
    regressor = get_default_quantitative_regressor()
    report = regressor.predict(
        wave,
        flux,
        targets=targets,
        confidence_level=confidence_level,
        medium=medium,
    )
    return report.to_dict()


@router.post("/quantitative/predict", response_model=QuantitativeReportResponse)
async def predict_quantitative(
    request: QuantitativePredictRequest,
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
        _execute_quantitative_sync,
        wave=wave,
        flux=flux,
        targets=request.targets,
        confidence_level=request.confidence_level,
        medium=medium,
    )
    return await anyio.to_thread.run_sync(fn)
