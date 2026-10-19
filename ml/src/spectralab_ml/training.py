from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

from spectralab_ml.benchmark import measure_inference
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
    *,
    ion_stage: int = 1,
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
                **(
                    {"ion_stage": ion_stage}
                    if "ion_stage" in GenerationConfig.__dataclass_fields__
                    else {}
                ),
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
    model_ids: list[str] | None = None,
    *,
    ion_stage: int = 1,
) -> dict[str, MultiLabelEstimator]:
    requested = model_ids or ["baseline-nnls", "logistic-ovr", "random-forest"]
    if not set(requested) <= {"baseline-nnls", "logistic-ovr", "random-forest", "cnn-1d"}:
        raise ValueError("Unknown model ID")
    models = {}
    for model_id in requested:
        if model_id == "baseline-nnls":
            models[model_id] = BaselineNNLS(
                make_reference_templates(catalog, preprocessor, class_order, ion_stage=ion_stage)
            )
        elif model_id == "logistic-ovr":
            models[model_id] = LogisticOVR(seed=seed)
        elif model_id == "random-forest":
            models[model_id] = RandomForestMultiLabel(seed=seed)
        else:
            from spectralab_ml.models.cnn import CNN1D

            models[model_id] = CNN1D(seed=seed)
    return models


def calibration_partition(dataset: DatasetBundle, seed: int) -> tuple[np.ndarray, np.ndarray, dict]:
    """Split development by base ID; variations of a sample never cross roles."""
    dataset.validate()
    ids = np.asarray(dataset.validation.base_sample_ids)
    unique = np.asarray(sorted(set(ids)))
    if len(unique) < 2:
        raise ValueError("Calibration requires at least two validation base IDs")
    shuffled = unique[np.random.default_rng(seed).permutation(len(unique))]
    calibration_ids = shuffled[: len(shuffled) // 2].tolist()
    threshold_ids = shuffled[len(shuffled) // 2 :].tolist()
    return (
        np.isin(ids, calibration_ids),
        np.isin(ids, threshold_ids),
        {
            "calibration_base_sample_ids": sorted(calibration_ids),
            "threshold_base_sample_ids": sorted(threshold_ids),
            "test_base_sample_ids": dataset.split_ids["test"],
        },
    )


def _plot_reliability(path: Path, before: dict, after: dict) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    path.parent.mkdir(parents=True, exist_ok=True)
    fig, axis = plt.subplots(figsize=(5, 5))
    axis.plot([0, 1], [0, 1], linestyle="--", color="gray", label="Ideal")
    for name, result in (("Before", before), ("After", after)):
        bins = [item for item in result["bins"] if item["count"]]
        axis.plot(
            [b["mean_score"] for b in bins],
            [b["positive_fraction"] for b in bins],
            marker="o",
            label=name,
        )
    axis.set(
        xlabel="Mean predicted probability",
        ylabel="Observed positive fraction",
        xlim=(0, 1),
        ylim=(0, 1),
        title="Held-out synthetic test reliability",
    )
    axis.legend()
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def train_and_save_models(
    dataset: DatasetBundle,
    catalog: ReferenceCatalog,
    preprocessor: SpectrumPreprocessor,
    output_dir: Path,
    *,
    seed: int,
    model_ids: list[str] | None = None,
    calibrate: bool = False,
    timing_warmups: int = 2,
    timing_repeats: int = 7,
) -> dict[str, dict[str, Any]]:
    from spectralab_ml.models.calibration import (
        CalibratedEstimator,
        probability_view,
        reliability_metrics,
    )

    dataset.validate(expected_features=len(preprocessor.model_grid))
    if not dataset.records:
        raise ValueError("Training dataset requires generation provenance records")
    stages = {record["parameters"].get("ion_stage", 1) for record in dataset.records}
    if len(stages) != 1:
        raise ValueError("Training dataset must use a single declared ion stage")
    for key in (
        "source",
        "checksum_sha256",
        "content_sha256",
        "source_version",
        "wavelength_medium",
    ):
        if dataset.reference_metadata.get(key) != catalog.metadata.get(key):
            raise ValueError("Dataset and training reference source are incompatible")
    results: dict[str, dict[str, Any]] = {}
    ion_stage = dataset.records[0]["parameters"].get("ion_stage", 1)
    for model_id, estimator in make_estimators(
        catalog, preprocessor, dataset.class_order, seed, model_ids, ion_stage=ion_stage
    ).items():
        estimator.fit(dataset.train.X, dataset.train.y)
        calibration_metadata = {}
        reliability = None
        threshold_mask = np.ones(len(dataset.validation.X), dtype=bool)
        if calibrate and model_id in {"logistic-ovr", "random-forest"}:
            calibration_mask, threshold_mask, partitions = calibration_partition(dataset, seed)
            uncalibrated = estimator
            before = reliability_metrics(
                dataset.test.y, probability_view(estimator, estimator.scores(dataset.test.X))
            )
            estimator = CalibratedEstimator(uncalibrated).fit_calibration(
                dataset.validation.X[calibration_mask], dataset.validation.y[calibration_mask]
            )
            calibration_metadata = {
                **estimator.calibration_metadata,
                **partitions,
                "development_split_hash": dataset.split_hash,
            }
            estimator.calibration_metadata = calibration_metadata
            after = reliability_metrics(dataset.test.y, estimator.scores(dataset.test.X))
            reliability = {
                "before": before,
                "after": after,
                "evaluation_split": "test; never used to fit calibration or thresholds",
            }
        validation_X = dataset.validation.X[threshold_mask]
        validation_y = dataset.validation.y[threshold_mask]
        validation_scores = estimator.scores(validation_X)
        default = (
            0.15
            if model_id == "baseline-nnls"
            else (
                0.0 if model_id in {"logistic-ovr", "cnn-1d"} and not estimator.calibrated else 0.5
            )
        )
        thresholds, selection = select_thresholds(validation_scores, validation_y, default=default)
        selection["validation_split_hash"] = dataset.split_hash
        if calibration_metadata:
            selection.update(
                split="development_thresholds",
                base_sample_ids=calibration_metadata["threshold_base_sample_ids"],
            )
        test_scores = estimator.scores(dataset.test.X)
        metrics = evaluate_multilabel(dataset.test.y, test_scores, thresholds, dataset.class_order)
        metrics.update(
            measure_inference(
                lambda estimator=estimator: estimator.scores(dataset.test.X),
                len(dataset.test.X),
                warmups=timing_warmups,
                repeats=timing_repeats,
            )
        )
        if reliability:
            metrics["reliability"] = reliability
            _plot_reliability(
                output_dir / model_id / "reliability.png",
                reliability["before"],
                reliability["after"],
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
                "protocol": "thresholds: development only; final metrics: grouped held-out test",
                "validation": evaluate_multilabel(
                    validation_y, validation_scores, thresholds, dataset.class_order
                ),
                "test": metrics,
            },
            reference_metadata=catalog.metadata,
            generator_parameters={"samples": [r["parameters"] for r in dataset.records]},
            split_ids=dataset.split_ids,
            ion_stages=(ion_stage,),
            **({"calibration_metadata": calibration_metadata} if calibration_metadata else {}),
        )
        save_artifact(output_dir / model_id, estimator, preprocessor, manifest)
        results[model_id] = metrics
    return results
