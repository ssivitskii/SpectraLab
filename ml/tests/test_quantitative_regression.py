from __future__ import annotations

import numpy as np
import pytest
from spectralab_ml.models.quantitative import (
    QuantitativeAssayRegressor,
    get_default_quantitative_regressor,
)


def test_quantitative_prediction_defaults() -> None:
    """Verify quantitative prediction returns valid oxide targets with conformal bounds."""
    regressor = get_default_quantitative_regressor()

    # Generate synthetic emission spectrum
    grid = np.linspace(250.0, 550.0, 15000)
    flux = np.full_like(grid, 1.0)
    # Add synthetic peaks for Fe and Mg
    flux[np.abs(grid - 373.593) < 0.05] += 10.0
    flux[np.abs(grid - 285.296) < 0.05] += 8.0

    report = regressor.predict(grid, flux, confidence_level=0.95)

    assert report.model_id == "ridge-conformal-v1"
    assert len(report.predictions) >= 6
    assert report.confidence_level == 0.95

    # Check each prediction
    species_found = {p.species for p in report.predictions}
    assert "FeOT" in species_found
    assert "MgO" in species_found

    for p in report.predictions:
        assert p.lower_wt_percent <= p.value_wt_percent or p.status == "below_lod"
        assert p.value_wt_percent <= p.upper_wt_percent
        assert p.lower_wt_percent >= 0.0
        assert p.status in ("in_domain", "near_boundary", "out_of_domain", "below_lod")
        assert p.unit == "wt_percent"

    # Serialization test
    as_dict = report.to_dict()
    assert "predictions" in as_dict
    assert len(as_dict["predictions"]) == len(report.predictions)


def test_conformal_interval_scaling_with_confidence() -> None:
    """Verify higher confidence levels yield wider conformal prediction intervals."""
    regressor = QuantitativeAssayRegressor()
    grid = np.linspace(250.0, 550.0, 6000)
    flux = np.ones_like(grid)
    flux[np.abs(grid - 373.593) < 0.05] += 5.0

    rep_90 = regressor.predict(grid, flux, confidence_level=0.90)
    rep_95 = regressor.predict(grid, flux, confidence_level=0.95)
    rep_99 = regressor.predict(grid, flux, confidence_level=0.99)

    fe_90 = next(p for p in rep_90.predictions if p.species == "FeOT")
    fe_95 = next(p for p in rep_95.predictions if p.species == "FeOT")
    fe_99 = next(p for p in rep_99.predictions if p.species == "FeOT")

    width_90 = fe_90.upper_wt_percent - fe_90.lower_wt_percent
    width_95 = fe_95.upper_wt_percent - fe_95.lower_wt_percent
    width_99 = fe_99.upper_wt_percent - fe_99.lower_wt_percent

    assert width_90 <= width_95 + 1e-6
    assert width_95 <= width_99 + 1e-6


def test_applicability_domain_ood_detection() -> None:
    """Verify OOD detection triggers on extreme feature inputs."""
    regressor = QuantitativeAssayRegressor()
    grid = np.linspace(250.0, 550.0, 5000)

    # An extreme non-physical uncalibrated peak 10000x normal
    flux = np.ones_like(grid)
    flux[np.abs(grid - 373.593) < 0.05] += 1e6

    report = regressor.predict(grid, flux)
    fe_pred = next(p for p in report.predictions if p.species == "FeOT")

    assert fe_pred.status in ("out_of_domain", "near_boundary")
    assert fe_pred.leverage > 0.0
    if fe_pred.status == "out_of_domain":
        assert fe_pred.warning is not None


def test_air_to_vacuum_consistency() -> None:
    """Verify medium='air' automatically converts wavelengths properly."""
    regressor = QuantitativeAssayRegressor()
    from spectralab_ml.data.wavelengths import vacuum_to_air

    vac_grid = np.linspace(250.0, 550.0, 5000)
    flux = np.ones_like(vac_grid)
    flux[np.abs(vac_grid - 373.593) < 0.05] += 10.0

    air_grid = vacuum_to_air(vac_grid)

    rep_vac = regressor.predict(vac_grid, flux, medium="vacuum")
    rep_air = regressor.predict(air_grid, flux, medium="air")

    fe_vac = next(p for p in rep_vac.predictions if p.species == "FeOT")
    fe_air = next(p for p in rep_air.predictions if p.species == "FeOT")

    assert fe_vac.value_wt_percent == pytest.approx(fe_air.value_wt_percent, rel=1e-3)
