from __future__ import annotations

import numpy as np
import pytest
from spectralab_ml.data.shot_series import ShotSeriesPolicy, aggregate_shot_series


def test_aggregator_rejects_shape_outlier_and_preserves_auditable_receipt():
    wavelength = np.linspace(300, 304, 5)
    shots = np.array(
        [
            [1.0, 2.0, 5.0, 2.0, 1.0],
            [1.1, 2.1, 5.1, 2.1, 1.1],
            [1.0, 1.0, 1.0, 9.0, 1.0],
        ]
    )

    result = aggregate_shot_series(wavelength, shots, policy=ShotSeriesPolicy(min_snr=0))

    assert result.receipt["status"] == "completed"
    assert result.receipt["accepted_shot_indices"] == [0, 1]
    assert result.receipt["shots"][2]["status"] == "rejected_quality"
    assert result.receipt["shots"][2]["reasons"] == ["shape_outlier"]
    assert len(result.receipt["source_sha256"]) == 64
    np.testing.assert_allclose(result.intensity, [1.05, 2.05, 5.05, 2.05, 1.05])
    np.testing.assert_allclose(result.pointwise_mad, [0.05, 0.05, 0.05, 0.05, 0.05])


def test_aggregator_marks_insufficient_series_without_returning_a_spectrum():
    wavelength = np.array([300.0, 301.0, 302.0])
    shots = np.array([[1.0, 1.0, 1.0], [2.0, 3.0, 4.0]])

    result = aggregate_shot_series(wavelength, shots, policy=ShotSeriesPolicy(min_snr=2))

    assert result.intensity is result.pointwise_mad is None
    assert result.receipt["status"] == "insufficient_series"
    assert result.receipt["shots"][0]["reasons"] == ["zero_dynamic_range", "low_snr"]
    assert result.receipt["accepted_shot_indices"] == [1]


@pytest.mark.parametrize(
    ("wavelength", "shots"),
    [
        ([300.0], [[1.0]]),
        ([300.0, 300.0], [[1.0, 2.0]]),
        ([300.0, np.nan], [[1.0, 2.0]]),
        ([300.0, 301.0], [[1.0]]),
        ([300.0, 301.0], [[1.0, np.inf]]),
    ],
)
def test_aggregator_rejects_invalid_axis_or_shot_shapes(wavelength, shots):
    with pytest.raises(ValueError):
        aggregate_shot_series(np.asarray(wavelength), np.asarray(shots))


@pytest.mark.parametrize(
    "policy",
    [
        ShotSeriesPolicy(min_snr=-1),
        ShotSeriesPolicy(min_accepted_shots=0),
        ShotSeriesPolicy(saturation_ceiling=np.inf),
        ShotSeriesPolicy(outlier_mad_z=0),
    ],
)
def test_aggregator_rejects_invalid_policy_before_processing(policy):
    with pytest.raises(ValueError):
        aggregate_shot_series(np.array([300.0, 301.0]), np.array([[1.0, 2.0]]), policy=policy)


def test_declared_detector_ceiling_rejects_saturation_without_guessing_it():
    result = aggregate_shot_series(
        np.array([300.0, 301.0, 302.0]),
        np.array([[1.0, 10.0, 2.0], [1.0, 9.0, 2.0]]),
        policy=ShotSeriesPolicy(min_snr=0, saturation_ceiling=10.0),
    )

    assert result.receipt["shots"][0]["reasons"] == ["saturated"]
    assert result.receipt["shots"][1]["status"] == "accepted"
    assert result.receipt["policy"]["saturation_ceiling"] == 10.0


def test_flat_series_with_snr_disabled_has_finite_receipt_and_no_aggregate():
    import json

    result = aggregate_shot_series(
        np.arange(100.0, 200.0), np.ones((3, 100)), policy=ShotSeriesPolicy(min_snr=0)
    )
    assert result.receipt["status"] == "insufficient_series"
    assert result.receipt["accepted_shot_count"] == 0
    assert all(s["reasons"] == ["zero_dynamic_range"] for s in result.receipt["shots"])
    json.dumps(result.receipt, allow_nan=False)


def test_localised_spike_is_detected_even_when_most_pixels_match():
    wave = np.linspace(500, 501, 100)
    good = np.exp(-(((wave - 500.5) / 0.05) ** 2))
    bad = good.copy()
    bad[10] = 20
    result = aggregate_shot_series(
        wave, np.array([good, good, bad]), policy=ShotSeriesPolicy(min_snr=0)
    )
    assert result.receipt["accepted_shot_indices"] == [0, 1]
    np.testing.assert_allclose(result.intensity, good)


def test_finite_extreme_values_do_not_overflow_median_and_receipt_is_repeatable():
    wave = np.array([300.0, 301.0, 302.0])
    shots = np.array([[1e308, 1.5e308, 1e308], [1e308, 1.5e308, 1e308]])
    first = aggregate_shot_series(wave, shots, policy=ShotSeriesPolicy(min_snr=0))
    second = aggregate_shot_series(wave.copy(), shots.copy(), policy=ShotSeriesPolicy(min_snr=0))
    assert first.receipt == second.receipt
    np.testing.assert_array_equal(first.intensity, shots[0])
    np.testing.assert_array_equal(first.pointwise_mad, [0, 0, 0])
