from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Response

from app.api.dependencies import get_services
from app.schemas.api import ExperimentDetail, ExperimentPage, ExperimentSummary
from app.services.container import Services
from app.services.exports import export_record, public_record

router = APIRouter(prefix="/experiments", tags=["experiments"])


@router.get("", response_model=list[ExperimentSummary])
def list_experiments(
    services: Annotated[Services, Depends(get_services)],
) -> list[dict[str, object]]:
    return public_record(services.experiments.list())


@router.get("/catalog", response_model=ExperimentPage)
def experiment_catalog(
    services: Annotated[Services, Depends(get_services)],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    cursor: Annotated[str | None, Query(max_length=1024)] = None,
    status: Annotated[str | None, Query(max_length=200)] = None,
    protocol: Annotated[str | None, Query(max_length=200)] = None,
    reference_source: Annotated[str | None, Query(max_length=200)] = None,
) -> dict[str, object]:
    return public_record(
        services.experiments.page(
            limit=limit,
            cursor=cursor,
            status=status,
            protocol=protocol,
            reference_source=reference_source,
        )
    )


@router.get("/{run_id}", response_model=ExperimentDetail)
def get_experiment(
    run_id: str, services: Annotated[Services, Depends(get_services)]
) -> dict[str, object]:
    return public_record(services.experiments.get(run_id))


@router.get("/{run_id}/export")
def export_experiment(
    run_id: str,
    services: Annotated[Services, Depends(get_services)],
    format: Literal["json", "zip"] = "zip",
) -> Response:
    record = services.experiments.get(run_id)
    related = {}
    for child in record.get("seed_runs", []):
        child_id = child.get("run_id")
        if child_id:
            try:
                related[child_id] = services.experiments.get(child_id)
            except FileNotFoundError:
                # Missing/failed children remain explicitly represented in the series record.
                continue
    return export_record("experiment", run_id, record, format, related_records=related)
