"""Read a checksum-pinned ChemCam CCS measurement without treating shots as samples.

The CSV declares NSHOTS in a comment and names its columns in ``# wave,shot1,...``.
Provider mean/median columns are validated but never used. Spectrometer gaps are
preserved; the observation-window adapter must reject interpolation across them.
No wavelength medium, specimen identity, composition or uncertainty is inferred.
"""

from __future__ import annotations

import csv
import hashlib
import io
import re
from pathlib import Path

import numpy as np

from spectralab_ml.data.shot_series import MAX_SERIES_VALUES

MAX_CCS_BYTES = 16_000_000
MAX_CCS_POINTS = 100_000
MAX_CCS_SHOTS = 1000


def read_chemcam_ccs_shots(
    path: Path | str, expected_sha256: str
) -> tuple[np.ndarray, np.ndarray, dict[str, int | str | bool]]:
    """Return the raw retained-order shots, without treating them as samples.

    The returned matrix has shape ``(shot_count, wavelength_count)``. NSHOTS
    must precede the column header. Numeric rows require every declared value,
    including provider summaries, to be finite and wavelengths to increase.
    """
    if not isinstance(expected_sha256, str) or not re.fullmatch(
        r"[a-fA-F0-9]{64}", expected_sha256
    ):
        raise ValueError("Expected a complete SHA-256 checksum")
    path = Path(path)
    if not path.is_file():
        raise ValueError("CCS input must be an existing file")
    if path.stat().st_size > MAX_CCS_BYTES:
        raise ValueError("CCS file exceeds the 16 MB size limit")
    with path.open("rb") as source:
        raw = source.read(MAX_CCS_BYTES + 1)
    if len(raw) > MAX_CCS_BYTES:
        raise ValueError("CCS file exceeds the 16 MB size limit")
    if hashlib.sha256(raw).hexdigest() != expected_sha256.lower():
        raise ValueError("CCS input checksum mismatch")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError("CCS input must be UTF-8 text") from exc

    shot_count: int | None = None
    header_found = False
    wavelengths: list[float] = []
    shot_rows: list[np.ndarray] = []
    try:
        for row_number, row in enumerate(csv.reader(io.StringIO(text), strict=True), start=1):
            if not row or (len(row) == 1 and not row[0].strip()):
                continue
            fields = [field.strip() for field in row]
            if fields[0].startswith("#"):
                if header_found:
                    raise ValueError("CCS comments or duplicate headers after the column header")
                first = fields[0][1:].strip()
                if first.upper().startswith("NSHOTS"):
                    matched = re.fullmatch(r"NSHOTS\s*=\s*([0-9]+)", first, flags=re.IGNORECASE)
                    if not matched or any(fields[1:]):
                        raise ValueError("Invalid NSHOTS declaration")
                    if shot_count is not None:
                        raise ValueError("Duplicate NSHOTS declaration")
                    shot_count = int(matched.group(1))
                    if not 1 <= shot_count <= MAX_CCS_SHOTS:
                        raise ValueError("NSHOTS must be between 1 and 1000")
                elif first.lower() == "wave":
                    if shot_count is None:
                        raise ValueError("NSHOTS must precede the CCS column header")
                    header = [first.lower(), *(field.lower() for field in fields[1:])]
                    expected = [
                        "wave",
                        *(f"shot{i}" for i in range(1, shot_count + 1)),
                        "median",
                        "mean",
                    ]
                    if header != expected:
                        raise ValueError(
                            "Expected ordered wave, consecutive shot1..shotN, median, mean"
                        )
                    header_found = True
                continue
            if not header_found or shot_count is None:
                raise ValueError("CCS numeric data requires a declared NSHOTS and # wave header")
            if len(wavelengths) >= MAX_CCS_POINTS:
                raise ValueError("CCS file exceeds the 100000-point limit")
            if (len(wavelengths) + 1) * shot_count > MAX_SERIES_VALUES:
                raise ValueError("CCS file exceeds the 2000000 shot-value limit")
            if len(fields) != shot_count + 3:
                raise ValueError(f"CCS row {row_number} has an unexpected column count")
            try:
                values = np.asarray([float(field) for field in fields], dtype=np.float64)
            except ValueError as exc:
                raise ValueError(f"CCS row {row_number} contains nonnumeric values") from exc
            if not np.isfinite(values).all():
                raise ValueError(f"CCS row {row_number} must contain only finite values")
            wavelength = float(values[0])
            if wavelengths and wavelength <= wavelengths[-1]:
                raise ValueError("CCS wavelengths must be strictly increasing")
            wavelengths.append(wavelength)
            shot_rows.append(values[1 : 1 + shot_count])
    except csv.Error as exc:
        raise ValueError("Malformed CCS CSV structure") from exc
    except FloatingPointError as exc:
        raise ValueError("Retained-shot averaging exceeded finite floating-point range") from exc
    if not header_found or shot_count is None:
        raise ValueError("CCS requires NSHOTS and a # wave column header")
    if len(wavelengths) < 2:
        raise ValueError("CCS requires at least two numeric wavelength rows")
    return (
        np.asarray(wavelengths, dtype=np.float64),
        np.asarray(shot_rows, dtype=np.float64).T,
        {
            "shot_count": shot_count,
            "input_sha256": hashlib.sha256(raw).hexdigest(),
            "provider_summary_columns_validated": True,
        },
    )


def read_chemcam_ccs(
    path: Path | str,
    expected_sha256: str,
    *,
    discard_first_shots: int = 5,
) -> tuple[np.ndarray, np.ndarray, dict[str, int | str]]:
    """Return wavelength, mean of retained shots, and explicit aggregation metadata.

    Both arrays are one-dimensional float64. This compatibility function retains
    its historic arithmetic-mean behaviour; callers needing per-shot quality
    control must use :func:`read_chemcam_ccs_shots` before aggregation.
    """
    if (
        isinstance(discard_first_shots, bool)
        or not isinstance(discard_first_shots, int)
        or discard_first_shots < 0
    ):
        raise ValueError("discard_first_shots must be a nonnegative integer")
    wavelengths, shots, metadata = read_chemcam_ccs_shots(path, expected_sha256)
    shot_count = metadata["shot_count"]
    assert isinstance(shot_count, int)
    if shot_count <= discard_first_shots:
        raise ValueError("NSHOTS must exceed discard_first_shots")
    try:
        with np.errstate(over="raise", invalid="raise"):
            mean = np.mean(shots[discard_first_shots:], axis=0, dtype=np.float64)
    except FloatingPointError as exc:
        raise ValueError("Retained-shot averaging exceeded finite floating-point range") from exc
    if not np.isfinite(mean).all():
        raise ValueError("Retained-shot mean must be finite")
    return (
        wavelengths,
        mean,
        {
            "shot_count": shot_count,
            "discarded_shots": discard_first_shots,
            "retained_shots": shot_count - discard_first_shots,
            "aggregation": "arithmetic_mean_of_retained_shots",
        },
    )
