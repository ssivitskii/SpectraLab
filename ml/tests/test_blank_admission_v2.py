from __future__ import annotations

import copy
import hashlib

import pytest
from spectralab_ml.evaluation.blank_admission_v2 import admit_final_campaign


def sha(value):
    return hashlib.sha256(value.encode()).hexdigest()


def evidence():
    return {
        "source": "https://example.org/synthetic-certificate",
        "source_sha256": sha("certificate"),
    }


def sample(name="blank", control="method_blank"):
    return {
        "measurement_id": name,
        "physical_sample_id": f"physical:{name}",
        "family_id": f"family:{name}",
        "session_id": f"session:{name}",
        "instrument_id": "synthetic-instrument",
        "input_sha256": sha(name),
        "raw_spectrum": {
            "uri": f"https://example.org/{name}.csv",
            "format": "csv",
            "numeric_spectrum_verified": True,
        },
        "license": {"id": "CC0-1.0", "confirmed": True, **evidence()},
        "axis": {"units": "nm", "medium": "vacuum", **evidence()},
        "control_type": control,
        "labels": {
            e: {
                "state": "negative",
                "basis": "independent_upper_bound",
                "upper_bound": 0.05,
                "units": "oxide_wt_percent",
                "oxide": oxide,
                "certified": True,
                "method": "independent assay",
                "independent": True,
                "evidence_id": f"assay:{name}:{e}",
                "physical_sample_id": f"physical:{name}",
                **evidence(),
            }
            for e, oxide in {"Fe": "FeOT", "Mg": "MgO"}.items()
        },
    }


@pytest.fixture
def manifest():
    return {
        "schema_version": 1,
        "campaign_id": "synthetic-unit-test-only",
        "selection_frozen_before_predictions": True,
        "estimand": "operational_above_threshold",
        "samples": [sample()],
    }


@pytest.fixture
def seen():
    return {
        field: []
        for field in (
            "measurement_ids",
            "physical_sample_ids",
            "family_ids",
            "session_ids",
            "input_sha256s",
            "assay_ids",
        )
    }


def codes(result):
    return {row["code"] for row in result["blockers"]}


def test_certified_real_blank_admitted_without_claiming_metrics(manifest, seen):
    result = admit_final_campaign(manifest, seen)
    assert result["status"] == "admitted"
    assert result["blockers"] == []
    assert result["final_metrics"] is None
    assert result["metrics_status"] == "pending_final_evaluation"
    assert result["verified_physical_support"]["Fe"] == {"positive": 0, "negative": 1, "unknown": 0}
    assert result["certified_blank_family_ids"] == ["family:blank"]


@pytest.mark.parametrize(
    "registry_field,record_field",
    [
        ("measurement_ids", "measurement_id"),
        ("physical_sample_ids", "physical_sample_id"),
        ("family_ids", "family_id"),
        ("session_ids", "session_id"),
        ("input_sha256s", "input_sha256"),
    ],
)
def test_every_seen_identity_blocks_old_development_and_old_tests(
    manifest, seen, registry_field, record_field
):
    seen[registry_field] = [manifest["samples"][0][record_field]]
    result = admit_final_campaign(manifest, seen)
    assert result["status"] == "blocked"
    assert "previously_seen_identity" in codes(result)


def test_assay_row_reuse_blocked_but_shared_source_table_allowed(manifest, seen):
    second = sample("another", "matrix_blank")
    manifest["samples"].append(second)
    assert admit_final_campaign(manifest, seen)["status"] == "admitted"
    seen["assay_ids"] = [second["labels"]["Fe"]["evidence_id"]]
    assert "previously_seen_assay" in codes(admit_final_campaign(manifest, seen))
    seen["assay_ids"] = []
    second["labels"]["Fe"]["evidence_id"] = "assay:blank:Fe"
    assert "assay_material_alias" in codes(admit_final_campaign(manifest, seen))


@pytest.mark.parametrize("control", ["dark", "no_laser", "sample"])
def test_dark_or_nonblank_never_satisfies_required_blank(manifest, seen, control):
    manifest["samples"][0]["control_type"] = control
    assert "certified_blank_family_missing" in codes(admit_final_campaign(manifest, seen))


@pytest.mark.parametrize(
    "field,value,expected",
    [
        ("upper_bound", 0, "upper_bound_unverified"),
        ("upper_bound", -1, "upper_bound_unverified"),
        ("upper_bound", float("nan"), "upper_bound_unverified"),
        ("upper_bound", True, "upper_bound_unverified"),
        ("upper_bound", 0.100001, "upper_bound_state_mismatch"),
        ("upper_bound", "0.05", "upper_bound_unverified"),
        ("certified", False, "upper_bound_unverified"),
        ("method", "", "upper_bound_unverified"),
        ("source_sha256", "", "evidence_hash_missing"),
        ("physical_sample_id", "another", "independent_assay_unverified"),
        ("independent", False, "independent_assay_unverified"),
        ("units", "elemental_wt_percent", "composition_units_mismatch"),
        ("oxide", "Fe2O3", "composition_units_mismatch"),
        ("basis", "libs_nondetection", "unsupported_label_basis"),
    ],
)
def test_invalid_or_dependent_blank_evidence_blocks(manifest, seen, field, value, expected):
    manifest["samples"][0]["labels"]["Fe"][field] = value
    result = admit_final_campaign(manifest, seen)
    assert result["status"] == "blocked"
    assert expected in codes(result)
    assert "certified_blank_family_missing" in codes(result)


def test_bound_boundary_is_inclusive_but_nominal_zero_is_not_absence(manifest, seen):
    for label in manifest["samples"][0]["labels"].values():
        label["upper_bound"] = 0.1
    assert admit_final_campaign(manifest, seen)["status"] == "admitted"
    item = sample("nominal", "sample")
    for label in item["labels"].values():
        label.update(basis="independent_composition", value=0, state="negative")
    manifest["samples"].append(item)
    assert "composition_state_mismatch" in codes(admit_final_campaign(manifest, seen))
    for label in item["labels"].values():
        label["state"] = "unknown"
    assert admit_final_campaign(manifest, seen)["status"] == "admitted"


@pytest.mark.parametrize(
    "value,state",
    [
        (0, "unknown"),
        (0.01, "negative"),
        (0.1, "negative"),
        (0.10001, "unknown"),
        (0.99, "unknown"),
        (1, "positive"),
    ],
)
def test_fixed_three_state_composition_policy(manifest, seen, value, state):
    item = sample("standard", "sample")
    for label in item["labels"].values():
        label.update(basis="independent_composition", value=value, state=state)
    manifest["samples"].append(item)
    assert admit_final_campaign(manifest, seen)["status"] == "admitted"
    assert admit_final_campaign(manifest, seen)["verified_physical_support"]["Fe"][state] >= 1


def test_explicit_unknown_allowed_but_missing_element_blocks(manifest, seen):
    item = sample("unknown", "sample")
    item["labels"]["Fe"] = {"state": "unknown", "basis": "unknown", "reason": "Not assayed"}
    manifest["samples"].append(item)
    assert admit_final_campaign(manifest, seen)["status"] == "admitted"
    del item["labels"]["Mg"]
    assert "label_missing" in codes(admit_final_campaign(manifest, seen))


def test_published_composition_estimate_cannot_replace_certified_blank_bound(manifest, seen):
    for label in manifest["samples"][0]["labels"].values():
        label.update(basis="independent_composition", value=0.01)
    assert "certified_blank_family_missing" in codes(admit_final_campaign(manifest, seen))


def test_air_axis_needs_documented_applied_conversion(manifest, seen):
    axis = manifest["samples"][0]["axis"]
    axis["medium"] = "air"
    assert "air_conversion_unverified" in codes(admit_final_campaign(manifest, seen))
    axis["conversion"] = {
        "target": "vacuum",
        "applied": True,
        "method": "documented test formula",
        "output_sha256": sha("converted"),
        **evidence(),
    }
    assert admit_final_campaign(manifest, seen)["status"] == "admitted"
    del axis["conversion"]["source_sha256"]
    assert "evidence_hash_missing" in codes(admit_final_campaign(manifest, seen))


@pytest.mark.parametrize(
    "section,field,value,expected",
    [
        ("raw_spectrum", "format", "png", "numeric_raw_unverified"),
        ("raw_spectrum", "numeric_spectrum_verified", False, "numeric_raw_unverified"),
        ("raw_spectrum", "uri", "", "numeric_raw_unverified"),
        ("license", "confirmed", False, "data_license_unconfirmed"),
        ("license", "source_sha256", "", "evidence_hash_missing"),
        ("axis", "medium", "unknown", "wavelength_medium_unconfirmed"),
        ("axis", "units", "Angstrom", "wavelength_medium_unconfirmed"),
    ],
)
def test_raw_license_axis_requirements(manifest, seen, section, field, value, expected):
    manifest["samples"][0][section][field] = value
    assert expected in codes(admit_final_campaign(manifest, seen))


def test_duplicate_spectra_and_conflicting_replicate_labels_block(manifest, seen):
    manifest["samples"].append(copy.deepcopy(manifest["samples"][0]))
    assert "duplicate_spectrum" in codes(admit_final_campaign(manifest, seen))
    duplicate = manifest["samples"][1]
    duplicate["measurement_id"], duplicate["input_sha256"] = "replicate", sha("replicate")
    assert admit_final_campaign(manifest, seen)["verified_physical_support"]["Mg"]["negative"] == 1
    duplicate["labels"]["Mg"] = {"state": "unknown", "basis": "unknown", "reason": "not assayed"}
    assert "inconsistent_replicate_labels" in codes(admit_final_campaign(manifest, seen))


def test_registry_and_freeze_missing_cannot_silently_admit(manifest, seen):
    assert "seen_registry_incomplete" in codes(admit_final_campaign(manifest, {}))
    manifest["selection_frozen_before_predictions"] = False
    assert "selection_not_frozen" in codes(admit_final_campaign(manifest, seen))
    manifest["estimand"] = "strict_presence"
    assert "invalid_estimand" in codes(admit_final_campaign(manifest, seen))


def test_empty_or_malformed_manifest_returns_blockers_not_final_scores(seen):
    for value in (
        None,
        {},
        [],
        {"samples": [None, {"control_type": [], "labels": {"Fe": {"state": []}}}]},
    ):
        result = admit_final_campaign(value, seen)
        assert result["status"] == "blocked"
        assert result["final_metrics"] is None


def test_source_spectrum_not_accepted_as_its_own_independent_assay(manifest, seen):
    item = manifest["samples"][0]
    item["labels"]["Fe"]["source_sha256"] = item["input_sha256"]
    assert "spectral_evidence_not_independent" in codes(admit_final_campaign(manifest, seen))


def test_hashes_bind_manifest_and_all_seen_registry(manifest, seen):
    first = admit_final_campaign(manifest, seen)
    seen["family_ids"] = ["other-family"]
    second = admit_final_campaign(manifest, seen)
    assert first["manifest_sha256"] == second["manifest_sha256"]
    assert first["previously_seen_sha256"] != second["previously_seen_sha256"]
    manifest["samples"][0]["axis"]["source"] = "another-source"
    assert first["manifest_sha256"] != admit_final_campaign(manifest, seen)["manifest_sha256"]


def test_whitespace_cannot_hide_previously_seen_identity(manifest, seen):
    seen["family_ids"] = ["family:blank"]
    manifest["samples"][0]["family_id"] = " family:blank "
    result = admit_final_campaign(manifest, seen)
    assert result["status"] == "blocked"
    assert "identity_missing" in codes(result)


def test_impossible_oxide_percent_rejected(manifest, seen):
    item = sample("impossible", "sample")
    item["labels"]["Fe"].update(basis="independent_composition", value=101, state="positive")
    manifest["samples"].append(item)
    assert "invalid_composition" in codes(admit_final_campaign(manifest, seen))
