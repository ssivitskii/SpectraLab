from __future__ import annotations

import functools
import json
from typing import Annotated, Any

import anyio.to_thread
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError as PydanticValidationError
from spectralab_ml.data.shot_series import ShotSeriesPolicy

from app.api.dependencies import get_services
from app.schemas.api import (
    SeriesAnalysisFormRequest,
    SeriesAnalysisRequestJson,
    SeriesAnalysisResponse,
    SeriesListResponse,
)
from app.services.container import Services
from app.services.series import MAX_SERIES_CSV_BYTES, analyze_shot_series_service

router = APIRouter(prefix="/series", tags=["series"])


def _check_csv_size(raw_bytes: bytes) -> None:
    if len(raw_bytes) > MAX_SERIES_CSV_BYTES:
        raise HTTPException(
            413,
            {
                "code": "payload_too_large",
                "message": f"CSV input exceeds the {MAX_SERIES_CSV_BYTES}-byte limit",
                "details": {"max_csv_bytes": MAX_SERIES_CSV_BYTES},
            },
        )


@router.post(
    "/analyze",
    response_model=SeriesAnalysisResponse,
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "multipart/form-data": {
                    "schema": {"$ref": "#/components/schemas/SeriesAnalysisFormRequest"}
                },
                "application/json": {
                    "schema": {"$ref": "#/components/schemas/SeriesAnalysisRequestJson"}
                },
            },
        }
    },
)
async def analyze_series(
    request: Request,
    services: Annotated[Services, Depends(get_services)],
) -> dict[str, Any]:
    content_type = request.headers.get("content-type", "")
    raw_bytes: bytes | None = None
    metadata_dict: dict[str, Any] | None = None
    discard_first_shots = 5
    min_snr = 3.0
    min_accepted_shots = 2
    saturation_ceiling: float | None = None
    outlier_mad_z = 6.0

    if "multipart/form-data" in content_type:
        form = await request.form()
        file_field = form.get("file")
        if not file_field or not hasattr(file_field, "read"):
            raise ValueError("file (CSV upload) is required")
        try:
            raw_bytes = await file_field.read(MAX_SERIES_CSV_BYTES + 1)
        finally:
            await file_field.close()
        _check_csv_size(raw_bytes)

        form_data: dict[str, Any] = {"file": raw_bytes}
        for k, v in form.items():
            if k != "file" and isinstance(v, str):
                form_data[k] = v

        try:
            req_model = SeriesAnalysisFormRequest.model_validate(form_data)
        except PydanticValidationError as exc:
            raise RequestValidationError(exc.errors()) from exc

        discard_first_shots = req_model.discard_first_shots
        min_snr = req_model.min_snr
        min_accepted_shots = req_model.min_accepted_shots
        saturation_ceiling = req_model.saturation_ceiling
        outlier_mad_z = req_model.outlier_mad_z

        if req_model.metadata:
            try:
                parsed = json.loads(req_model.metadata)
                if not isinstance(parsed, dict):
                    raise ValueError("metadata must be a JSON object")
                metadata_dict = parsed
            except json.JSONDecodeError as exc:
                raise ValueError(f"Failed to parse metadata JSON: {exc}") from exc

    elif "application/json" in content_type:
        try:
            body = await request.json()
            req_model = SeriesAnalysisRequestJson.model_validate(body)
        except PydanticValidationError as exc:
            raise RequestValidationError(exc.errors()) from exc
        except Exception as exc:
            raise ValueError(f"Invalid JSON request body: {exc}") from exc

        raw_bytes = req_model.csv_content.encode("utf-8")
        _check_csv_size(raw_bytes)
        metadata_dict = req_model.metadata
        discard_first_shots = req_model.discard_first_shots
        min_snr = req_model.min_snr
        min_accepted_shots = req_model.min_accepted_shots
        saturation_ceiling = req_model.saturation_ceiling
        outlier_mad_z = req_model.outlier_mad_z

    else:
        raise HTTPException(
            415,
            {
                "code": "unsupported_media_type",
                "message": "Content-Type must be multipart/form-data or application/json",
            },
        )

    assert raw_bytes is not None

    policy = ShotSeriesPolicy(
        min_snr=min_snr,
        min_accepted_shots=min_accepted_shots,
        saturation_ceiling=saturation_ceiling,
        outlier_mad_z=outlier_mad_z,
    )
    policy.validate()

    return await anyio.to_thread.run_sync(
        functools.partial(
            analyze_shot_series_service,
            raw_bytes,
            metadata_dict,
            discard_first_shots=discard_first_shots,
            policy=policy,
            services=services,
        )
    )


@router.get("", response_model=SeriesListResponse)
async def list_series(
    services: Annotated[Services, Depends(get_services)],
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    items, total = services.series.list(limit=limit, offset=offset)
    return {"items": items, "total": total}


@router.get("/{series_id}", response_model=SeriesAnalysisResponse)
async def get_series(
    series_id: str,
    services: Annotated[Services, Depends(get_services)],
) -> dict[str, Any]:
    return services.series.get(series_id)


@router.get("/{series_id}/files/{filename}")
async def get_series_file(
    series_id: str,
    filename: str,
    services: Annotated[Services, Depends(get_services)],
) -> Response:
    content = services.series.get_file(series_id, filename)
    media_type = "text/csv" if filename.endswith(".csv") else "application/json"
    return Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.delete("/{series_id}")
async def delete_series(
    series_id: str,
    services: Annotated[Services, Depends(get_services)],
) -> dict[str, Any]:
    success = services.series.delete(series_id)
    if not success:
        raise HTTPException(
            404,
            {
                "code": "not_found",
                "message": f"Series '{series_id}' was not found",
            },
        )
    return {"success": True, "series_id": series_id}
