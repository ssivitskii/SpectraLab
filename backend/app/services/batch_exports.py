from __future__ import annotations

import csv
import hashlib
import io
import json
import zipfile
from typing import Literal

from fastapi.responses import Response

from app.schemas.api import BatchResponse
from app.services.batches import BatchStorage
from app.services.exports import encode_json, public_record


def csv_literal(value: object) -> object:
    # Filenames and measurement metadata are user text, not spreadsheet formulas.
    if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


def export_batch(
    storage: BatchStorage, batch_id: str, format: Literal["json", "csv", "zip"]
) -> Response:
    record = public_record(BatchResponse.model_validate(storage.get(batch_id)).model_dump())
    by_id = {file["file_id"]: file for file in record["files"]}
    stream = io.StringIO(newline="")
    fields = [
        "batch_id",
        "file_id",
        "relative_path",
        "input_sha256",
        "size_bytes",
        "spectrum_id",
        "sample_id",
        "instrument_id",
        "session_id",
        "validation_origin",
        "model_id",
        "model_identity",
        "model_version",
        "reference_source",
        "status",
        "prediction_id",
        "cache_hit",
        "score_kind",
        "calibrated",
        "detected_elements",
        "scores",
        "thresholds",
        "warnings",
        "error_code",
        "error_message",
    ]
    writer = csv.DictWriter(stream, fieldnames=fields)
    writer.writeheader()
    for result in record["results"]:
        file = by_id[result["file_id"]]
        prediction = result["prediction"] or {}
        snapshot = record["model_snapshots"][result["model_id"]]
        row = {
            "batch_id": batch_id,
            "file_id": file["file_id"],
            "relative_path": file["relative_path"],
            "input_sha256": file["sha256"],
            "size_bytes": file["size_bytes"],
            "spectrum_id": file["spectrum_id"],
            **{
                key: file["measurement_metadata"].get(key)
                for key in ("sample_id", "instrument_id", "session_id")
            },
            "validation_origin": file["validation_origin"],
            "model_id": result["model_id"],
            "model_identity": result["model_identity"],
            "model_version": snapshot["model_version"],
            "reference_source": snapshot["reference_source"],
            "status": result["status"],
            "prediction_id": result["prediction_id"],
            "cache_hit": result["cache_hit"],
            "score_kind": prediction.get("score_kind"),
            "calibrated": prediction.get("calibrated"),
            **{
                key: json.dumps(prediction.get(key), ensure_ascii=False)
                for key in ("detected_elements", "scores", "thresholds", "warnings")
            },
            "error_code": result["error_code"],
            "error_message": result["error_message"],
        }
        writer.writerow({key: csv_literal(value) for key, value in row.items()})
    files = {"report.json": encode_json(record), "summary.csv": stream.getvalue().encode()}
    for file in record["files"]:
        if file["status"] == "ready":
            files[f"inputs/{file['file_id']}.csv"] = storage.input_bytes(file)
    manifest = {
        "schema_version": 1,
        "kind": "csv_batch",
        "batch_id": batch_id,
        "status": record["status"],
        "model_identities": record["model_identities"],
        "files": {
            name: {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}
            for name, data in files.items()
        },
        "checksum_scope": "exact file bytes; original CSV bytes are not resampled",
        "limitations": "Synthetic model inference; not validation of real sample composition",
    }
    if format == "csv":
        data, media_type = files["summary.csv"], "text/csv"
    elif format == "json":
        data, media_type = encode_json({"manifest": manifest, "record": record}), "application/json"
    else:
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, content in files.items():
                archive.writestr(name, content)
            archive.writestr("manifest.json", encode_json(manifest))
        data, media_type = output.getvalue(), "application/zip"
    return Response(
        data,
        media_type=media_type,
        headers={
            "Content-Disposition": f'attachment; filename="batch-{batch_id}.{format}"',
            "X-Content-SHA256": hashlib.sha256(data).hexdigest(),
        },
    )
