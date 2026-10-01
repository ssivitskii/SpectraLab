from __future__ import annotations

import csv
import io
from typing import Annotated

import numpy as np
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from spectralab_ml.preprocessing import validate_spectrum_arrays
from spectralab_ml.simulation import GenerationConfig, generate_spectrum

from app.api.dependencies import get_services
from app.schemas.api import GenerateSpectrumRequest, SpectrumResponse
from app.services.container import Services

router = APIRouter(prefix="/spectra", tags=["spectra"])


@router.post("/generate", response_model=SpectrumResponse)
def generate(
    request: GenerateSpectrumRequest, services: Annotated[Services, Depends(get_services)]
) -> dict[str, object]:
    unsupported = set(request.elements) - set(services.project.elements)
    if unsupported:
        raise HTTPException(422, f"Unsupported elements: {', '.join(sorted(unsupported))}")
    spectrum = generate_spectrum(
        GenerationConfig(
            elements=tuple(request.elements),
            component_weights=tuple(request.component_weights),
            wavelength_start_nm=services.project.wavelength_start_nm,
            wavelength_end_nm=services.project.wavelength_end_nm,
            instrumental_fwhm_nm=request.instrumental_fwhm_nm,
            sampling_step_nm=request.sampling_step_nm,
            snr_db=request.snr_db,
            background_level=request.background_level,
            background_slope=request.background_slope,
            calibration_shift_nm=request.calibration_shift_nm,
            amplitude_variation=request.amplitude_variation,
            seed=request.seed,
            max_points=services.settings.max_spectrum_points,
        ),
        services.catalog,
        class_order=services.project.elements,
    )
    return services.spectra.save({**spectrum.to_dict(), "origin": "generated"})


@router.post("/upload", response_model=SpectrumResponse)
async def upload(
    file: Annotated[UploadFile, File()],
    units: Annotated[str, Form()],
    wavelength_medium: Annotated[str, Form()],
    services: Annotated[Services, Depends(get_services)],
) -> dict[str, object]:
    if units != "nm" or wavelength_medium != "vacuum":
        raise HTTPException(422, "Only vacuum wavelengths in nm are accepted")
    raw = await file.read(services.settings.max_upload_bytes + 1)
    if len(raw) > services.settings.max_upload_bytes:
        raise HTTPException(413, "CSV file is too large")
    try:
        text = raw.decode("utf-8-sig")
        reader = csv.DictReader(io.StringIO(text))
        if sorted(reader.fieldnames or []) != ["intensity", "wavelength_nm"]:
            raise ValueError("CSV columns must be exactly wavelength_nm,intensity")
        wavelengths: list[float] = []
        intensities: list[float] = []
        for index, row in enumerate(reader):
            if index >= services.settings.max_spectrum_points:
                raise ValueError("CSV contains too many points")
            if None in row or any(value is None for value in row.values()):
                raise ValueError("Unexpected CSV field count")
            wavelengths.append(float(row["wavelength_nm"]))
            intensities.append(float(row["intensity"]))
        validate_spectrum_arrays(
            np.asarray(wavelengths),
            np.asarray(intensities),
            max_points=services.settings.max_spectrum_points,
        )
    except (UnicodeDecodeError, ValueError, TypeError, KeyError) as exc:
        raise HTTPException(422, f"Invalid spectrum CSV: {exc}") from exc
    services.registry.preprocessor.transform(np.asarray(wavelengths), np.asarray(intensities))
    return services.spectra.save(
        {
            "wavelength_nm": wavelengths,
            "intensity": intensities,
            "clean_signal": None,
            "true_labels": None,
            "parameters": {
                "units": units,
                "wavelength_medium": wavelength_medium,
                "snr_db": None,
                "snr_kind": "unknown",
                "original_filename": file.filename,
            },
            "base_sample_id": None,
            "reference_source": "user_upload",
            "origin": "uploaded",
        }
    )


@router.get("/{spectrum_id}", response_model=SpectrumResponse)
def get_spectrum(
    spectrum_id: str, services: Annotated[Services, Depends(get_services)]
) -> dict[str, object]:
    return services.spectra.get(spectrum_id)
