from __future__ import annotations

from dataclasses import dataclass

from spectralab_ml.config import load_project_config, load_reference_catalog
from spectralab_ml.inference import ModelRegistry
from spectralab_ml.preprocessing import SpectrumPreprocessor

from app.core.config import Settings
from app.services.batches import BatchStorage
from app.services.instrument_profiles import InstrumentProfileRegistry
from app.services.jobs import JobStorage
from app.services.storage import (
    ExperimentStorage,
    MeasuredPredictionStorage,
    PredictionStorage,
    SpectrumStorage,
)


@dataclass
class Services:
    settings: Settings
    project: object
    catalog: object
    registry: ModelRegistry
    spectra: SpectrumStorage
    experiments: ExperimentStorage
    predictions: PredictionStorage
    jobs: JobStorage
    batches: BatchStorage
    instrument_profiles: InstrumentProfileRegistry
    measured_predictions: MeasuredPredictionStorage


def build_services(settings: Settings) -> Services:
    project = load_project_config(settings.project_root / "ml" / "configs" / "project.yaml")
    catalog = load_reference_catalog(
        project,
        reference_cache=settings.reference_cache,
        expected_sha256=settings.reference_sha256,
    )
    preprocessor = SpectrumPreprocessor(
        project.wavelength_start_nm,
        project.wavelength_end_nm,
        project.model_step_nm,
    )
    return Services(
        settings=settings,
        project=project,
        catalog=catalog,
        registry=ModelRegistry(
            settings.resolved_artifacts_dir() / "models",
            catalog,
            project.elements,
            preprocessor,
            ion_stage=project.ion_stage,
        ),
        spectra=SpectrumStorage(
            settings.resolved_artifacts_dir() / "spectra",
            settings.max_stored_spectra,
            settings.max_storage_bytes,
        ),
        experiments=ExperimentStorage(settings.resolved_reports_dir() / "experiments"),
        predictions=PredictionStorage(
            settings.resolved_artifacts_dir() / "predictions",
            settings.max_stored_predictions,
            settings.max_prediction_storage_bytes,
        ),
        jobs=JobStorage(
            settings.resolved_artifacts_dir() / "jobs",
            settings.max_pending_jobs,
            settings.max_job_records,
        ),
        batches=BatchStorage(settings.resolved_artifacts_dir() / "batches", settings),
        instrument_profiles=InstrumentProfileRegistry(settings),
        measured_predictions=MeasuredPredictionStorage(
            settings.resolved_artifacts_dir() / "measured_predictions",
            settings.max_stored_measured_predictions,
            settings.max_measured_prediction_storage_bytes,
        ),
    )
