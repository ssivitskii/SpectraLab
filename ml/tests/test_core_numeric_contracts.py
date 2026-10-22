"""Regression cases for generic training boundaries, independent of real test data."""

from dataclasses import replace

import numpy as np
import pytest
from spectralab_ml.datasets import build_demo_dataset
from spectralab_ml.models.calibration import CalibratedEstimator, reliability_metrics
from spectralab_ml.models.cnn import CNN1D
from spectralab_ml.models.estimators import RandomForestMultiLabel, select_thresholds
from spectralab_ml.preprocessing import SpectrumPreprocessor
from spectralab_ml.training import calibration_partition, train_and_save_models


@pytest.mark.parametrize("labels", [[0, 1, 0, 1], [0, 0, 0, 0], [1, 1, 1, 1]])
def test_random_forest_single_output_keeps_probability_column(labels):
    X = np.array([[0, 1], [1, 0], [0, 1], [1, 0]], dtype=float)
    model = RandomForestMultiLabel(seed=7, n_estimators=16).fit(X, np.array(labels)[:, None])
    scores = model.scores(X)
    assert scores.shape == (4, 1)
    np.testing.assert_array_equal(scores[:, 0] >= 0.5, labels)
    if len(set(labels)) == 1:
        np.testing.assert_array_equal(scores[:, 0], labels)


def test_threshold_search_is_per_class_and_invariant_to_other_class_scale():
    y = np.array([[0, 1], [0, 0], [1, 1], [1, 0]])
    scores = np.array([[0, 1], [0.001, 0], [0.002, 1], [0.003, 0]])
    original, _ = select_thresholds(scores, y, default=0.5)
    scores[:, 1] *= 1000
    thresholds, metadata = select_thresholds(scores, y, default=0.5)
    assert thresholds[0] == original[0] == pytest.approx(0.0015)
    assert thresholds[1] == 500.0
    np.testing.assert_array_equal(scores >= thresholds, y)
    assert metadata["validation_f1"] == [1.0, 1.0]
    assert metadata["schema_version"] == 3


def test_threshold_is_the_midpoint_of_the_separating_gap():
    y = np.array([[0], [0], [1], [1]])
    scores = np.array([[0.1], [0.2], [0.8], [0.9]])
    thresholds, _ = select_thresholds(scores, y, default=0.5)
    assert thresholds[0] == pytest.approx(0.5)
    everything = select_thresholds(np.array([[0.3], [0.3]]), np.array([[1], [0]]), default=0.5)
    assert everything[0][0] == 0.3


def test_threshold_search_matches_brute_force_with_ties():
    rng = np.random.default_rng(42)
    scores = rng.integers(-3, 4, size=(40, 3)).astype(float)
    y = rng.integers(0, 2, size=scores.shape)
    thresholds, _ = select_thresholds(scores, y, default=0.0)
    for column in range(3):
        candidates = np.unique(scores[:, column])
        f1 = []
        for threshold in candidates:
            predicted = scores[:, column] >= threshold
            tp = (predicted & (y[:, column] == 1)).sum()
            f1.append(2 * tp / (predicted.sum() + y[:, column].sum()))
        best = int(np.argmax(f1))
        expected = candidates[best]
        if best > 0:
            expected = (candidates[best - 1] + candidates[best]) / 2
        assert thresholds[column] == pytest.approx(expected)
        np.testing.assert_array_equal(
            scores[:, column] >= thresholds[column], scores[:, column] >= candidates[best]
        )


@pytest.mark.parametrize("bad", [np.nan, np.inf, -1, 2, 0.5])
def test_nonbinary_or_unknown_labels_cannot_tune_thresholds_or_reliability(bad):
    y = np.array([[0.0], [bad]])
    scores = np.array([[0.2], [0.8]])
    with pytest.raises(ValueError):
        select_thresholds(scores, y, default=0.5)
    with pytest.raises(ValueError):
        reliability_metrics(y, scores)


@pytest.mark.parametrize("shape", [(0, 2), (2, 0), (2,)])
def test_threshold_and_reliability_reject_empty_or_nonmatrix_labels(shape):
    values = np.zeros(shape)
    with pytest.raises(ValueError):
        select_thresholds(values, values, default=0.5)
    with pytest.raises(ValueError):
        reliability_metrics(values, values)


@pytest.mark.parametrize("bins", [0, -1, True, 1.5, 1001])
def test_invalid_reliability_bins_cannot_report_zero_ece(bins):
    with pytest.raises(ValueError, match="bin count"):
        reliability_metrics(np.array([[0], [1]]), np.array([[0.9], [0.1]]), bins=bins)


def test_reliability_one_bin_uses_all_observations():
    result = reliability_metrics(np.array([[0], [1]]), np.array([[0.8], [0.8]]), bins=1)
    assert result["ece"] == pytest.approx(0.3)
    assert result["brier"] == pytest.approx(0.34)
    assert result["bins"][0]["count"] == 2


class IdentityScores:
    def fit(self, X, y):
        return self

    def scores(self, X):
        return X


def test_refitting_base_model_invalidates_calibration():
    X, y = np.array([[-1.0], [1.0]]), np.array([[0], [1]])
    model = CalibratedEstimator(IdentityScores()).fit_calibration(X, y)
    assert model.scores(X).shape == y.shape
    model.fit(X, y)
    assert model.calibration_metadata == {}
    with pytest.raises(ValueError, match="Calibrator has not been fitted"):
        model.scores(X)


def test_calibration_rejects_invalid_single_class_labels_and_nonfinite_scores():
    with pytest.raises(ValueError, match="binary"):
        CalibratedEstimator(IdentityScores()).fit_calibration(np.ones((2, 1)), np.full((2, 1), 2))

    class BadScores(IdentityScores):
        def scores(self, X):
            return np.full((len(X), 1), np.nan)

    with pytest.raises(ValueError, match="finite"):
        CalibratedEstimator(BadScores()).fit_calibration(np.ones((2, 1)), np.zeros((2, 1)))


def test_training_rejects_group_leakage_before_fitting_or_creating_artifacts(
    dataset, catalog, processor, tmp_path, monkeypatch
):
    ids = list(dataset.validation.base_sample_ids)
    ids[0] = dataset.train.base_sample_ids[0]
    leaked = replace(dataset, validation=replace(dataset.validation, base_sample_ids=tuple(ids)))

    def forbidden(*args, **kwargs):
        pytest.fail("Model construction must not happen for invalid split identities")

    monkeypatch.setattr("spectralab_ml.training.make_estimators", forbidden)
    with pytest.raises(ValueError, match="overlap"):
        train_and_save_models(leaked, catalog, processor, tmp_path / "models", seed=7)
    with pytest.raises(ValueError, match="overlap"):
        calibration_partition(leaked, 7)
    assert not (tmp_path / "models").exists()


@pytest.mark.parametrize(
    "problem", ["id_count", "nonfinite", "class_count", "feature_count", "variant_labels"]
)
def test_dataset_validation_rejects_misaligned_variations(dataset, problem):
    split = dataset.train
    if problem == "id_count":
        split = replace(split, base_sample_ids=split.base_sample_ids[:-1])
    elif problem == "nonfinite":
        X = split.X.copy()
        X[0, 0] = np.inf
        split = replace(split, X=X)
    elif problem == "class_count":
        split = replace(split, y=split.y[:, :-1])
    elif problem == "feature_count":
        split = replace(split, X=split.X[:, :-1])
    else:
        y = split.y.copy()
        assert split.base_sample_ids[0] == split.base_sample_ids[1]
        y[1, 0] = 1 - y[0, 0]
        split = replace(split, y=y)
    with pytest.raises(ValueError):
        replace(dataset, train=split).validate()


@pytest.mark.parametrize("combinations", [[], [()], [("H", "H")], [("H", "He", "Na", "Mg")]])
def test_invalid_demo_combinations_fail_explicitly(catalog, processor, classes, combinations):
    with pytest.raises(ValueError, match="combinations"):
        build_demo_dataset(catalog, processor, classes, combinations=combinations)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"model_step_nm": 7},
        {"model_step_nm": -1},
        {"model_step_nm": 0},
        {"model_step_nm": 1e-100},
        {"model_step_nm": np.nan},
        {"wavelength_end_nm": np.inf},
        {"wavelength_start_nm": 800},
        {"normalization": "unknown"},
        {"normalization": []},
    ],
)
def test_preprocessing_rejects_invalid_grid_before_allocating(kwargs):
    with pytest.raises(ValueError):
        SpectrumPreprocessor(**kwargs)


@pytest.mark.parametrize("problem", ["nan", "overflow32", "nonbinary", "empty"])
def test_cnn_validates_arrays_before_importing_optional_runtime(problem, monkeypatch):
    X, y = np.ones((2, 4)), np.array([[0], [1]])
    if problem == "nan":
        X[0, 0] = np.nan
    elif problem == "overflow32":
        X[0, 0] = 1e100
    elif problem == "nonbinary":
        y[0, 0] = 2
    else:
        X, y = X[:0], y[:0]

    def forbidden():
        pytest.fail("Invalid input must be rejected before importing the CNN runtime")

    monkeypatch.setattr("spectralab_ml.models.cnn._torch", forbidden)
    with pytest.raises(ValueError):
        CNN1D().fit(X, y)
