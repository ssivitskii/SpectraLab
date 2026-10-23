from __future__ import annotations

import json
from dataclasses import replace

import numpy as np
import pytest
from spectralab_ml.inference.predictor import ModelRegistry
from spectralab_ml.models import load_artifact
from spectralab_ml.models.line_classifier import LineLogisticOVR
from spectralab_ml.training import train_and_save_models


def test_line_evidence_is_invariant_to_additive_continuum():
    grid = np.linspace(490, 510, 401)
    model = LineLogisticOVR(grid, (500.0,))
    spectra = np.array([np.exp(-(((grid - 500) / 0.3) ** 2))])
    np.testing.assert_allclose(model.features(spectra), model.features(spectra + 4), atol=1e-12)


def test_multiscale_evidence_survives_small_wavelength_drift():
    grid = np.linspace(490, 510, 401)
    model = LineLogisticOVR(grid, (500.0,))
    spectra = np.array([np.exp(-(((grid - center) / 0.3) ** 2)) for center in (500, 500.15)])
    features = model.features(spectra)
    assert features.shape == (2, 6)
    # The wider 0.6 nm height window still sees the displaced peak.
    assert features[1, 2] >= features[0, 2] * 0.95


def test_single_class_training_preserves_always_positive_and_always_negative_outputs():
    grid = np.linspace(490, 510, 401)
    X = np.array([np.exp(-(((grid - 500) / 0.3) ** 2))] * 4)
    y = np.array([[1, 0]] * 4)
    model = LineLogisticOVR(grid, (500.0,)).fit(X, y)
    np.testing.assert_array_equal(model.scores(X) >= 0, y.astype(bool))
    assert np.isfinite(model.scores(np.zeros((1, len(grid))))).all()


@pytest.mark.parametrize("problem", ["unknown_labels", "nonfinite", "wrong_grid", "unfitted"])
def test_classifier_rejects_bad_training_or_prediction_arrays(problem):
    grid = np.linspace(490, 510, 401)
    model = LineLogisticOVR(grid, (500.0,))
    X, y = np.ones((4, len(grid))), np.array([[0], [1], [0], [1]])
    with pytest.raises(ValueError):
        if problem == "unknown_labels":
            model.fit(X, np.full_like(y, 2))
        elif problem == "nonfinite":
            X[0, 0] = np.nan
            model.fit(X, y)
        elif problem == "wrong_grid":
            model.fit(X[:, :-1], y)
        else:
            model.scores(X)


def test_catalog_selection_requires_reference_evidence_for_every_class(catalog, processor):
    with pytest.raises(ValueError, match="No usable"):
        LineLogisticOVR.from_catalog(catalog, processor.model_grid, ("unsupported",))


def test_line_model_roundtrip_registry_and_train_only_scaler(dataset, catalog, processor, tmp_path):
    result = train_and_save_models(
        dataset,
        catalog,
        processor,
        tmp_path,
        seed=42,
        model_ids=["line-logistic"],
        timing_warmups=1,
        timing_repeats=2,
    )
    model, _, manifest = load_artifact(tmp_path / "line-logistic")
    assert result["line-logistic"]["micro_f1"] >= 0
    assert manifest.model_configuration["feature_version"] == "multiscale-line-windows-v1"
    np.testing.assert_allclose(model.scaler.mean_, model.features(dataset.train.X).mean(axis=0))
    before = model.scaler.mean_.copy()
    model.scores(dataset.test.X)
    np.testing.assert_array_equal(model.scaler.mean_, before)
    registry = ModelRegistry(tmp_path, catalog, dataset.class_order, processor)
    listed = next(item for item in registry.list_models() if item["model_id"] == "line-logistic")
    assert listed["ready"]
    record = next(record for record in dataset.records if record["split"] == "test")
    prediction = registry.predict(
        "line-logistic", np.array(record["wavelength_nm"]), np.array(record["intensity"])
    )
    assert len(prediction.scores) == len(dataset.class_order)
    assert not prediction.calibrated
    assert "decision_function" in prediction.score_kind


def test_model_configuration_cannot_disagree_with_serialized_line_features(
    dataset, catalog, processor, tmp_path
):
    train_and_save_models(
        dataset,
        catalog,
        processor,
        tmp_path,
        seed=42,
        model_ids=["line-logistic"],
        timing_warmups=1,
        timing_repeats=2,
    )
    path = tmp_path / "line-logistic/manifest.json"
    manifest = json.loads(path.read_text())
    manifest["model_configuration"]["positions_nm"][0] += 1
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="configuration"):
        load_artifact(path.parent)


def test_invalid_group_split_prevents_training_line_model(dataset, catalog, processor, tmp_path):
    ids = list(dataset.test.base_sample_ids)
    ids[0] = dataset.train.base_sample_ids[0]
    invalid = replace(dataset, test=replace(dataset.test, base_sample_ids=tuple(ids)))
    with pytest.raises(ValueError, match="overlap"):
        train_and_save_models(
            invalid, catalog, processor, tmp_path, seed=42, model_ids=["line-logistic"]
        )


def test_line_margins_use_logistic_probability_view():
    from scipy.special import expit
    from spectralab_ml.models.calibration import probability_view, score_logits

    grid = np.linspace(490, 510, 401)
    model = LineLogisticOVR(grid, (500.0,))
    scores = np.array([[-2.0, 0.0, 2.0]])
    np.testing.assert_array_equal(score_logits(model.score_kind, scores), scores)
    np.testing.assert_allclose(probability_view(model, scores), expit(scores))


def test_calibrated_line_model_artifact_roundtrip(dataset, catalog, processor, tmp_path):
    train_and_save_models(
        dataset,
        catalog,
        processor,
        tmp_path,
        seed=42,
        model_ids=["line-logistic"],
        calibrate=True,
        timing_warmups=1,
        timing_repeats=2,
    )
    model, _, manifest = load_artifact(tmp_path / "line-logistic")
    assert manifest.calibrated
    assert manifest.model_configuration["positions_nm"]
    probabilities = model.scores(dataset.test.X)
    assert np.all((probabilities >= 0) & (probabilities <= 1))
    metadata = manifest.calibration_metadata
    assert not set(metadata["calibration_base_sample_ids"]) & set(
        metadata["threshold_base_sample_ids"]
    )
