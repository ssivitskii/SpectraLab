from __future__ import annotations

from dataclasses import dataclass

from spectralab_ml.config import load_project_config
from spectralab_ml.data.reference import load_demo_catalog
from spectralab_ml.inference import ModelRegistry
from spectralab_ml.preprocessing import SpectrumPreprocessor

from app.core.config import Settings
from app.services.storage import ExperimentStorage, SpectrumStorage


@dataclass
class Services:
    settings: Settings
    project: object
    catalog: object
    registry: ModelRegistry
    spectra: SpectrumStorage
    experiments: ExperimentStorage


def build_services(settings: Settings) -> Services:
    project = load_project_config(settings.project_root / "ml" / "configs" / "project.yaml")
    catalog = load_demo_catalog(settings.project_root)
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
        ),
        spectra=SpectrumStorage(
            settings.resolved_artifacts_dir() / "spectra",
            settings.max_stored_spectra,
            settings.max_storage_bytes,
        ),
        experiments=ExperimentStorage(settings.resolved_reports_dir() / "experiments"),
    )
