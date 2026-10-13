from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from app.api.dependencies import get_services
from app.schemas.api import JobRequest, JobResponse
from app.services.container import Services
from app.services.jobs import IdempotencyConflict

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.get("", response_model=list[JobResponse])
def list_jobs(services: Annotated[Services, Depends(get_services)]) -> list[dict[str, object]]:
    return services.jobs.list()


@router.post("", response_model=JobResponse, status_code=202)
def create_job(
    request: JobRequest, services: Annotated[Services, Depends(get_services)]
) -> dict[str, object]:
    try:
        return services.jobs.create(request.model_dump())
    except IdempotencyConflict as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get("/{job_id}", response_model=JobResponse)
def get_job(job_id: str, services: Annotated[Services, Depends(get_services)]) -> dict[str, object]:
    return services.jobs.get(job_id)


@router.post("/{job_id}/cancel", response_model=JobResponse)
def cancel_job(
    job_id: str, services: Annotated[Services, Depends(get_services)]
) -> dict[str, object]:
    return services.jobs.cancel(job_id)
