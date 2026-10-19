from __future__ import annotations

import pytest
from spectralab_ml.data.assays import normalize_assay, normalize_assay_manifest


def assay(**overrides):
    return {
        "element": "Fe",
        "reported_species": "FeOT",
        "units": "wt_percent",
        "value": 0.05,
        **overrides,
    }


def test_explicit_mass_and_oxide_conversions_match_published_intake_examples():
    kd_fe = normalize_assay(
        assay(reported_species="Fe", total_element=True, units="mg_per_kg", value=180)
    )
    kd_mg = normalize_assay(assay(element="Mg", reported_species="Mg", units="mg_per_kg", value=21))
    pds = normalize_assay(assay(reported_species="Fe2O3T", value=0.03))
    assert kd_fe["value"] == pytest.approx(0.023156809024979852)
    assert kd_mg["value"] == pytest.approx(0.0034823451964616334)
    assert pds["value"] == pytest.approx(0.02699430761427041)
    assert kd_fe["operational_state"] == "negative"
    assert kd_fe["qualified_blank"] is False
    assert normalize_assay(assay(units="mass_fraction", value=0.0005))["value"] == 0.05


@pytest.mark.parametrize("species", ["Fe", "FeO", "Fe2O3"])
def test_csa_iron_basis_is_never_guessed(species):
    with pytest.raises(ValueError, match="total_element"):
        normalize_assay(assay(reported_species=species))
    assert (
        normalize_assay(assay(reported_species=species, total_element=True))["operational_state"]
        == "negative"
    )


@pytest.mark.parametrize(
    "value,state",
    [(0, "unknown"), (0.1, "negative"), (0.10001, "unknown"), (0.999, "unknown"), (1, "positive")],
)
def test_operational_boundaries_and_nominal_zero(value, state):
    assert normalize_assay(assay(value=value))["operational_state"] == state


def test_reported_interval_crossing_a_threshold_stays_unknown():
    assert (
        normalize_assay(
            assay(
                value=1.05, uncertainty=0.1, uncertainty_type="expanded_absolute", coverage_factor=2
            )
        )["operational_state"]
        == "unknown"
    )
    assert (
        normalize_assay(
            assay(
                value=0.05,
                uncertainty=0.06,
                uncertainty_type="expanded_absolute",
                coverage_factor=2,
            )
        )["operational_state"]
        == "unknown"
    )
    assert (
        normalize_assay(
            assay(
                value=0.05,
                uncertainty=0.01,
                uncertainty_type="expanded_absolute",
                coverage_factor=2,
            )
        )["operational_state"]
        == "negative"
    )


def test_upper_bound_classification_is_conditional_and_not_a_blank_certificate():
    result = normalize_assay(assay(value=None, upper_bound=0.05, qualifier="certified"))
    assert result["operational_state"] == "negative"
    assert result["qualified_blank"] is False
    assert result["input"]["qualifier"] == "certified"
    assert normalize_assay(assay(value=None, upper_bound=0))["operational_state"] == "unknown"


@pytest.mark.parametrize("qualifier", ["estimated", "informational", "not_reported"])
def test_informational_or_missing_values_are_not_promoted_to_labels(qualifier):
    assert normalize_assay(assay(qualifier=qualifier))["operational_state"] == "unknown"


@pytest.mark.parametrize(
    "overrides",
    [
        {"value": True},
        {"value": "0.05"},
        {"value": float("nan")},
        {"value": -1},
        {"value": 101},
        {"value": 2, "units": "mass_fraction"},
        {"upper_bound": 0.01},
        {"units": "ppm"},
        {"units": "mg_per_litre"},
        {"units": "ng_per_cm2"},
        {"reported_species": "FeO", "total_element": "yes"},
        {"element": "H"},
        {"element": "Mg", "reported_species": "FeOT"},
        {"typo": 1},
        {"uncertainty": 0.01},
        {"coverage_factor": 2},
        {"uncertainty": -0.01, "uncertainty_type": "expanded_absolute", "coverage_factor": 2},
        {"uncertainty": 0.01, "uncertainty_type": "expanded_absolute", "coverage_factor": 0},
    ],
)
def test_ambiguous_invalid_and_non_mass_units_fail_closed(overrides):
    with pytest.raises(ValueError):
        normalize_assay(assay(**overrides))


def test_elemental_oxide_equivalent_above_100_is_not_silently_clipped():
    result = normalize_assay(assay(reported_species="Fe", total_element=True, value=100))
    assert result["value"] > 100
    assert result["operational_state"] == "positive"


def test_batch_keeps_rejected_rows_and_does_not_hide_errors():
    result = normalize_assay_manifest(
        {"schema_version": 1, "assays": [assay(), assay(units="ppm")]}
    )
    assert result["status"] == "blocked"
    assert result["normalized_count"] == 1
    assert result["blocked_count"] == 1
    assert result["rows"][1]["row_index"] == 1
    assert result["qualified_blank_count"] == 0
