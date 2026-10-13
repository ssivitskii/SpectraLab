from __future__ import annotations

from typing import Annotated, Literal

import numpy as np
from fastapi import APIRouter, Depends, HTTPException, Response

from app.api.dependencies import get_services
from app.schemas.api import (
    PredictArraysRequest,
    PredictByIdRequest,
    PredictionResponse,
    PredictRequest,
)
from app.services.container import Services
from app.services.exports import export_record

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
        stored = {
            "wavelength_nm": wavelength,
            "intensity": intensity,
            "units": request.units,
            "wavelength_medium": request.wavelength_medium,
        }
    else:  # pragma: no cover - protected by Pydantic discriminator
        raise TypeError("Unsupported prediction input")
    # Deliberately pass only measurement arrays: generator truth and composition metadata
    # in a stored spectrum are never visible to the classifier.
    try:
        result = services.registry.predict(
            request.model_id,
            np.asarray(wavelength, dtype=float),
            np.asarray(intensity, dtype=float),
        )
    except (FileNotFoundError, ImportError) as exc:
        raise HTTPException(
            404,
            {
                "code": "model_unavailable",
                "message": "Model is unavailable; inspect /models for its diagnostic code",
                "details": {"model_id": request.model_id},
            },
        ) from exc
    return services.predictions.save(
        {**result.to_dict(), "reference_metadata": services.catalog.metadata}, stored
    )


@router.get("/predictions/{prediction_id}", response_model=PredictionResponse)
def get_prediction(
    prediction_id: str, services: Annotated[Services, Depends(get_services)]
) -> dict[str, object]:
    return services.predictions.get(prediction_id)


@router.get("/predictions/{prediction_id}/export")
def export_prediction(
    prediction_id: str,
    services: Annotated[Services, Depends(get_services)],
    format: Literal["json", "csv", "zip"] = "zip",
) -> Response:
    return export_record(
        "prediction", prediction_id, services.predictions.get(prediction_id), format
    )
