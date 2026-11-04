"""Service layer for LIBS shot-series processing, quality control and storage."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import tempfile
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np
from spectralab_ml.data.chemcam_measurements import read_chemcam_ccs_shots
from spectralab_ml.data.measured_spectra import METADATA_FIELDS
from spectralab_ml.data.series_metadata import (
    validate_series_coverage,
    validate_series_metadata,
)
from spectralab_ml.data.shot_series import ShotSeriesPolicy, aggregate_shot_series

from app.services.storage import now_iso

if TYPE_CHECKING:
    from app.services.container import Services

MAX_SERIES_CSV_BYTES = 16_000_000


def _csv_bytes(wavelength: np.ndarray, values: np.ndarray, header: str) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(["wavelength_nm", header])
    writer.writerows(zip(wavelength, values, strict=True))
    return output.getvalue().encode("utf-8")


def analyze_shot_series_service(
    raw_csv_bytes: bytes,
    metadata_dict: dict[str, Any] | None,
    *,
    discard_first_shots: int = 5,
    policy: ShotSeriesPolicy,
    services: Services,
) -> dict[str, Any]:
    if len(raw_csv_bytes) > MAX_SERIES_CSV_BYTES:
        raise ValueError(f"CSV file exceeds the {MAX_SERIES_CSV_BYTES}-byte limit")
    if not raw_csv_bytes:
        raise ValueError("CSV file is empty")

    input_sha256 = hashlib.sha256(raw_csv_bytes).hexdigest()

    # Read shots using temporary file with read_chemcam_ccs_shots
    with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as tmp:
        tmp_path = Path(tmp.name)
        tmp.write(raw_csv_bytes)

    try:
        wave, shots, source_info = read_chemcam_ccs_shots(tmp_path, input_sha256)
    finally:
        tmp_path.unlink(missing_ok=True)

    # Process and validate metadata
    if metadata_dict is not None:
        meta = {k: v for k, v in metadata_dict.items() if k in METADATA_FIELDS}
        meta["schema_version"] = 1
        meta["input_sha256"] = input_sha256
        if "axis" not in meta or not isinstance(meta["axis"], dict):
            meta["axis"] = {}
        axis = meta["axis"]
        if "units" not in axis:
            axis["units"] = "nm"
        if "medium" not in axis:
            axis["medium"] = "vacuum"
        if "source" not in axis:
            axis["source"] = "Uploaded shot series"
        if "source_sha256" not in axis:
            axis["source_sha256"] = input_sha256
        if "bands_nm" not in axis:
            axis["bands_nm"] = [[float(wave[0]), float(wave[-1])]]
        if "max_step_nm" not in axis:
            axis["max_step_nm"] = float(np.max(np.diff(wave)) * 1.05)
        for key in (
            "physical_sample_id",
            "family_id",
            "session_id",
            "measurement_id",
            "instrument_id",
        ):
            if key not in meta or not meta[key]:
                meta[key] = f"unspecified-{key}"
        validate_series_metadata(meta)
    else:
        meta = {
            "schema_version": 1,
            "measurement_id": f"series-{uuid.uuid4().hex[:8]}",
            "physical_sample_id": "unspecified-sample",
            "family_id": "unspecified-family",
            "session_id": "unspecified-session",
            "instrument_id": "unspecified-instrument",
            "input_sha256": input_sha256,
            "axis": {
                "units": "nm",
                "medium": "vacuum",
                "source": "Uploaded shot series",
                "source_sha256": input_sha256,
                "bands_nm": [[float(wave[0]), float(wave[-1])]],
                "max_step_nm": float(np.max(np.diff(wave)) * 1.05),
            },
        }
        validate_series_metadata(meta)

    coverage = validate_series_coverage(wave, meta)
    metadata_sha256 = hashlib.sha256(
        json.dumps(meta, sort_keys=True, allow_nan=False).encode("utf-8")
    ).hexdigest()

    if discard_first_shots < 0:
        raise ValueError("discard_first_shots must be nonnegative")
    if discard_first_shots >= len(shots):
        raise ValueError("discard_first_shots must leave at least one shot")

    # Run quality control and aggregation
    result = aggregate_shot_series(wave, shots[discard_first_shots:], policy=policy)

    series_id = uuid.uuid4().hex
    created_at = now_iso()

    receipt = {
        **result.receipt,
        "series_id": series_id,
        "created_at": created_at,
        "workflow": "chemcam-shot-series-v1",
        "measurement": meta,
        "metadata_sha256": metadata_sha256,
        "source": source_info,
        "coverage": coverage,
        "discard_first_shots": discard_first_shots,
        "shot_number_convention": "one_based_original_CCS_column",
        "discarded_shot_numbers": list(range(1, discard_first_shots + 1)),
        "accepted_shot_numbers": [
            i + discard_first_shots + 1 for i in result.receipt["accepted_shot_indices"]
        ],
        "outputs": {},
    }

    for shot in receipt["shots"]:
        shot["shot_number"] = shot["shot_index"] + discard_first_shots + 1

    files: dict[str, bytes] = {}
    aggregate_points: list[dict[str, float]] | None = None

    if result.intensity is not None and result.pointwise_mad is not None:
        files["aggregate.csv"] = _csv_bytes(wave, result.intensity, "intensity")
        files["dispersion.csv"] = _csv_bytes(wave, result.pointwise_mad, "intensity_mad")
        aggregate_metadata = {
            **meta,
            "axis": {
                key: value
                for key, value in meta["axis"].items()
                if key not in {"bands_nm", "max_step_nm"}
            },
            "input_sha256": hashlib.sha256(files["aggregate.csv"]).hexdigest(),
        }
        files["metadata.json"] = (
            json.dumps(aggregate_metadata, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
        ).encode("utf-8")

        aggregate_points = [
            {
                "wavelength_nm": float(w),
                "intensity": float(inten),
                "intensity_mad": float(mad),
            }
            for w, inten, mad in zip(wave, result.intensity, result.pointwise_mad, strict=True)
        ]

    receipt["outputs"] = {
        name: {"sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)}
        for name, raw in files.items()
    }

    # Store in durable series storage
    services.series.save(series_id, receipt, files, raw_csv=raw_csv_bytes)

    # Return response payload
    return {
        **receipt,
        "aggregate": aggregate_points,
    }
