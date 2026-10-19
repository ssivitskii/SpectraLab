"""Bounded, local-only I/O for the new final campaign protocol.

No input path can escape the caller's data root or traverse a symbolic link.
Numerical spectra are hashed and parsed from the same bytes, never reopened.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import stat
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np

MAX_JSON_BYTES = 16_000_000
MAX_SPECTRUM_BYTES = 128_000_000
MAX_ROWS = 2_000_000


def encoded(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def digest(value: Any) -> str:
    return hashlib.sha256(encoded(value)).hexdigest()


def unique_object(pairs: list[tuple[str, Any]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON field")
        result[key] = value
    return result


def local_name(name: str) -> bool:
    """Validate a portable relative path without inspecting the filesystem."""
    return (
        isinstance(name, str)
        and bool(name)
        and "\\" not in name
        and "\x00" not in name
        and not Path(name).is_absolute()
        and all(part not in {"", ".", ".."} for part in name.split("/"))
    )


def local_path(root: Path, name: str) -> Path:
    if not local_name(name):
        raise ValueError("Expected a normalized relative local path")
    root = Path(root).resolve(strict=True)
    current = root
    for part in name.split("/"):
        current /= part
        if current.is_symlink():
            raise ValueError("Symbolic links are not allowed in campaign inputs")
    return current


def regular_file(path: Path, limit: int) -> int:
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= limit:
        raise ValueError("Expected a nonempty bounded regular file")
    return info.st_size


def read_bytes(path: Path, limit: int) -> bytes:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= limit:
            raise ValueError("Expected a nonempty bounded regular file")
        raw = stream.read(limit + 1)
    if not 0 < len(raw) <= limit:
        raise ValueError("Input exceeds its size limit")
    return raw


def read_json(path: Path) -> dict:
    value = json.loads(read_bytes(path, MAX_JSON_BYTES), object_pairs_hook=unique_object)
    if not isinstance(value, dict):
        raise ValueError("Expected a JSON object")
    encoded(value)  # Also rejects NaN and Infinity accepted by json.loads.
    return value


def exclusive_json(path: Path, value: dict) -> None:
    raw = encoded(value) + b"\n"
    if len(raw) > MAX_JSON_BYTES:
        raise ValueError("JSON exceeds the size limit")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        parent = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(parent)
        finally:
            os.close(parent)


def verify_spectrum(
    root: Path,
    sample: dict,
    profile: dict,
    *,
    on_read: Callable[[], None] | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    descriptor = sample["local_spectrum"]
    raw = read_bytes(local_path(root, descriptor["path"]), MAX_SPECTRUM_BYTES)
    if on_read is not None:
        on_read()
    if hashlib.sha256(raw).hexdigest() != descriptor["sha256"]:
        raise ValueError("Numerical spectrum checksum differs from its frozen input")
    if sample["axis"]["medium"] == "air":
        original = read_bytes(
            local_path(root, sample["raw_spectrum"]["local_path"]), MAX_SPECTRUM_BYTES
        )
        if on_read is not None:
            on_read()
        if hashlib.sha256(original).hexdigest() != sample["input_sha256"]:
            raise ValueError("Original air spectrum checksum differs from its frozen input")
    delimiter = "," if descriptor["format"] == "csv" else "\t"
    try:
        reader = csv.reader(io.StringIO(raw.decode("utf-8-sig")), delimiter=delimiter, strict=True)
        if next(reader) != ["wavelength_nm", "intensity"]:
            raise ValueError("Numerical spectrum header must be wavelength_nm,intensity")
        values = []
        for row in reader:
            if len(row) != 2 or len(values) >= descriptor["rows"]:
                raise ValueError("Numerical spectrum has an unexpected row or column count")
            values.append([float(row[0]), float(row[1])])
        data = np.asarray(values, dtype=float)
    except (csv.Error, UnicodeError, StopIteration, OverflowError) as exc:
        raise ValueError("Invalid bounded numerical CSV/TSV spectrum") from exc
    if data.shape != (descriptor["rows"], 2) or not np.isfinite(data).all():
        raise ValueError("Finite numerical spectrum with the declared shape required")
    axis, intensity = data[:, 0], data[:, 1]
    if not np.all(np.diff(axis) > 0):
        raise ValueError("Wavelength axis must increase strictly without duplicate rows")
    validate_coverage(axis, intensity, profile)
    axis.setflags(write=False)
    intensity.setflags(write=False)
    return axis, intensity


def validate_coverage(axis: np.ndarray, intensity: np.ndarray, profile: dict) -> None:
    """Check another model's coverage contract on the same already verified arrays."""
    for lower, upper in profile["required_intervals_nm"]:
        if axis[0] > lower + 1e-9 or axis[-1] < upper - 1e-9:
            raise ValueError("Spectrum does not cover a required profile interval")
        left = max(0, int(np.searchsorted(axis, lower, side="right")) - 1)
        right = min(len(axis) - 1, int(np.searchsorted(axis, upper, side="left")))
        selected = axis[left : right + 1]
        if len(selected) < 2 or np.max(np.diff(selected)) > profile["max_sampling_step_nm"] + 1e-9:
            raise ValueError(
                "Spectrum has a gap or insufficient native sampling in a required interval"
            )
        if profile["require_positive_signal"] and not np.any(intensity[left : right + 1] > 0):
            raise ValueError("A required observed interval has no positive signal")
