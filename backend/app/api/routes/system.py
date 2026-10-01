from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.dependencies import get_services
from app.schemas.api import ElementsInfo, ModelInfo
from app.services.container import Services

router = APIRouter(tags=["system"])


@router.get("/health")
def health(services: Annotated[Services, Depends(get_services)]) -> dict[str, object]:
    models = services.registry.list_models()
    return {
        "status": "ok",
        "reference_ready": bool(services.catalog.lines),
        "ready_models": [model["model_id"] for model in models if model["ready"]],
    }


@router.get("/elements", response_model=ElementsInfo)
def elements(services: Annotated[Services, Depends(get_services)]) -> dict[str, object]:
    return {
        "elements": list(services.project.elements),
        "ion_stage": services.project.ion_stage,
        "reference": services.catalog.metadata,
        "line_counts": {
            element: sum(1 for line in services.catalog.lines if line.element == element)
            for element in services.project.elements
        },
    }


@router.get("/models", response_model=list[ModelInfo])
def models(services: Annotated[Services, Depends(get_services)]) -> list[dict[str, object]]:
    return services.registry.list_models()
