"""Bounded two-column measured-spectrum input with explicit axis provenance."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
import stat
from pathlib import Path

import numpy as np

from spectralab_ml.preprocessing.pipeline import validate_spectrum_arrays

MAX_BYTES = 16_000_000
MAX_POINTS = 100_000
METADATA_FIELDS = {
    "schema_version",
    "measurement_id",
    "physical_sample_id",
    "family_id",
    "session_id",
    "instrument_id",
    "axis",
    "input_sha256",
}


def read_bounded(path: Path, limit: int = MAX_BYTES) -> bytes:
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= limit:
            raise ValueError("Input must be a nonempty bounded regular file")
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise ValueError("Input size limit exceeded")
    return raw


def validate_measurement_metadata(value: dict) -> None:
    if (
        not isinstance(value, dict)
        or set(value) - METADATA_FIELDS
        or type(value.get("schema_version")) is not int
        or value["schema_version"] != 1
    ):
        raise ValueError("Schema 1 measurement metadata with documented fields required")
    for key in ("instrument_id", "measurement_id"):
        if not isinstance(value.get(key), str) or not value[key].strip():
            raise ValueError("Explicit instrument_id and measurement_id required")
    axis = value.get("axis")
    if (
        not isinstance(axis, dict)
        or axis.get("units") != "nm"
        or axis.get("medium") not in ("air", "vacuum")
        or not isinstance(axis.get("source"), str)
        or not axis["source"].strip()
        or not isinstance(axis.get("source_sha256"), str)
        or not re.fullmatch(r"[a-f0-9]{64}", axis["source_sha256"])
    ):
        raise ValueError("Explicit nm air/vacuum convention and its source/hash required")
    if "input_sha256" in value and (
        not isinstance(value["input_sha256"], str)
        or not re.fullmatch(r"[a-f0-9]{64}", value["input_sha256"])
    ):
        raise ValueError("input_sha256 must be lowercase SHA-256")


def read_measurement_metadata(path: Path) -> tuple[dict, str]:
    raw = read_bounded(path, 1_000_000)

    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate measurement metadata key")
            result[key] = value
        return result

    try:
        value = json.loads(raw, object_pairs_hook=unique)
        json.dumps(value, allow_nan=False)
    except (UnicodeError, RecursionError) as exc:
        raise ValueError("Finite bounded UTF-8 measurement metadata required") from exc
    validate_measurement_metadata(value)
    return value, hashlib.sha256(raw).hexdigest()


def read_measured_csv(
    path: Path, *, expected_sha256: str | None = None
) -> tuple[np.ndarray, np.ndarray, dict]:
    raw = read_bounded(path)
    checksum = hashlib.sha256(raw).hexdigest()
    if expected_sha256 is not None and checksum != expected_sha256:
        raise ValueError("Measurement input checksum mismatch")
    try:
        reader = csv.reader(io.StringIO(raw.decode("utf-8-sig")), strict=True)
        if next(reader, None) != ["wavelength_nm", "intensity"]:
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
    return (
        wave,
        intensity,
        {
            "input_sha256": checksum,
            "input_points": len(wave),
            "aggregation": "one_measurement_not_independent_shots",
        },
    )
