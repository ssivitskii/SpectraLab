from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import uuid
from typing import Annotated, Any, Literal

import anyio.to_thread
import numpy as np
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import Response
from pydantic import ValidationError as PydanticValidationError
from spectralab_ml.data.measured_spectra import (
    METADATA_FIELDS,
    validate_measurement_metadata,
)
from spectralab_ml.inference.measured import predict_line_arrays
from spectralab_ml.preprocessing.pipeline import validate_spectrum_arrays

from app.api.dependencies import get_services
from app.schemas.api import (
    AxisMetadata,
    InstrumentProfileInfo,
    MeasuredPredictionFormRequest,
    MeasuredPredictionRequestJson,
    MeasuredPredictionResponse,
    MeasurementMetadataPayload,
)
from app.services.container import Services
from app.services.exports import export_measured_prediction, public_record

router = APIRouter(tags=["measured"])

MAX_CSV_BYTES = 16_000_000
MAX_POINTS = 100_000


def _check_csv_size(raw_bytes: bytes) -> None:
    if len(raw_bytes) > MAX_CSV_BYTES:
        raise HTTPException(
            413,
            {
                "code": "payload_too_large",
                "message": f"CSV input exceeds the {MAX_CSV_BYTES}-byte limit",
                "details": {"max_csv_bytes": MAX_CSV_BYTES},
            },
        )


def parse_and_validate_csv(
    raw_bytes: bytes, expected_sha256: str | None = None
) -> tuple[np.ndarray, np.ndarray, str]:
    if len(raw_bytes) > MAX_CSV_BYTES:
        raise ValueError("CSV input size limit exceeded")
    checksum = hashlib.sha256(raw_bytes).hexdigest()
    if expected_sha256 is not None and checksum != expected_sha256:
        raise ValueError("Measurement input checksum mismatch")
    try:
        reader = csv.reader(io.StringIO(raw_bytes.decode("utf-8-sig")), strict=True)
        header = next(reader, None)
        if header != ["wavelength_nm", "intensity"]:
            raise ValueError("CSV header must be exactly wavelength_nm,intensity")
        rows = []
        for row in reader:
            if len(row) != 2 or len(rows) >= MAX_POINTS:
                raise ValueError("Every CSV row needs two numbers; at most 100000 points")
            rows.append([float(value) for value in row])
        values = np.asarray(rows, dtype=float)
        if values.ndim != 2 or values.shape[1] != 2:
            raise ValueError("CSV needs at least two numerical rows")
        wave, intensity = values[:, 0], values[:, 1]
        validate_spectrum_arrays(wave, intensity, max_points=MAX_POINTS)
    except (UnicodeError, csv.Error, OverflowError) as exc:
        raise ValueError("Invalid finite UTF-8 two-column spectrum CSV") from exc
    return wave, intensity, checksum


def _execute_measured_sync(
    services: Services,
    profile_id: str,
    raw_bytes: bytes,
    metadata_dict: dict[str, Any],
    medium: str | None,
    units: str | None,
    instrument_id: str | None,
    measurement_id: str | None,
    physical_sample_id: str | None,
    family_id: str | None,
    session_id: str | None,
) -> dict[str, Any]:
    if metadata_dict:
        if set(metadata_dict) - METADATA_FIELDS:
            raise ValueError("Schema 1 measurement metadata with documented fields required")
        if "schema_version" in metadata_dict and metadata_dict["schema_version"] != 1:
            raise ValueError("Schema 1 measurement metadata with documented fields required")

    # Contradiction checks between top-level parameters and metadata_dict
    if (
        instrument_id
        and "instrument_id" in metadata_dict
        and instrument_id != metadata_dict["instrument_id"]
    ):
        raise ValueError("Contradictory instrument_id between request parameters and metadata")
    if (
        measurement_id
        and "measurement_id" in metadata_dict
        and measurement_id != metadata_dict["measurement_id"]
    ):
        raise ValueError("Contradictory measurement_id between request parameters and metadata")
    if (
        physical_sample_id
        and "physical_sample_id" in metadata_dict
        and physical_sample_id != metadata_dict["physical_sample_id"]
    ):
        raise ValueError("Contradictory physical_sample_id between request parameters and metadata")
    if family_id and "family_id" in metadata_dict and family_id != metadata_dict["family_id"]:
        raise ValueError("Contradictory family_id between request parameters and metadata")
    if session_id and "session_id" in metadata_dict and session_id != metadata_dict["session_id"]:
        raise ValueError("Contradictory session_id between request parameters and metadata")

    # Instrument identity must be explicitly provided by the caller
    inst_id = instrument_id or metadata_dict.get("instrument_id")
    if not inst_id or not isinstance(inst_id, str) or len(inst_id) > 128 or not inst_id.strip():
        raise ValueError("Explicit instrument_id is required; cannot be inferred from profile")

    # Validate profile in trusted registry
    model, profile, profile_sha = services.instrument_profiles.resolve_profile(profile_id)
    expected_inst = (
        profile.get("provenance", {}).get("instrument_id")
        or profile.get("instrument_id")
        or "chemcam-lanl-testbed"
    )
    if inst_id != expected_inst:
        raise ValueError(
            f"Measurement instrument differs from profile: expected '{expected_inst}', "
            f"got '{inst_id}'"
        )

    meas_id = (
        measurement_id or metadata_dict.get("measurement_id") or f"meas_{uuid.uuid4().hex[:12]}"
    )
    if not isinstance(meas_id, str) or len(meas_id) > 128:
        raise ValueError("measurement_id must be a string up to 128 characters")

    # Axis conventions: explicit axis metadata required from caller
    axis_in = metadata_dict.get("axis")
    if isinstance(axis_in, AxisMetadata):
        axis_data = axis_in.model_dump()
    elif isinstance(axis_in, dict):
        axis_data = dict(axis_in)
    else:
        raise ValueError(
            "Explicit axis metadata object required; axis evidence cannot be inferred from profile"
        )

    if medium and "medium" in axis_data and medium != axis_data["medium"]:
        raise ValueError("Contradictory medium between request parameters and axis metadata")

    axis_medium = medium or axis_data.get("medium")
    if not axis_medium or axis_medium not in ("air", "vacuum"):
        raise ValueError("Explicit axis medium ('air' or 'vacuum') is required")

    axis_units = axis_data.get("units")
    if axis_units != "nm":
        raise ValueError("Explicit axis.units 'nm' required; cannot be null or omitted")

    if units is not None and units != axis_units:
        raise ValueError("Contradictory units between request parameters and axis metadata")

    axis_source = axis_data.get("source")
    if not axis_source or not isinstance(axis_source, str) or not axis_source.strip():
        raise ValueError("Explicit axis.source is required; cannot be inferred from profile")

    axis_source_sha = axis_data.get("source_sha256")
    if (
        not axis_source_sha
        or not isinstance(axis_source_sha, str)
        or not re.fullmatch(r"[a-f0-9]{64}", axis_source_sha)
    ):
        raise ValueError(
            "Explicit 64-char hex axis.source_sha256 is required; cannot be inferred from profile"
        )

    ml_metadata: dict[str, Any] = {
        "schema_version": 1,
        "instrument_id": inst_id,
        "measurement_id": meas_id,
        "axis": {
            "units": axis_units,
            "medium": axis_medium,
            "source": axis_source,
            "source_sha256": axis_source_sha,
        },
    }
    sample_id = physical_sample_id or metadata_dict.get("physical_sample_id")
    if sample_id is not None:
        if not isinstance(sample_id, str) or len(sample_id) > 128:
            raise ValueError("physical_sample_id must be a string up to 128 characters")
        ml_metadata["physical_sample_id"] = sample_id
    fam_id = family_id or metadata_dict.get("family_id")
    if fam_id is not None:
        if not isinstance(fam_id, str) or len(fam_id) > 128:
            raise ValueError("family_id must be a string up to 128 characters")
        ml_metadata["family_id"] = fam_id
    sess_id = session_id or metadata_dict.get("session_id")
    if sess_id is not None:
        if not isinstance(sess_id, str) or len(sess_id) > 128:
            raise ValueError("session_id must be a string up to 128 characters")
        ml_metadata["session_id"] = sess_id
    if "input_sha256" in metadata_dict:
        ml_metadata["input_sha256"] = metadata_dict["input_sha256"]

    validate_measurement_metadata(ml_metadata)

    # Parse and validate CSV data
    wave, intensity, input_sha = parse_and_validate_csv(raw_bytes, ml_metadata.get("input_sha256"))

    # Execute measured inference
    result = predict_line_arrays(model, profile, wave, intensity, ml_metadata)
    result["profile_sha256"] = profile_sha
    result["profile_id"] = profile_id

    detected_elements = [
        item["element"] for item in result.get("predictions", []) if item.get("detected") is True
    ]
    result["detected_elements"] = detected_elements

    input_provenance = {
        "input_sha256": input_sha,
        "raw_csv_sha256": input_sha,
        "input_points": len(wave),
        "format": "csv",
        "metadata_sha256": hashlib.sha256(
            json.dumps(ml_metadata, sort_keys=True).encode("utf-8")
        ).hexdigest(),
    }
    result["input_provenance"] = input_provenance
    result["physical_sample_id"] = ml_metadata.get("physical_sample_id")
    result["family_id"] = ml_metadata.get("family_id")
    result["session_id"] = ml_metadata.get("session_id")
    result["measurement_metadata"] = public_record({**metadata_dict, **ml_metadata})

    # Save to storage (CSV first, JSON second under directory lock)
    stored = services.measured_predictions.save(
        result, profile_id, input_provenance, raw_csv_bytes=raw_bytes
    )
    return stored


@router.get("/instrument-profiles", response_model=list[InstrumentProfileInfo])
def list_instrument_profiles(
    services: Annotated[Services, Depends(get_services)],
) -> list[dict[str, Any]]:
    return services.instrument_profiles.list_profiles()


@router.get("/instrument-profiles/{profile_id}", response_model=InstrumentProfileInfo)
def get_instrument_profile(
    profile_id: str,
    services: Annotated[Services, Depends(get_services)],
) -> dict[str, Any]:
    return services.instrument_profiles.get_profile_info(profile_id)


@router.post(
    "/measured-predictions",
    response_model=MeasuredPredictionResponse,
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "multipart/form-data": {
                    "schema": {"$ref": "#/components/schemas/MeasuredPredictionFormRequest"}
                },
                "application/json": {
                    "schema": {"$ref": "#/components/schemas/MeasuredPredictionRequestJson"}
                },
            },
        }
    },
)
async def create_measured_prediction(
    request: Request,
    services: Annotated[Services, Depends(get_services)],
) -> dict[str, Any]:
    content_type = request.headers.get("content-type", "")
    profile_id = "calcium-v4"
    raw_bytes: bytes | None = None
    metadata: dict[str, Any] = {}
    medium: str | None = None
    units: str | None = None
    instrument_id: str | None = None
    measurement_id: str | None = None
    physical_sample_id: str | None = None
    family_id: str | None = None
    session_id: str | None = None

    if "multipart/form-data" in content_type:
        form = await request.form()
        file_field = form.get("file")
        if not file_field or not hasattr(file_field, "read"):
            raise ValueError("file (CSV upload) is required")
        try:
            raw_bytes = await file_field.read(MAX_CSV_BYTES + 1)
        finally:
            await file_field.close()
        _check_csv_size(raw_bytes)

        form_data: dict[str, Any] = {"file": raw_bytes}
        for k, v in form.items():
            if k != "file" and isinstance(v, str):
                form_data[k] = v

        try:
            req_model = MeasuredPredictionFormRequest.model_validate(form_data)
        except PydanticValidationError as exc:
            raise RequestValidationError(exc.errors()) from exc

        if req_model.profile_id and req_model.profile_id.strip():
            profile_id = req_model.profile_id.strip()
        medium = req_model.medium
        units = req_model.units
        instrument_id = req_model.instrument_id
        measurement_id = req_model.measurement_id
        physical_sample_id = req_model.physical_sample_id
        family_id = req_model.family_id
        session_id = req_model.session_id

        if req_model.metadata is not None:
            try:
                parsed_meta = json.loads(req_model.metadata)
            except Exception as exc:
                raise ValueError(f"Failed to parse metadata JSON: {exc}") from exc
            if not isinstance(parsed_meta, dict):
                raise ValueError("metadata must be a JSON object")
            try:
                validated_meta = MeasurementMetadataPayload.model_validate(parsed_meta)
            except PydanticValidationError as exc:
                raise RequestValidationError(exc.errors()) from exc
            metadata = validated_meta.model_dump(exclude_none=True)

    elif "application/json" in content_type:
        try:
            body = await request.json()
        except Exception as exc:
            raise ValueError(f"Invalid JSON request body: {exc}") from exc
        if not isinstance(body, dict):
            raise ValueError("JSON object body required")
        if isinstance(body.get("csv_content"), str):
            _check_csv_size(body["csv_content"].encode("utf-8"))

        try:
            json_model = MeasuredPredictionRequestJson.model_validate(body)
        except PydanticValidationError as exc:
            raise RequestValidationError(exc.errors()) from exc

        if json_model.profile_id and json_model.profile_id.strip():
            profile_id = json_model.profile_id.strip()
        medium = json_model.medium
        units = json_model.units
        instrument_id = json_model.instrument_id
        measurement_id = json_model.measurement_id
        physical_sample_id = json_model.physical_sample_id
        family_id = json_model.family_id
        session_id = json_model.session_id
        if json_model.metadata is not None:
            metadata = json_model.metadata.model_dump(exclude_none=True)

        if json_model.csv_content is not None:
            raw_bytes = json_model.csv_content.encode("utf-8")
        elif json_model.points is not None:
            lines = ["wavelength_nm,intensity\n"]
            for pt in json_model.points:
                lines.append(f"{pt.wavelength_nm!r},{pt.intensity!r}\n")
            raw_bytes = "".join(lines).encode("utf-8")
        elif json_model.wavelength_nm is not None and json_model.intensity is not None:
            lines = ["wavelength_nm,intensity\n"]
            for w, i in zip(json_model.wavelength_nm, json_model.intensity, strict=True):
                lines.append(f"{w!r},{i!r}\n")
            raw_bytes = "".join(lines).encode("utf-8")
    else:
        raise ValueError("Supported content types: multipart/form-data, application/json")

    if raw_bytes is None:
        raise ValueError("Raw CSV spectrum bytes required")
    _check_csv_size(raw_bytes)

    # Offload synchronous ML inference and storage I/O to a worker thread
    stored = await anyio.to_thread.run_sync(
        _execute_measured_sync,
        services,
        profile_id,
        raw_bytes,
        metadata,
        medium,
        units,
        instrument_id,
        measurement_id,
        physical_sample_id,
        family_id,
        session_id,
    )
    return stored


@router.get("/measured-predictions/{prediction_id}", response_model=MeasuredPredictionResponse)
def get_measured_prediction(
    prediction_id: str,
    services: Annotated[Services, Depends(get_services)],
) -> dict[str, Any]:
    return services.measured_predictions.get(prediction_id)


@router.get("/measured-predictions/{prediction_id}/export")
def export_measured_prediction_route(
    prediction_id: str,
    services: Annotated[Services, Depends(get_services)],
    format: Literal["json", "csv", "zip"] = Query(default="zip"),
) -> Response:
    record = services.measured_predictions.get(prediction_id)
    raw_csv = services.measured_predictions.get_raw_csv(prediction_id)
    return export_measured_prediction(prediction_id, record, raw_csv, format)
