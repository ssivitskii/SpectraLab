"""Ion-resolved reference templates, with explicit element-level aggregation."""

from __future__ import annotations

import numpy as np

from spectralab_ml.data.reference import ReferenceCatalog
from spectralab_ml.preprocessing import SpectrumPreprocessor
from spectralab_ml.simulation import GenerationConfig, generate_spectrum


def make_ion_templates(
    catalog: ReferenceCatalog,
    preprocessor: SpectrumPreprocessor,
    species: tuple[tuple[str, int], ...],
) -> np.ndarray:
    if not species or len(set(species)) != len(species):
        raise ValueError("Unique (element, ion_stage) species required")
    elements = tuple(dict.fromkeys(element for element, _ in species))
    templates = []
    for element, stage in species:
        spectrum = generate_spectrum(
            GenerationConfig(
                elements=(element,),
                component_weights=(1.0,),
                ion_stage=stage,
                wavelength_start_nm=preprocessor.wavelength_start_nm,
                wavelength_end_nm=preprocessor.wavelength_end_nm,
                sampling_step_nm=preprocessor.model_step_nm,
                snr_db=None,
                amplitude_variation=0,
            ),
            catalog,
            class_order=elements,
        )
        templates.append(preprocessor.transform(spectrum.wavelength_nm, spectrum.intensity))
    return np.stack(templates)


def aggregate_ion_detections(
    detections: np.ndarray, species: tuple[tuple[str, int], ...], elements: tuple[str, ...]
) -> np.ndarray:
    """Boolean OR of thresholded species, never a sum of probabilities or coefficients."""
    if detections.dtype != bool or detections.shape[-1] != len(species):
        raise ValueError("Aggregate thresholded boolean ion detections only")
    if set(element for element, _ in species) != set(elements):
        raise ValueError("Element order must cover all species exactly")
    return np.stack(
        [
            detections[..., [i for i, pair in enumerate(species) if pair[0] == e]].any(-1)
            for e in elements
        ],
        axis=-1,
    )
