"""Explicit standard-air conversions. Never guess a measurement's wavelength medium.

Peck & Reeder (1972), doi:10.1364/JOSA.62.000958, as used by NIST ASD.
Standard dry air: 15 °C, 101325 Pa, 0.033% CO2; supported vacuum range 185-1690 nm.
"""

from __future__ import annotations

from dataclasses import asdict, replace

import numpy as np

from spectralab_ml.data.reference import ReferenceCatalog, reference_content_hash

CONVERSION_SOURCE = "https://physics.nist.gov/PhysRefData/ASD/Html/lineshelp.html#REFRACTIVE"


def refractive_index(vacuum_nm: np.ndarray | float) -> np.ndarray:
    wavelength = np.asarray(vacuum_nm, dtype=float)
    if not np.isfinite(wavelength).all() or np.any((wavelength < 185) | (wavelength > 1690)):
        raise ValueError("Peck-Reeder conversion supports 185–1690 vacuum nm")
    inverse_square = (1000 / wavelength) ** 2
    return 1 + 1e-8 * (
        8060.51 + 2480990 / (132.274 - inverse_square) + 17455.7 / (39.32957 - inverse_square)
    )


def vacuum_to_air(vacuum_nm: np.ndarray | float) -> np.ndarray:
    values = np.asarray(vacuum_nm, dtype=float)
    return values / refractive_index(values)


def air_to_vacuum(air_nm: np.ndarray | float) -> np.ndarray:
    values = np.asarray(air_nm, dtype=float)
    lower, upper = vacuum_to_air(np.array([185.0, 1690.0]))
    if not np.isfinite(values).all() or np.any((values < lower) | (values > upper)):
        raise ValueError("Air wavelength outside Peck-Reeder validated range")
    result = np.clip(values * 1.0003, 185, 1690)
    for _ in range(8):
        result = np.clip(values * refractive_index(result), 185, 1690)
    return result


def convert_air_catalog(catalog: ReferenceCatalog) -> ReferenceCatalog:
    if not catalog.lines or any(line.wavelength_medium != "air" for line in catalog.lines):
        raise ValueError("Explicit conversion requires a homogeneous air reference")
    lines = [
        replace(
            line,
            wavelength_nm=float(air_to_vacuum(line.wavelength_nm)),
            wavelength_medium="vacuum",
            flags=line.flags + ";air_to_vacuum_peck_reeder",
        )
        for line in catalog.lines
    ]
    metadata = {
        **catalog.metadata,
        "schema_version": 2,
        "wavelength_medium": "vacuum",
        "units": "nm",
        "wavelength_conversion": {
            "method": "Peck-Reeder 1972",
            "source": CONVERSION_SOURCE,
            "input_medium": "air",
            "output_medium": "vacuum",
            "temperature_c": 15,
            "pressure_pa": 101325,
            "co2_fraction": 0.00033,
            "original_content_sha256": catalog.metadata.get("content_sha256"),
        },
    }
    metadata["content_sha256"] = reference_content_hash(metadata, [asdict(line) for line in lines])
    return ReferenceCatalog(lines, metadata)
