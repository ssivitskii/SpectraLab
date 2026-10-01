from __future__ import annotations

from typing import Annotated

import numpy as np
from fastapi import APIRouter, Depends

from app.api.dependencies import get_services
from app.schemas.api import (
    PredictArraysRequest,
    PredictByIdRequest,
    PredictionResponse,
    PredictRequest,
)
from app.services.container import Services

router = APIRouter(tags=["prediction"])


@router.post("/predict", response_model=PredictionResponse)
def predict(
    request: PredictRequest, services: Annotated[Services, Depends(get_services)]
) -> dict[str, object]:
    if isinstance(request, PredictByIdRequest):
        stored = services.spectra.get(request.spectrum_id)
        wavelength = stored["wavelength_nm"]
        intensity = stored["intensity"]
    elif isinstance(request, PredictArraysRequest):
        wavelength = request.wavelength_nm
        intensity = request.intensity
    else:  # pragma: no cover - protected by Pydantic discriminator
        raise TypeError("Unsupported prediction input")
    # Deliberately pass only measurement arrays: generator truth and composition metadata
    # in a stored spectrum are never visible to the classifier.
    result = services.registry.predict(
        request.model_id,
        np.asarray(wavelength, dtype=float),
        np.asarray(intensity, dtype=float),
    )
    return result.to_dict()
