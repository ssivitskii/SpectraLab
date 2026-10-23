"""Explicit identities and spectral coverage for a single-measurement shot series."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np

from spectralab_ml.data.measured_spectra import read_bounded, validate_measurement_metadata

GROUP_FIELDS = ("physical_sample_id", "family_id", "session_id")


def read_series_metadata(path: Path) -> tuple[dict, str]:
    """Validate and hash the same bounded metadata bytes, rejecting duplicate keys."""
    raw = read_bounded(path, 1_000_000)

    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError(f"Duplicate series metadata key: {key}")
            value[key] = item
        return value

    try:
        metadata = json.loads(raw, object_pairs_hook=unique)
        json.dumps(metadata, allow_nan=False)
    except (UnicodeError, RecursionError) as exc:
        raise ValueError("Finite bounded UTF-8 series metadata required") from exc
    validate_series_metadata(metadata)
    return metadata, hashlib.sha256(raw).hexdigest()


def validate_series_metadata(metadata: dict) -> None:
    """Extend measured-spectrum metadata with mandatory groups and band geometry."""
    validate_measurement_metadata(metadata)
    for key in (*GROUP_FIELDS, "measurement_id", "instrument_id"):
        value = metadata.get(key)
        if not isinstance(value, str) or not value.strip() or value != value.strip():
            raise ValueError(f"Explicit, nonblank, trimmed {key} required for a series")
    if "input_sha256" not in metadata:
        raise ValueError("Pinned input_sha256 required for a shot series")
    axis = metadata["axis"]
    if set(axis) != {"units", "medium", "source", "source_sha256", "bands_nm", "max_step_nm"}:
        raise ValueError("Series axis requires documented bands_nm and max_step_nm fields")
    step = axis["max_step_nm"]
    if (
        isinstance(step, bool)
        or not isinstance(step, (float, int))
        or not np.isfinite(step)
        or step <= 0
    ):
        raise ValueError("max_step_nm must be finite and positive")
    bands = axis["bands_nm"]
    if not isinstance(bands, list) or not 1 <= len(bands) <= 32:
        raise ValueError("bands_nm must contain 1..32 ordered, disjoint wavelength bands")
    previous_end = 0.0
    for band in bands:
        if (
            not isinstance(band, list)
            or len(band) != 2
            or any(isinstance(v, bool) or not isinstance(v, (int, float)) for v in band)
            or not np.isfinite(band).all()
            or not 0 < band[0] < band[1]
            or band[0] <= previous_end
        ):
            raise ValueError("bands_nm must contain positive ordered, disjoint intervals")
        previous_end = band[1]


def validate_series_coverage(wavelength_nm: np.ndarray, metadata: dict) -> list[dict]:
    """Require every declared band, preserving inter-band gaps without interpolation."""
    validate_series_metadata(metadata)
    wave = np.asarray(wavelength_nm, dtype=float)
    if (
        wave.ndim != 1
        or len(wave) < 2
        or not np.isfinite(wave).all()
        or not np.all(np.diff(wave) > 0)
    ):
        raise ValueError("Coverage requires a finite increasing wavelength axis")
    step = metadata["axis"]["max_step_nm"]
    covered = np.zeros(len(wave), dtype=bool)
    reports = []
    for start, end in metadata["axis"]["bands_nm"]:
        mask = (wave >= start) & (wave <= end)
        band = wave[mask]
        if (
            len(band) < 2
            or band[0] - start > step
            or end - band[-1] > step
            or np.max(np.diff(band)) > step * (1 + 1e-10)
        ):
            raise ValueError(f"Insufficient coverage or internal gap in band {start:g}..{end:g} nm")
        covered |= mask
        reports.append({"band_nm": [start, end], "points": len(band)})
    if not covered.all():
        raise ValueError("Input points outside the declared spectral bands")
    return reports


def validate_series_splits(splits: dict[str, list[dict]]) -> None:
    """Reject repeated measurements and group leakage before fitting any model.

    IDs must already be canonicalised by the intake; this helper does not infer
    aliases or certify independence from an external previously-seen registry.
    """
    if not isinstance(splits, dict) or len(splits) < 2:
        raise ValueError("At least two named series splits required")
    owners: dict[tuple[str, str], str] = {}
    measurements: set[str] = set()
    for split, records in splits.items():
        if not isinstance(split, str) or not split.strip() or not isinstance(records, list):
            raise ValueError("Series splits require names and lists of metadata")
        for metadata in records:
            validate_series_metadata(metadata)
            measurement_id = metadata["measurement_id"]
            if measurement_id in measurements:
                raise ValueError(f"Duplicate measurement_id: {measurement_id}")
            measurements.add(measurement_id)
            for key in GROUP_FIELDS:
                group = (key, metadata[key])
                if group in owners and owners[group] != split:
                    raise ValueError(f"Series split overlap in {key}: {metadata[key]}")
                owners[group] = split
