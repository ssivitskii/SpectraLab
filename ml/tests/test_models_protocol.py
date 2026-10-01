import json
from dataclasses import replace

import numpy as np
import pytest
import yaml
from spectralab_ml.data.reference import ReferenceCatalog
from spectralab_ml.datasets import build_demo_dataset
from spectralab_ml.evaluation import evaluate_multilabel
from spectralab_ml.experiments import run_experiment
from spectralab_ml.models import load_artifact, save_artifact
from spectralab_ml.models.estimators import BaselineNNLS, select_thresholds
from spectralab_ml.training import train_and_save_models


def test_grouped_split_and_shared_pipeline(dataset, processor):
    ids = dataset.split_ids
    assert not (
        set(ids["train"]) & set(ids["validation"])
        or set(ids["train"]) & set(ids["test"])
        or set(ids["test"]) & set(ids["validation"])
    )
    for split_name in ids:
        sample = next(r for r in dataset.records if r["split"] == split_name)
        np.testing.assert_array_equal(
            processor.transform(sample["wavelength_nm"], sample["intensity"]),
            getattr(dataset, split_name).X[0],
        )


def test_reference_changes_dataset_hash(catalog, classes, processor, dataset):
    changed = ReferenceCatalog(
        [
            replace(catalog.lines[0], wavelength_nm=catalog.lines[0].wavelength_nm + 0.1),
            *catalog.lines[1:],
        ],
        catalog.metadata,
    )
    other = build_demo_dataset(changed, processor, classes, base_samples=18, variations=2)
    assert other.dataset_hash != dataset.dataset_hash


def test_artifact_roundtrip(tmp_path, dataset, catalog, processor, classes):
    results = train_and_save_models(dataset, catalog, processor, tmp_path, seed=42)
    assert set(results) == {"baseline-nnls", "logistic-ovr", "random-forest"}
    for model_id in results:
        estimator, pipeline, manifest = load_artifact(
            tmp_path / model_id,
            expected_class_order=classes,
            expected_reference=catalog.metadata,
            expected_model_id=model_id,
        )
        assert (
            manifest.class_order == classes
            and manifest.reference_metadata["source"] == "demo_fixture"
        )
        assert manifest.threshold_selection["split"] == "validation"
        assert set(manifest.metrics) >= {"validation", "test"}
        copy = tmp_path / f"{model_id}-copy"
        save_artifact(copy, estimator, pipeline, manifest)
        loaded, _, _ = load_artifact(copy)
        np.testing.assert_array_equal(
            estimator.scores(dataset.test.X), loaded.scores(dataset.test.X)
        )
        with pytest.raises(ValueError):
            load_artifact(tmp_path / model_id, expected_class_order=tuple(reversed(classes)))
        with pytest.raises(ValueError):
            load_artifact(tmp_path / model_id, expected_model_id="other")
        with pytest.raises(ValueError):
            save_artifact(
                tmp_path / "invalid",
                estimator,
                replace(pipeline, wavelength_start_nm=351, wavelength_end_nm=801),
                manifest,
            )


def test_no_forced_baseline_detection():
    model = BaselineNNLS(np.eye(2))
    assert not (model.scores(np.array([[0.01, 0.01]])) >= 0.2).any()
    assert np.isfinite(model.scores(np.zeros((1, 2)))).all()


def test_threshold_fallback_and_undefined_metrics():
    scores = np.array([[0.0, 0.0], [1.0, 0.0]])
    y = np.array([[0, 0], [1, 0]])
    thresholds, meta = select_thresholds(scores, y, default=0.5)
    assert meta["fallback_columns"] == [1]
    metrics = evaluate_multilabel(y, scores, thresholds, ("H", "He"))
    assert metrics["per_element"]["He"]["precision"] is None
    assert metrics["per_element"]["He"]["recall"] is None


@pytest.mark.parametrize(
    "wavelength,intensity",
    [
        ([350, 350, 800], [1, 2, 3]),
        ([350, 800], [1]),
        ([350, 800], [0, 0]),
        ([350.004, 799.996], [1, 2]),
        ([350, 800], [1, float("nan")]),
    ],
)
def test_invalid_measurements(processor, wavelength, intensity):
    with pytest.raises(ValueError):
        processor.transform(wavelength, intensity)


def test_unseen_protocol_and_immutable_runs(tmp_path):
    config = {
        "run_id": "check",
        "seed": 7,
        "protocol": "unseen_combinations",
        "models": ["baseline-nnls"],
        "base_samples": 18,
        "variations": 1,
        "factor": "snr_db",
        "values": [10, 30],
    }
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(config))
    first = run_experiment(path, tmp_path / "runs")
    second = run_experiment(path, tmp_path / "runs")
    assert first != second
    result = json.loads((first / "result.json").read_text())
    assert result["status"] == "completed"
    assert not {tuple(c) for c in result["heldout_combinations"]} & {
        tuple(c) for c in result["train_combinations"]
    }
    assert {element for combo in result["train_combinations"] for element in combo} == set(
        result["class_order"]
    )
    assert result["runs"][0]["test_base_sample_ids"] == result["runs"][1]["test_base_sample_ids"]
    assert result["model_artifacts"]["baseline-nnls"]["sha256"]
    assert (first / "dataset/spectra.npz").is_file()
    config["factor"] = "typo"
    path.write_text(yaml.safe_dump(config))
    with pytest.raises(ValueError):
        run_experiment(path, tmp_path / "runs")
