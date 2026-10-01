from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.dependencies import get_services
from app.schemas.api import ExperimentDetail, ExperimentSummary
from app.services.container import Services

router = APIRouter(prefix="/experiments", tags=["experiments"])


@router.get("", response_model=list[ExperimentSummary])
def list_experiments(
    services: Annotated[Services, Depends(get_services)],
) -> list[dict[str, object]]:
    return services.experiments.list()


@router.get("/{run_id}", response_model=ExperimentDetail)
def get_experiment(
    run_id: str, services: Annotated[Services, Depends(get_services)]
) -> dict[str, object]:
    return services.experiments.get(run_id)
