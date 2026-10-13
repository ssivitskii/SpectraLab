from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.dependencies import get_services
from app.schemas.api import ElementsInfo, GenerateSpectrumRequest, ModelInfo, ServiceCapabilities
from app.services.container import Services
from app.services.exports import public_record

router = APIRouter(tags=["system"])


@router.get("/health")
def health(services: Annotated[Services, Depends(get_services)]) -> dict[str, object]:
    models = services.registry.list_models()
    return {
        "status": "ok",
        "reference_ready": bool(services.catalog.lines),
        "ready_models": [model["model_id"] for model in models if model["ready"]],
    }


@router.get("/elements", response_model=ElementsInfo)
def elements(services: Annotated[Services, Depends(get_services)]) -> dict[str, object]:
    return {
        "elements": list(services.project.elements),
        "ion_stage": services.project.ion_stage,
        "reference": public_record(services.catalog.metadata),
        "line_counts": {
            element: sum(1 for line in services.catalog.lines if line.element == element)
            for element in services.project.elements
        },
    }


@router.get("/models", response_model=list[ModelInfo])
def models(services: Annotated[Services, Depends(get_services)]) -> list[dict[str, object]]:
    return services.registry.list_models()


@router.get("/capabilities", response_model=ServiceCapabilities)
def capabilities(services: Annotated[Services, Depends(get_services)]) -> dict[str, object]:
    project = services.project
    defaults = GenerateSpectrumRequest(
        elements=[project.elements[0]], component_weights=[1], ion_stage=project.ion_stage
    )
    return {
        "schema_versions": {"configuration": 1, "spectrum": 1, "prediction": 1, "export": 1},
        "spectrum": {
            "wavelength_start_nm": project.wavelength_start_nm,
            "wavelength_end_nm": project.wavelength_end_nm,
            "model_step_nm": project.model_step_nm,
            "max_points": services.settings.max_spectrum_points,
            "max_upload_bytes": services.settings.max_upload_bytes,
        },
        "generation": {
            "min_sampling_step_nm": (
                (project.wavelength_end_nm - project.wavelength_start_nm)
                / (services.settings.max_spectrum_points - 1)
            ),
            "defaults": defaults.model_dump(exclude={"measurement_metadata"}),
            "ion_stages": [project.ion_stage],
            "limits": {
                "instrumental_fwhm_nm": {"minimum": 0.001, "maximum": 20},
                "snr_db": {"minimum": -40, "maximum": 120, "nullable": True},
                "amplitude_variation": {"minimum": 0, "maximum": 2},
                "background_level": {"minimum": -1e6, "maximum": 1e6},
                "background_slope": {"minimum": -1e4, "maximum": 1e4},
                "calibration_shift_nm": {"minimum": -5, "maximum": 5},
                "component_weights": {"minimum": 0, "maximum": 1000},
                "lorentzian_fwhm_nm": {"minimum": 0, "maximum": 20},
                "photon_scale": {"minimum": 1e-6, "maximum": 1e9},
                "read_noise_std": {"minimum": 0, "maximum": 1e6},
                "response_slope": {"minimum": -1, "maximum": 1},
                "saturation_level": {"minimum": 0, "nullable": True},
            },
            "constraints": [
                "sampling_step_nm must divide the wavelength span exactly",
                "component_weights and saturation_level must be strictly positive",
                "poisson_read requires snr_db=null",
                "lorentzian_fwhm_nm > 0 requires profile=voigt",
            ],
        },
        "reference": public_record(services.catalog.metadata),
        "elements": list(project.elements),
        "storage": {
            "max_spectra": services.settings.max_stored_spectra,
            "max_spectrum_bytes": services.settings.max_storage_bytes,
            "max_predictions": services.settings.max_stored_predictions,
            "max_prediction_bytes": services.settings.max_prediction_storage_bytes,
        },
        "jobs": {
            "enabled": True,
            "worker_required": True,
            "max_seeds": 10,
            "max_pending": services.settings.max_pending_jobs,
            "timeout_seconds": services.settings.job_timeout_seconds,
            "config_names": ["demo", "default"],
        },
        "batches": {
            "enabled": True,
            "worker_required": True,
            "max_files": services.settings.max_batch_files,
            "max_models": 5,
            "max_upload_bytes": services.settings.max_upload_bytes,
            "max_total_upload_bytes": services.settings.max_batch_upload_bytes,
        },
    }
