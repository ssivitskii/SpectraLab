from __future__ import annotations

from dataclasses import replace

import numpy as np
import pytest
from spectralab_ml.datasets import build_demo_dataset
from spectralab_ml.evaluation.trace import (
    TraceCondition,
    build_trace_condition,
    trace_detection_metrics,
)
from spectralab_ml.inference.predictor import ModelRegistry
from spectralab_ml.models import load_artifact
from spectralab_ml.models.line_classifier import LineLogisticOVR
from spectralab_ml.training import train_and_save_models


def test_robust_generation_has_grouped_trace_contributions_and_full_provenance(catalog, processor):
    kwargs = dict(seed=909, base_samples=18, variations=2, generation_profile="line_robust_v1")
    data = build_demo_dataset(catalog, processor, ("H", "Na"), **kwargs)
    repeated = build_demo_dataset(catalog, processor, ("H", "Na"), **kwargs)
    data.validate()
    assert data.dataset_hash == repeated.dataset_hash
    assert data.split_hash == repeated.split_hash
    np.testing.assert_array_equal(data.train.X, repeated.train.X)
    contributions = {}
    for record in data.records:
        config = record["parameters"]
        assert abs(config["calibration_shift_nm"]) <= 0.5
        assert 0.02 <= min(config["component_weights"]) <= max(config["component_weights"]) == 1
        previous = contributions.setdefault(record["base_sample_id"], config["component_weights"])
        assert config["component_weights"] == previous
    assert any(min(weights) < 0.2 for weights in contributions.values())
    demo = build_demo_dataset(catalog, processor, ("H", "Na"), seed=909, base_samples=18)
    assert demo.dataset_hash != data.dataset_hash
    assert demo.split_hash == data.split_hash
    with pytest.raises(ValueError, match="profile"):
        build_demo_dataset(catalog, processor, ("H", "Na"), generation_profile="typo")


def test_element_local_predictions_do_not_use_other_element_windows():
    grid = np.linspace(490, 530, 801)
    groups = (tuple(range(6)), tuple(range(6, 12)))
    model = LineLogisticOVR(grid, (500.0, 520.0), feature_groups=groups)
    first = np.exp(-(((grid - 500) / 0.3) ** 2))
    second = np.exp(-(((grid - 520) / 0.3) ** 2))
    X = np.array([first, second, first + second, np.full_like(grid, 0.01)])
    model.fit(X, np.array([[1, 0], [0, 1], [1, 1], [0, 0]]))
    # No re-normalization here: adding a distant peak must not influence class 0.
    np.testing.assert_allclose(model.scores(X[[0]])[:, 0], model.scores(X[[2]])[:, 0])
    assert all(estimator.n_features_in_ == 6 for estimator in model.models)
    assert model.configuration()["feature_groups"] == [list(group) for group in groups]
    with pytest.raises(ValueError, match="feature groups"):
        LineLogisticOVR(grid, (500.0,), feature_groups=((9999,),)).fit(X, np.ones((4, 1)))


def test_robust_model_training_artifact_and_own_reference_windows(
    dataset, catalog, processor, tmp_path
):
    train_and_save_models(
        dataset,
        catalog,
        processor,
        tmp_path,
        seed=42,
        model_ids=["line-logistic-robust"],
        timing_warmups=1,
        timing_repeats=2,
    )
    model, _, manifest = load_artifact(tmp_path / "line-logistic-robust")
    assert len(model.feature_groups) == len(dataset.class_order)
    assert manifest.model_configuration["classifier_scope"].startswith("each element")
    assert all(0 < len(group) <= 36 for group in model.feature_groups)
    assert np.isfinite(model.scores(dataset.test.X)).all()
    np.testing.assert_allclose(model.scaler.mean_, model.features(dataset.train.X).mean(axis=0))
    registry = ModelRegistry(tmp_path, catalog, dataset.class_order, processor)
    assert next(row for row in registry.list_models() if row["model_id"] == "line-logistic-robust")[
        "ready"
    ]
    record = dataset.records[0]
    prediction = registry.predict(
        "line-logistic-robust", np.asarray(record["wavelength_nm"]), np.asarray(record["intensity"])
    )
    assert any("не концентрации" in warning for warning in prediction.warnings)


def test_trace_zero_is_absent_not_a_zero_positive_weight(catalog, processor):
    condition = TraceCondition("control", 0, 2)
    X, y, targets, provenance = build_trace_condition(
        catalog, processor, ("H", "Na"), condition, seed=8, repeats=1
    )
    assert X.shape == (2, len(processor.model_grid))
    assert np.all(y[np.arange(2), targets] == 0)
    assert np.all(y.sum(axis=1) == 1)
    assert all(
        record["parameters"]["component_weights"] == (1,) for record in provenance["records"]
    )
    again = build_trace_condition(catalog, processor, ("H", "Na"), condition, seed=8, repeats=1)
    assert provenance["dataset_hash"] == again[3]["dataset_hash"]
    positive = build_trace_condition(
        catalog, processor, ("H", "Na"), replace(condition, ratio=0.1), seed=8, repeats=1
    )
    assert positive[1].sum() == 4
    assert provenance["dataset_hash"] != positive[3]["dataset_hash"]


@pytest.mark.parametrize("ratio", [-1, 1.1, float("nan")])
def test_bad_trace_ratio_is_rejected(catalog, processor, ratio):
    with pytest.raises(ValueError, match="Invalid trace"):
        build_trace_condition(catalog, processor, ("H", "Na"), TraceCondition("bad", ratio), seed=8)


def test_trace_metrics_separate_weak_recall_and_false_positives():
    y = np.array([[1, 1, 0], [1, 0, 0]])
    pred = np.array([[1, 0, 0], [1, 1, 0]])
    metrics = trace_detection_metrics(y, pred, np.array([1, 1]))
    assert metrics["weak_target_recall"] == 0
    assert metrics["target_false_positive_rate"] == 1
    assert metrics["false_positive_count"] == 1
    assert metrics["false_positive_rate"] == pytest.approx(1 / 3)
    with pytest.raises(ValueError, match="valid target"):
        trace_detection_metrics(y, pred, np.array([-1, 1]))
