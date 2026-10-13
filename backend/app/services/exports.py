from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import zipfile
from typing import Any, Literal

from fastapi.responses import Response

# Export scientific data, never deployment paths, checkpoints or executable objects.
PRIVATE_KEYS = {
    "path",
    "config_path",
    "checkpoint",
    "checkpoint_path",
    "artifact_path",
    "model_path",
    "reference_cache",
    "output_root",
    "project_root",
    "directory",
    "file_path",
    "cache_path",
}


def public_record(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: public_record(item)
            for key, item in value.items()
            if key not in PRIVATE_KEYS
            and (not key.endswith("_path") or key == "relative_path")
            and not key.endswith("_dir")
        }
    if isinstance(value, (tuple, list)):
        return [public_record(item) for item in value]
    if isinstance(value, str):
        if value.startswith(("/", "file://")) or re.match(r"^[a-zA-Z]:[\\/]", value):
            return "[local path omitted]"
        # Exceptions produced by a local experiment can embed a filename in a sentence.
        return re.sub(
            r"(?<![\w:/])/(?:Users|home|tmp|private|var|opt|etc|workspace|root|mnt|Volumes)"
            r"(?:/[^\s\"'<>]*)?",
            "[local path omitted]",
            value,
        )
    return value


def encode_json(record: Any) -> bytes:
    return (json.dumps(record, ensure_ascii=False, allow_nan=False, indent=2) + "\n").encode()


def spectrum_csv(record: dict[str, Any]) -> bytes:
    stream = io.StringIO(newline="")
    writer = csv.writer(stream)
    writer.writerow(["wavelength_nm", "intensity"])
    writer.writerows(zip(record["wavelength_nm"], record["intensity"], strict=True))
    return stream.getvalue().encode()


def export_record(
    kind: str,
    record_id: str,
    record: dict[str, Any],
    format: Literal["json", "csv", "zip"],
    related_records: dict[str, dict[str, Any]] | None = None,
) -> Response:
    safe = public_record(record)
    files = {"record.json": encode_json(safe)}
    for related_id, related in (related_records or {}).items():
        if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}", related_id):
            raise ValueError("Invalid related experiment identity")
        files[f"runs/{related_id}/record.json"] = encode_json(public_record(related))
    spectrum = safe.get("input", safe)
    if "wavelength_nm" in spectrum and "intensity" in spectrum:
        files["spectrum.csv"] = spectrum_csv(spectrum)
    manifest = {
        "schema_version": 1,
        "kind": kind,
        "record_id": record_id,
        "api_version": "1.1",
        "record_schema_version": safe.get("schema_version", 1),
        "reference_source": safe.get("reference_source"),
        "model_id": safe.get("model_id"),
        "model_version": safe.get("model_version"),
        "files": {
            name: {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}
            for name, data in files.items()
        },
        "checksum_scope": "exact UTF-8 file bytes; manifest is not self-hashed",
    }
    if format == "json":
        data = encode_json(
            {
                "manifest": manifest,
                "record": safe,
                "related_records": public_record(related_records or {}),
            }
        )
        media_type = "application/json"
    elif format == "csv":
        if "spectrum.csv" not in files:
            raise ValueError("CSV is available only for records containing a spectrum")
        data = files["spectrum.csv"]
        media_type = "text/csv"
    else:
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, content in files.items():
                archive.writestr(name, content)
            archive.writestr("manifest.json", encode_json(manifest))
        data = stream.getvalue()
        media_type = "application/zip"
    return Response(
        data,
        media_type=media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{kind}-{record_id}.{format}"',
            "X-Content-SHA256": hashlib.sha256(data).hexdigest(),
        },
    )
