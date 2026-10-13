"""Admission checks for locked real-measurement test manifests.

Ambient atmosphere does not establish the wavelength convention. Missing labels
are unknown, never negative classes. This module never fits or selects thresholds.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import numpy as np


def inspect_external_test(manifest: dict[str, Any], data_dir: Path) -> dict[str, Any]:
    if manifest.get("schema_version") != 1:
        raise ValueError("External test manifest schema must be 1")
    samples = manifest.get("samples", [])
    if not samples:
        raise ValueError("External test must contain samples")
    ids = [sample["sample_id"] for sample in samples]
    if len(ids) != len(set(ids)):
        raise ValueError("External sample IDs must be unique")
    development = set(manifest.get("development_sample_ids", []))
    if development & set(ids):
        raise ValueError("Physical samples overlap development and external test")
    sessions = {sample["session_id"] for sample in samples}
    if sessions & set(manifest.get("development_session_ids", [])):
        raise ValueError("Acquisition sessions overlap development and external test")
    blockers = []
    if not manifest.get("license_id") or not manifest.get("license_source"):
        blockers.append("usage_rights_not_confirmed")
    if manifest.get("wavelength_medium") not in {"air", "vacuum"}:
        blockers.append("wavelength_medium_not_documented")
    if not manifest.get("wavelength_medium_source"):
        blockers.append("wavelength_medium_evidence_missing")
    if manifest.get("units") != "nm":
        blockers.append("units_not_nm")
    diagnostics = []
    for sample in samples:
        path = data_dir / sample["filename"]
        if path.resolve().parent != data_dir.resolve() or path.is_symlink():
            raise ValueError("Unsafe measurement filename")
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != sample["sha256"]:
            raise ValueError("Measurement checksum mismatch")
        data = np.loadtxt(path, delimiter=",", skiprows=1)
        if data.ndim != 2 or data.shape[1] != 2 or not np.isfinite(data).all():
            raise ValueError("Real spectra must contain finite wavelength/intensity pairs")
        if not np.all(np.diff(data[:, 0]) > 0):
            raise ValueError("Real wavelength grid must be strictly increasing")
        diagnostics.append(
            {
                "sample_id": sample["sample_id"],
                "session_id": sample["session_id"],
                "points": len(data),
                "start_nm": float(data[0, 0]),
                "end_nm": float(data[-1, 0]),
                "max_sampling_gap_nm": float(np.diff(data[:, 0]).max()),
                "unknown_labels": [e for e, v in sample["labels"].items() if v is None],
            }
        )
    return {
        "schema_version": 1,
        "status": "blocked" if blockers else "admitted",
        "blockers": blockers,
        "samples": diagnostics,
        "metrics": None,
        "test_locked_before_prediction": manifest.get("test_locked_before_prediction", False),
        "source": manifest.get("source"),
        "model_fitted": False,
        "limitations": "Admission is not validation. No test-based alignment or threshold fitting.",
    }
