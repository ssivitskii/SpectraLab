from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile

from app.api.dependencies import get_services
from app.schemas.api import BatchFile, BatchRejection, BatchRequest, BatchResponse
from app.services.batch_exports import export_batch
from app.services.batches import model_snapshot
from app.services.container import Services
from app.services.exports import public_record
from app.services.jobs import IdempotencyConflict

router = APIRouter(prefix="/batches", tags=["batches"])


def conflict(exc: IdempotencyConflict) -> HTTPException:
    return HTTPException(409, str(exc))


@router.get("", response_model=list[BatchResponse])
def list_batches(services: Annotated[Services, Depends(get_services)]) -> list[dict]:
    return public_record(services.batches.list())


@router.post("", response_model=BatchResponse, status_code=201)
def create_batch(
    request: BatchRequest, services: Annotated[Services, Depends(get_services)]
) -> dict:
    # A retried create returns its original pinned identities, even after artifacts change.
    existing = next(
        (
            item
            for item in services.batches.list()
            if item["idempotency_key"] == request.idempotency_key
        ),
        None,
    )
    try:
        snapshots = (
            existing["model_snapshots"]
            if existing
            else {model_id: model_snapshot(services, model_id) for model_id in request.model_ids}
        )
        return public_record(services.batches.create(request.model_dump(), snapshots))
    except IdempotencyConflict as exc:
        raise conflict(exc) from exc
    except (FileNotFoundError, ImportError) as exc:
        raise HTTPException(422, "Choose ready model IDs from /models") from exc


@router.get("/{batch_id}", response_model=BatchResponse)
def get_batch(batch_id: str, services: Annotated[Services, Depends(get_services)]) -> dict:
    return public_record(services.batches.get(batch_id))


@router.post("/{batch_id}/files", response_model=BatchFile)
async def upload_batch_file(
    batch_id: str,
    file: Annotated[UploadFile, File()],
    relative_path: Annotated[str, Form()],
    services: Annotated[Services, Depends(get_services)],
    units: Annotated[str, Form()] = "nm",
    wavelength_medium: Annotated[str, Form()] = "vacuum",
) -> dict:
    if units != "nm" or wavelength_medium != "vacuum":
        raise HTTPException(422, "Only vacuum wavelengths in nm are accepted")
    raw = await file.read(services.settings.max_upload_bytes + 1)
    if len(raw) > services.settings.max_upload_bytes:
        raise HTTPException(413, "CSV file exceeds the upload size limit")
    try:
        return public_record(services.batches.upload(batch_id, relative_path, raw, services))
    except IdempotencyConflict as exc:
        raise conflict(exc) from exc


@router.post("/{batch_id}/rejections", response_model=BatchFile)
def reject_batch_file(
    batch_id: str, request: BatchRejection, services: Annotated[Services, Depends(get_services)]
) -> dict:
    try:
        return public_record(services.batches.reject(batch_id, request.model_dump()))
    except IdempotencyConflict as exc:
        raise conflict(exc) from exc


@router.post("/{batch_id}/start", response_model=BatchResponse, status_code=202)
def start_batch(batch_id: str, services: Annotated[Services, Depends(get_services)]) -> dict:
    try:
        return public_record(services.batches.start(batch_id))
    except IdempotencyConflict as exc:
        raise conflict(exc) from exc


@router.post("/{batch_id}/resume", response_model=BatchResponse, status_code=202)
def resume_batch(batch_id: str, services: Annotated[Services, Depends(get_services)]) -> dict:
    try:
        return public_record(services.batches.start(batch_id, resume=True))
    except IdempotencyConflict as exc:
        raise conflict(exc) from exc


@router.post("/{batch_id}/cancel", response_model=BatchResponse)
def cancel_batch(batch_id: str, services: Annotated[Services, Depends(get_services)]) -> dict:
    return public_record(services.batches.cancel(batch_id))


@router.get("/{batch_id}/export")
def batch_export(
    batch_id: str,
    services: Annotated[Services, Depends(get_services)],
    format: Literal["json", "csv", "zip"] = "zip",
) -> Response:
    return export_batch(services.batches, batch_id, format)
