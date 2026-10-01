from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

from spectralab_ml.data.reference import ReferenceCatalog
from spectralab_ml.datasets import DatasetBundle
from spectralab_ml.evaluation import evaluate_multilabel
from spectralab_ml.models import (
    BaselineNNLS,
    LogisticOVR,
    ModelManifest,
    RandomForestMultiLabel,
    save_artifact,
)
from spectralab_ml.models.estimators import MultiLabelEstimator, select_thresholds
from spectralab_ml.preprocessing import SpectrumPreprocessor
from spectralab_ml.simulation import GenerationConfig, generate_spectrum


def make_reference_templates(
    catalog: ReferenceCatalog,
    preprocessor: SpectrumPreprocessor,
    class_order: tuple[str, ...],
) -> np.ndarray:
    rows = []
    for index, element in enumerate(class_order):
        spectrum = generate_spectrum(
            GenerationConfig(
                elements=(element,),
                component_weights=(1.0,),
                wavelength_start_nm=preprocessor.wavelength_start_nm,
                wavelength_end_nm=preprocessor.wavelength_end_nm,
                sampling_step_nm=preprocessor.model_step_nm,
                instrumental_fwhm_nm=0.5,
                snr_db=None,
                amplitude_variation=0,
                seed=index,
            ),
            catalog,
            class_order=class_order,
        )
        rows.append(preprocessor.transform(spectrum.wavelength_nm, spectrum.intensity))
    return np.stack(rows)


def make_estimators(
    catalog: ReferenceCatalog,
    preprocessor: SpectrumPreprocessor,
    class_order: tuple[str, ...],
    seed: int,
) -> dict[str, MultiLabelEstimator]:
    return {
        "baseline-nnls": BaselineNNLS(make_reference_templates(catalog, preprocessor, class_order)),
        "logistic-ovr": LogisticOVR(seed=seed),
        "random-forest": RandomForestMultiLabel(seed=seed),
    }


def train_and_save_models(
    dataset: DatasetBundle,
    catalog: ReferenceCatalog,
    preprocessor: SpectrumPreprocessor,
    output_dir: Path,
    *,
    seed: int,
    model_ids: list[str] | None = None,
) -> dict[str, dict[str, Any]]:
    results: dict[str, dict[str, Any]] = {}
    for model_id, estimator in make_estimators(
        catalog, preprocessor, dataset.class_order, seed
    ).items():
        if model_ids is not None and model_id not in model_ids:
            continue
        estimator.fit(dataset.train.X, dataset.train.y)
        validation_scores = estimator.scores(dataset.validation.X)
        default = (
            0.15 if model_id == "baseline-nnls" else (0.0 if model_id == "logistic-ovr" else 0.5)
        )
        thresholds, selection = select_thresholds(
            validation_scores, dataset.validation.y, default=default
        )
        selection["validation_split_hash"] = dataset.split_hash
        test_scores = estimator.scores(dataset.test.X)
        metrics = evaluate_multilabel(
            dataset.test.y,
            test_scores,
            thresholds,
            dataset.class_order,
            timed_predict=lambda estimator=estimator: estimator.scores(dataset.test.X),
        )
        threshold_map = dict(zip(dataset.class_order, thresholds.tolist(), strict=True))
        manifest = ModelManifest.create(
            model_id=model_id,
            model_type=type(estimator).__name__,
            class_order=dataset.class_order,
            preprocessor=preprocessor,
            thresholds=threshold_map,
            threshold_selection=selection,
            score_kind=estimator.score_kind,
            calibrated=estimator.calibrated,
            dataset_hash=dataset.dataset_hash,
            split_hash=dataset.split_hash,
            seed=seed,
            metrics={
                "protocol": "thresholds: validation; final metrics: grouped held-out test",
                "validation": evaluate_multilabel(
                    dataset.validation.y, validation_scores, thresholds, dataset.class_order
                ),
                "test": metrics,
            },
            reference_metadata=catalog.metadata,
            generator_parameters={"samples": [r["parameters"] for r in dataset.records]},
            split_ids=dataset.split_ids,
        )
        save_artifact(output_dir / model_id, estimator, preprocessor, manifest)
        results[model_id] = metrics
    return results
