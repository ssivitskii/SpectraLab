from __future__ import annotations

import importlib.util
from pathlib import Path

import joblib
import numpy as np
import pytest
from spectralab_ml.evaluation.real_validation import (
    ObservedWindowEstimator,
    interpolate_observed,
    make_locked_synthetic_dataset,
    normalize_observed,
    observation_mask,
    positive_recall_report,
)
from spectralab_ml.models import BaselineNNLS


def test_observed_windows_exclude_missing_bands_and_reject_internal_gaps():
    wave = np.array([1.0, 2.0, 3.0, 7.0, 8.0, 9.0])
    grid = np.arange(1.0, 10.0, 0.5)
    windows = [[1.0, 3.0], [7.0, 9.0]]
    x = interpolate_observed(wave, wave * 2, grid, windows, max_gap_nm=1.1)
    mask = observation_mask(grid, windows)
    np.testing.assert_allclose(x[mask], 2 * grid[mask])
    assert np.all(x[~mask] == 0)
    with pytest.raises(ValueError, match="unobserved spectral gap"):
        interpolate_observed(wave, wave, grid, [[1.0, 9.0]], max_gap_nm=1.1)
    with pytest.raises(ValueError, match="cover"):
        interpolate_observed(wave[1:], wave[1:], grid, windows, max_gap_nm=1.1)
    with pytest.raises(ValueError, match="disjoint"):
        observation_mask(grid, [[1, 4], [3, 5]])


def test_observation_normalization_ignores_excluded_signal_and_survives_roundtrip(tmp_path):
    mask = np.array([True, False, True])
    x = np.array([[2, 99999, 1], [0, -99999, 3]])
    normalized = normalize_observed(x, mask)
    np.testing.assert_allclose(normalized, [[1, 0.5], [0, 1]])
    model = ObservedWindowEstimator(BaselineNNLS(np.eye(2)), mask)
    first = model.scores(x)
    path = tmp_path / "observed.joblib"
    joblib.dump(model, path)
    np.testing.assert_allclose(joblib.load(path).scores(x), first)
    np.testing.assert_array_equal(normalize_observed(np.zeros((1, 3)), mask), [[0, 0]])


def test_recall_is_physical_sample_weighted_and_unknown_labels_never_negative():
    # One physical sample has three repeats, another one repeat. Weight both equally.
    detected = np.array([[True, True], [True, False], [True, True], [False, True]])
    positive = np.array([[True, False]] * 4)
    groups = ["a", "a", "a", "b"]
    report = positive_recall_report(detected, positive, groups, ("Na", "H"))
    assert report["primary"]["recall"] == 0.5
    assert report["per_element"]["Na"]["replicate_missed_positives"] == 1
    assert report["per_element"]["H"]["recall"] is None
    assert report["per_element"]["H"]["unknown_label_prediction_count"] == 3
    assert all(report[key] is None for key in ("precision", "micro_f1", "false_positive_rate"))
    duplicated = positive_recall_report(
        np.repeat(detected, 2, axis=0),
        np.repeat(positive, 2, axis=0),
        np.repeat(groups, 2).tolist(),
        ("Na", "H"),
    )
    assert duplicated["primary"] == report["primary"]
    positive[1, 1] = True
    with pytest.raises(ValueError, match="conflicting"):
        positive_recall_report(detected, positive, groups, ("Na", "H"))


def test_locked_development_is_reproducible_and_disjoint_by_physical_group():
    templates = np.eye(3)
    kwargs = dict(seed=7, base_samples=60, variations=2, mask=np.ones(3, dtype=bool))
    first = make_locked_synthetic_dataset(templates, ("A", "B", "C"), {}, **kwargs)
    second = make_locked_synthetic_dataset(templates, ("A", "B", "C"), {}, **kwargs)
    assert first.dataset_hash == second.dataset_hash
    np.testing.assert_array_equal(first.train.X, second.train.X)
    train, dev, test = [set(first.split_ids[k]) for k in ("train", "validation", "test")]
    assert not (train & dev or train & test or dev & test)
    assert len(first.train.X) == 2 * len(train)


def test_primary_recall_weights_samples_equally_with_different_label_support():
    detected = np.array([[True, True], [False, False], [True, False]])
    positive = np.array([[True, True], [True, False], [False, False]])
    report = positive_recall_report(detected, positive, ["a", "b", "unlabelled"], ("Na", "Mg"))
    assert report["primary"]["recall"] == 0.5  # mean(2/2, 0/1), not 2/3
    assert report["primary"]["physical_sample_support"] == 2
    assert report["primary"]["positive_sample_element_pairs"] == 3
    assert report["per_element"]["Mg"]["recall"] == 1


def test_chemcam_runner_rejects_real_development_before_reading_inputs(tmp_path):
    script = Path(__file__).resolve().parents[2] / "scripts/validate_chemcam.py"
    spec = importlib.util.spec_from_file_location("chemcam_runner", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for field in ("development_sample_ids", "development_session_ids"):
        protocol = dict(
            test_locked_before_prediction=True,
            development_sample_ids=[],
            development_session_ids=[],
        )
        protocol[field] = ["external-test-member"]
        with pytest.raises(ValueError, match="external-only"):
            module.prepare(tmp_path, tmp_path, protocol, {})
