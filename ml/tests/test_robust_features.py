import numpy as np
import pytest
from spectralab_ml.models.calibration import (
    TemperatureScaledEstimator,
    fit_temperatures,
    score_logits,
)
from spectralab_ml.models.line_features import (
    GradientBoostingOVR,
    LineWindowFeatures,
    ScaledLogisticOVR,
    select_line_positions,
)
from spectralab_ml.preprocessing.robust import RobustBandTransform, als_baseline, band_slices


def test_als_recovers_smooth_baseline_under_lines():
    grid = np.linspace(0, 100, 1001)
    baseline = 0.5 + 0.01 * grid + 0.2 * np.sin(grid / 30)
    lines = sum(
        height * np.exp(-0.5 * ((grid - center) / 0.3) ** 2)
        for center, height in ((20, 5), (45, 2), (70, 8))
    )
    estimate = als_baseline(baseline + lines, smoothness=1e6)
    assert np.max(np.abs(estimate - baseline)) < 0.05
    with pytest.raises(ValueError):
        als_baseline(np.ones(3))
    with pytest.raises(ValueError):
        als_baseline(np.r_[np.ones(10), np.nan])


def test_band_transform_normalizes_each_band_and_never_bridges_gaps():
    mask = np.r_[np.ones(200, bool), np.zeros(50, bool), np.ones(300, bool)]
    assert band_slices(mask) == [slice(0, 200), slice(250, 550)]
    rng = np.random.default_rng(0)
    X = np.zeros((2, len(mask)))
    X[:, 100] = 10
    X[:, 400] = 1000
    X += rng.normal(0, 0.01, X.shape) + 3
    X[:, ~mask] = 0
    features = RobustBandTransform(smoothness=1e5).transform(X, mask)
    assert features.shape == (2, int(mask.sum()))
    for band in (slice(0, 200), slice(200, 500)):
        assert np.sum(np.clip(features[:, band], 0, None), axis=1) == pytest.approx(1.0)
        assert features[0, band].max() > 0.5
    with pytest.raises(ValueError):
        RobustBandTransform(normalization="global")


def test_line_windows_measure_local_prominence_inside_one_band():
    grid = np.round(np.arange(380.0, 420.0, 0.1), 3)
    mask = (grid < 399.5) | (grid > 401)
    lines = LineWindowFeatures((390.0, 405.0), grid, mask)
    spectrum = np.full(len(grid), 0.2)
    spectrum[np.argmin(np.abs(grid - 390.0))] += 1.0
    masked = spectrum[mask][None, :]
    features = lines.transform(masked)
    assert features[0, 0] == pytest.approx(np.arcsinh(1000.0))
    assert features[0, 1] == pytest.approx(0.0)
    with pytest.raises(ValueError, match="outside"):
        LineWindowFeatures((400.0,), grid, mask)
    templates = np.zeros((1, len(grid)))
    templates[0, [50, 52, 150]] = [1.0, 0.9, 0.5]
    positions = select_line_positions(templates, grid, ("X",), per_element=5)
    assert [w for _, w in positions] == [385.0, 395.0]


def test_temperature_scaling_recovers_overconfidence_and_keeps_ranking():
    rng = np.random.default_rng(1)
    true_logits = rng.normal(0, 2, size=(4000, 2))
    y = (rng.random(true_logits.shape) < 1 / (1 + np.exp(-true_logits))).astype(int)
    y[:, 1] = 1
    temperatures, fallback = fit_temperatures(5 * true_logits, y)
    assert temperatures[0] == pytest.approx(5.0, rel=0.1)
    assert temperatures[1] == 1.0 and fallback == [1]
    assert np.all(np.isfinite(score_logits("random_forest", np.array([[0.0, 1.0]]))))


def test_temperature_scaled_estimator_and_line_models():
    rng = np.random.default_rng(2)
    X = rng.normal(size=(300, 4))
    noise = 0.5 * rng.normal(size=(300, 2))
    y = np.column_stack([X[:, 0] + noise[:, 0] > 0, X[:, 1] + noise[:, 1] > 0.5]).astype(int)
    for base in (ScaledLogisticOVR(seed=0), GradientBoostingOVR(seed=0, max_iter=30)):
        model = TemperatureScaledEstimator(base).fit(X[:200], y[:200])
        with pytest.raises(ValueError, match="not been fitted"):
            model.scores(X[200:])
        model.fit_calibration(X[200:250], y[200:250])
        probabilities = model.scores(X[250:])
        raw = base.scores(X[250:])
        assert np.all(np.isfinite(probabilities))
        assert np.all((probabilities >= 0) & (probabilities <= 1))
        for column in range(2):
            order = np.argsort(raw[:, column])
            assert np.all(np.diff(probabilities[order, column]) >= -1e-12)
