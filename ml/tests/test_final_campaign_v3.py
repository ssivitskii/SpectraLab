"""Fictional inputs only; no candidate final spectra are opened."""

from __future__ import annotations

import copy
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pytest
from spectralab_ml.evaluation import _final_campaign_io as campaign_io
from spectralab_ml.evaluation import final_campaign_v3 as final


def sha(value):
    return hashlib.sha256(value.encode() if isinstance(value, str) else value).hexdigest()


def evidence():
    return {
        "source": "https://example.org/fictional-unit-test-only",
        "source_sha256": sha("evidence"),
    }


def add_sample(root, name, group, number):
    physical = f"physical:{name}"
    labels = {}
    for element, oxide in (("Fe", "FeOT"), ("Mg", "MgO")):
        if group.startswith("blank") and element not in group:
            labels[element] = {"state": "unknown", "basis": "unknown", "reason": "Not assayed"}
            continue
        label = {
            "state": "positive" if group == "positive" else "negative",
            "basis": "independent_upper_bound"
            if group.startswith("blank")
            else "independent_composition",
            "units": "oxide_wt_percent",
            "oxide": oxide,
            "independent": True,
            "evidence_id": f"assay:{name}:{element}",
            "physical_sample_id": physical,
            **evidence(),
        }
        if group.startswith("blank"):
            label.update(upper_bound=0.05, certified=True, method="fictional certified assay")
        else:
            label["value"] = 2 if group == "positive" else 0.05
        labels[element] = label
    intensity = (10 if group == "positive" else 1) + number / 100
    raw = "wavelength_nm,intensity\n" + "".join(f"{400 + i * 0.25},{intensity}\n" for i in range(5))
    path = f"{name}.csv"
    (root / path).write_text(raw)
    return {
        "measurement_id": name,
        "physical_sample_id": physical,
        "family_id": f"family:{name}",
        "session_id": f"session:{name}",
        "instrument_id": "fictional-instrument",
        "data_source_id": "fictional-source",
        "input_sha256": sha(raw),
        "raw_spectrum": {
            "uri": f"https://example.org/{name}.csv",
            "format": "csv",
            "numeric_spectrum_verified": True,
        },
        "local_spectrum": {
            "path": path,
            "sha256": sha(raw),
            "format": "csv",
            "columns": ["wavelength_nm", "intensity"],
            "rows": 5,
        },
        "license": {"id": "CC0-1.0", "confirmed": True, **evidence()},
        "axis": {
            "units": "nm",
            "medium": "vacuum",
            "coverage_intervals_nm": [[400, 401]],
            "max_sampling_step_nm": 0.25,
            "resolution_fwhm_nm": 0.1,
            **evidence(),
        },
        "control_type": "method_blank" if group.startswith("blank") else "sample",
        "labels": labels,
    }


@pytest.fixture
def campaign(tmp_path):
    (tmp_path / "profile.json").write_text('{"fixture_only":true}')
    (tmp_path / "predictor.py").write_text("# Fictional pinned test implementation\n")
    samples = [
        add_sample(tmp_path, f"{group}-{i}", group, number * 2 + i)
        for number, group in enumerate(("positive", "negative", "blankFe", "blankMg"))
        for i in range(2)
    ]
    manifest = {
        **final.empty_final_manifest("fictional-tests-only"),
        "selection_frozen_before_predictions": True,
        "data_source": {"id": "fictional-source", **evidence()},
        "profile": {
            "identity": sha("fictional-model"),
            "instrument_id": "fictional-instrument",
            "class_order": ["Na", "Mg", "Ca", "Fe"],
            "thresholds": {e: 0.5 for e in ("Na", "Mg", "Ca", "Fe")},
            "artifact_files": [
                {"path": "profile.json", "sha256": sha((tmp_path / "profile.json").read_bytes())}
            ],
            "units": "nm",
            "medium": "vacuum",
            "required_intervals_nm": [[400, 401]],
            "max_sampling_step_nm": 0.25,
            "max_resolution_fwhm_nm": 0.2,
            "require_positive_signal": True,
        },
        "implementation_files": [
            {"path": "predictor.py", "sha256": sha((tmp_path / "predictor.py").read_bytes())}
        ],
        "support_policy": {
            "ci_estimand": "cluster_any_error",
            "confidence_level": 0.95,
            # Loose only for tiny fictional tests; not a scientific recommendation.
            "max_wilson_half_width": 0.49,
            "min_support": {
                e: {g: dict.fromkeys(final.DIMENSIONS, 2) for g in final.GROUPS}
                for e in final.ELEMENTS
            },
        },
        "samples": samples,
    }
    registry = {field: [] for field in final.IDENTITIES}
    registry["aliases"] = []
    return manifest, registry, tmp_path


def codes(report):
    return {row["code"] for row in report["blockers"]}


def freeze(campaign, filename="freeze.json"):
    manifest, registry, root = campaign
    path = root / filename
    saved = final.freeze_final_campaign(path, manifest, registry, data_root=root)
    return path, saved["runtime_identity"]


def predict(metadata, axis, intensity):
    assert set(metadata) == {"measurement_id", "instrument_id", "axis"}
    assert not axis.flags.writeable and not intensity.flags.writeable
    return {e: 0.9 if intensity[0] > 5 else 0.1 for e in ("Na", "Mg", "Ca", "Fe")}


def evaluate(campaign, frozen, **kwargs):
    root = campaign[2]
    return final.evaluate_final_campaign(
        frozen[0],
        root / "receipts",
        data_root=root,
        runtime_identity=frozen[1],
        **({"predictor": predict} | kwargs),
    )


def test_separate_element_blanks_and_full_class_order(campaign):
    manifest, registry, root = campaign
    report = final.preflight_final_campaign(manifest, registry)
    assert report["status"] == "metadata_ready", report["blockers"]
    assert report["final_metrics"] is None and report["numerical_files_read"] == 0
    for element in final.ELEMENTS:
        assert report["support"][element]["blank"] == dict.fromkeys(final.DIMENSIONS, 2)
    checked = final.preflight_final_campaign(manifest, registry, data_root=root, verify_files=True)
    assert checked["status"] == "admitted" and checked["numerical_files_read"] == 8


def test_current_search_stays_blocked_without_spectrum_reads(monkeypatch):
    root = Path(__file__).resolve().parents[2]
    saved = json.loads((root / "data/reference/final_search_v3_admission.json").read_text())
    manifest = {**final.empty_final_manifest(), **saved["manifest"], "schema_version": 3}
    registry = {**saved["previously_seen_ids"], "aliases": []}
    monkeypatch.setattr(
        final, "verify_spectrum", lambda *_: pytest.fail("No final spectra may open")
    )
    first = final.preflight_final_campaign(manifest, registry, verify_files=True)
    assert first == final.preflight_final_campaign(manifest, registry, verify_files=True)
    assert first["status"] == "blocked" and first["final_metrics"] is None
    assert first["numerical_files_read"] == 0
    assert {"no_final_samples", "selection_not_frozen", "certified_element_blank_missing"} <= codes(
        first
    )


@pytest.mark.parametrize(
    "field,value,code",
    [
        ("evaluation_role", "cross_validation", "not_independent_final"),
        ("assessment_mode", "selective_refusal", "assessment_mode_unsupported"),
        ("selection_basis", "after_scores", "selection_basis_invalid"),
    ],
)
def test_cv_or_selective_metrics_cannot_be_relabeled(campaign, field, value, code):
    manifest, registry, _ = campaign
    manifest[field] = value
    assert code in codes(final.preflight_final_campaign(manifest, registry))


def test_blanks_cannot_replace_ordinary_negative_population(campaign):
    manifest, registry, _ = campaign
    manifest["samples"] = [
        s for s in manifest["samples"] if not s["measurement_id"].startswith("negative")
    ]
    report = final.preflight_final_campaign(manifest, registry)
    assert "insufficient_support" in codes(report)
    assert report["support"]["Fe"]["negative"]["physical"] == 0
    assert report["support"]["Fe"]["blank"]["physical"] == 2


def test_minimum_counts_cannot_override_precision_policy(campaign):
    manifest, registry, _ = campaign
    manifest["support_policy"]["max_wilson_half_width"] = 0.1
    report = final.preflight_final_campaign(manifest, registry)
    assert "precision_support_insufficient" in codes(report)
    assert "insufficient_support" not in codes(report)


def test_session_replicates_are_not_independent(campaign):
    manifest, registry, _ = campaign
    manifest["samples"][1]["session_id"] = manifest["samples"][0]["session_id"]
    report = final.preflight_final_campaign(manifest, registry)
    assert report["support"]["Fe"]["positive"]["physical"] == 2
    assert report["support"]["Fe"]["positive"]["independent_clusters"] == 1
    assert report["precision"]["Fe"]["positive"]["worst_case_wilson_half_width"] is None


def test_transitive_alias_hits_seen_registry(campaign):
    manifest, registry, _ = campaign
    name = manifest["samples"][0]["family_id"]
    registry["family_ids"] = ["historical-family"]
    registry["aliases"] = [
        {"field": "family_ids", "left": name, "right": "middle", **evidence()},
        {"field": "family_ids", "left": "middle", "right": "historical-family", **evidence()},
    ]
    assert "previously_seen_identity" in codes(final.preflight_final_campaign(manifest, registry))


def test_alias_conflicting_labels_fail(campaign):
    manifest, registry, _ = campaign
    registry["aliases"] = [
        {
            "field": "physical_sample_ids",
            "left": manifest["samples"][0]["physical_sample_id"],
            "right": manifest["samples"][2]["physical_sample_id"],
            **evidence(),
        }
    ]
    assert "inconsistent_replicate_labels" in codes(
        final.preflight_final_campaign(manifest, registry)
    )


@pytest.mark.parametrize(
    "mutation,code",
    [
        (lambda s: s.update(instrument_id="other"), "instrument_incompatible"),
        (
            lambda s: s["axis"].update(coverage_intervals_nm=[[400, 400.4], [400.6, 401]]),
            "coverage_gap",
        ),
        (lambda s: s["axis"].update(resolution_fwhm_nm=0.5), "sampling_or_resolution_incompatible"),
        (
            lambda s: s["axis"].update(max_sampling_step_nm=0.5),
            "sampling_or_resolution_incompatible",
        ),
        (lambda s: s["license"].update(confirmed=False), "data_license_unconfirmed"),
        (lambda s: s["labels"]["Fe"].update(value=0), "composition_state_mismatch"),
        (lambda s: s["local_spectrum"].update(path="../escape.csv"), "local_spectrum_invalid"),
    ],
)
def test_evidence_and_instrument_contract(campaign, mutation, code):
    manifest, registry, _ = campaign
    mutation(manifest["samples"][0])
    assert code in codes(final.preflight_final_campaign(manifest, registry))


@pytest.mark.parametrize(
    "raw",
    [
        "wavelength_nm,intensity\n400,1\n400.25,nan\n400.5,1\n400.75,1\n401,1\n",
        "wavelength_nm,intensity\n400,1\n400.25,1\n400.25,1\n400.75,1\n401,1\n",
        "wavelength_nm,intensity\n400,1\n400.1,1\n400.2,1\n400.9,1\n401,1\n",
        "wavelength_nm,intensity\n400,0\n400.25,0\n400.5,0\n400.75,0\n401,0\n",
        "wavelength_nm,intensity\n400,1\n401,1\n",
        "wavelength_nm,intensity\n" + "9" * 150_000 + ",1\n",
    ],
)
def test_actual_numerical_shape_finite_axis_gaps_and_signal(campaign, raw):
    manifest, registry, root = campaign
    sample = manifest["samples"][0]
    (root / sample["local_spectrum"]["path"]).write_text(raw)
    sample["input_sha256"] = sample["local_spectrum"]["sha256"] = sha(raw)
    assert final.preflight_final_campaign(manifest, registry)["status"] == "metadata_ready"
    checked = final.preflight_final_campaign(manifest, registry, data_root=root, verify_files=True)
    assert "numerical_input_invalid" in codes(checked) and checked["final_metrics"] is None


def test_freeze_does_not_open_spectra_and_is_exclusive(campaign, monkeypatch):
    original = final.read_bytes

    def read(path, limit):
        assert path.suffix != ".csv"
        return original(path, limit)

    monkeypatch.setattr(final, "read_bytes", read)
    path, _ = freeze(campaign)
    assert json.loads(path.read_text())["numerical_inputs_read"] is False
    with pytest.raises(FileExistsError):
        freeze(campaign)


def test_receipts_precede_reads_and_identical_replay_is_labeled(campaign, monkeypatch):
    frozen = freeze(campaign)
    calls, original = [], campaign_io.read_bytes

    def read(path, limit):
        if path.suffix == ".csv":
            assert list((campaign[2] / "receipts").glob("campaign-*.json"))
            calls.append(path.name)
        return original(path, limit)

    monkeypatch.setattr(campaign_io, "read_bytes", read)
    report = evaluate(campaign, frozen)
    assert report["status"] == "completed"
    assert report["evaluation_kind"] == "first_locked_evaluation"
    assert len(calls) == len(set(calls)) == 8
    assert report["final_metrics"]["Fe"]["positive"]["physical_recall"] == 1
    assert report["final_metrics"]["Mg"]["blank"]["physical_false_positive_rate"] == 0
    assert report["final_metrics"]["Fe"]["positive"]["physical_ci"] is None
    with pytest.raises(ValueError, match="already consumed"):
        evaluate(campaign, frozen)
    replay = evaluate(campaign, frozen, replay=True)
    assert replay["evaluation_kind"] == "locked_replay"
    assert replay["final_metrics"] == report["final_metrics"]


def test_changed_bytes_after_freeze_consume_without_scores(campaign):
    frozen = freeze(campaign)
    path = campaign[2] / campaign[0]["samples"][0]["local_spectrum"]["path"]
    path.write_bytes(path.read_bytes().replace(b"10.0", b"11.0"))
    report = evaluate(campaign, frozen)
    assert report["status"] == "blocked" and report["final_metrics"] is None
    assert report["completed_predictions"]["primary"] == 0
    with pytest.raises(ValueError, match="already consumed"):
        evaluate(campaign, frozen)


def test_renaming_campaign_or_partial_overlap_cannot_reuse_inputs(campaign):
    evaluate(campaign, freeze(campaign))
    manifest, _, root = campaign
    manifest["campaign_id"] = "renamed-campaign"
    manifest["samples"][0] = add_sample(root, "new-positive", "positive", 90)
    second = freeze(campaign, "another-freeze.json")
    with pytest.raises(ValueError, match="already consumed"):
        evaluate(campaign, second)
    with pytest.raises(ValueError, match="identity differs"):
        evaluate(campaign, second, replay=True)


def test_changed_alias_representative_cannot_erase_consumed_family(campaign):
    evaluate(campaign, freeze(campaign))
    manifest, registry, root = campaign
    old_family = manifest["samples"][0]["family_id"]
    manifest["campaign_id"] = "another-campaign"
    manifest["samples"] = [
        add_sample(root, f"new-{group}-{i}", group, 30 + number * 2 + i)
        for number, group in enumerate(("positive", "negative", "blankFe", "blankMg"))
        for i in range(2)
    ]
    manifest["samples"][0]["family_id"] = "aaa-new-alias"
    registry["aliases"] = [
        {"field": "family_ids", "left": "aaa-new-alias", "right": old_family, **evidence()}
    ]
    second = freeze(campaign, "another-freeze.json")
    with pytest.raises(ValueError, match="already consumed"):
        evaluate(campaign, second)


def test_runtime_or_artifact_change_blocks_before_consumption(campaign):
    frozen = freeze(campaign)
    altered = copy.deepcopy(frozen[1])
    altered["profile_identity"] = sha("other")
    with pytest.raises(ValueError, match="differs from the freeze"):
        evaluate(campaign, (frozen[0], altered))
    assert not (campaign[2] / "receipts").exists()
    (campaign[2] / "profile.json").write_text("changed")
    with pytest.raises(ValueError, match="checksum changed"):
        evaluate(campaign, frozen)
    assert not (campaign[2] / "receipts").exists()


def test_competing_evaluations_and_corrupt_receipt_fail_closed(campaign):
    frozen = freeze(campaign)

    def run():
        try:
            return evaluate(campaign, frozen)["status"]
        except ValueError:
            return "rejected"

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(lambda _: run(), range(2))) == ["completed", "rejected"]
    receipt = next((campaign[2] / "receipts").glob("campaign-*.json"))
    receipt.write_text("{")
    with pytest.raises(ValueError, match="remains consumed"):
        evaluate(campaign, frozen, replay=True)


def test_comparisons_share_one_read_and_separate_thresholds(campaign, monkeypatch):
    manifest, _, root = campaign
    comparison = copy.deepcopy(manifest["profile"])
    comparison["identity"] = sha("fictional-baseline")
    comparison["thresholds"]["Fe"] = float(np.nextafter(1.0, np.inf))
    manifest["comparisons"] = {"baseline": comparison}
    frozen = freeze(campaign)
    calls, original = [], campaign_io.read_bytes

    def read(path, limit):
        if path.suffix == ".csv":
            calls.append(path.name)
        return original(path, limit)

    monkeypatch.setattr(campaign_io, "read_bytes", read)
    report = final.evaluate_final_campaign(
        frozen[0],
        root / "receipts",
        data_root=root,
        runtime_identity=frozen[1],
        predictors={"primary": predict, "baseline": predict},
    )
    assert report["status"] == "completed" and len(calls) == 8
    assert report["comparison_metrics"]["baseline"]["Fe"]["positive"]["physical_recall"] == 0
    assert report["paired_physical_error_rate_delta"]["baseline"]["Fe"]["positive"] == 1
    assert report["selective_policy_evaluated"] is False


def test_selective_result_is_not_silently_scored_as_binary(campaign):
    report = evaluate(
        campaign,
        freeze(campaign),
        predictor=lambda *_: {"scores": {"Fe": 0.1}, "accepted": {"Fe": False}},
    )
    assert report["status"] == "blocked" and report["final_metrics"] is None


def test_air_conversion_verifies_original_and_effective_files(campaign):
    manifest, registry, root = campaign
    sample = manifest["samples"][0]
    original = b"fictional archived original air spectrum\n"
    (root / "original-air.csv").write_bytes(original)
    sample["input_sha256"] = sha(original)
    sample["raw_spectrum"]["local_path"] = "original-air.csv"
    sample["axis"]["medium"] = "air"
    sample["axis"]["conversion"] = {
        "target": "vacuum",
        "applied": True,
        "method": "fictional tested conversion",
        **evidence(),
        "output_sha256": sample["local_spectrum"]["sha256"],
        "coverage_intervals_nm": [[400, 401]],
        "max_sampling_step_nm": 0.25,
        "resolution_fwhm_nm": 0.1,
    }
    assert (
        final.preflight_final_campaign(manifest, registry, data_root=root, verify_files=True)[
            "status"
        ]
        == "admitted"
    )
    (root / "original-air.csv").write_text("changed source")
    assert "numerical_input_invalid" in codes(
        final.preflight_final_campaign(manifest, registry, data_root=root, verify_files=True)
    )


def test_physical_replicates_have_equal_weight_not_spectrum_weight(campaign):
    manifest, _, root = campaign
    original = manifest["samples"][0]
    duplicate = add_sample(root, "positive-replicate", "positive", 77)
    duplicate["physical_sample_id"] = original["physical_sample_id"]
    duplicate["family_id"] = original["family_id"]
    for element in final.ELEMENTS:
        duplicate["labels"][element]["physical_sample_id"] = original["physical_sample_id"]
    manifest["samples"].append(duplicate)

    def mixed(metadata, *_):
        value = 0.4 if metadata["measurement_id"] == original["measurement_id"] else 0.8
        return dict.fromkeys(("Na", "Mg", "Ca", "Fe"), value)

    report = evaluate(campaign, freeze(campaign), predictor=mixed)
    assert report["final_metrics"]["Fe"]["positive"]["physical_support"] == 2
    # (.4 + .8)/2 exceeds .5 for the first physical material; the other scores .8.
    assert report["final_metrics"]["Fe"]["positive"]["physical_recall"] == 1
    assert report["final_metrics"]["Fe"]["positive"]["independent_clusters"] == 2


def test_huge_json_numbers_and_extreme_confidence_fail_without_exceptions(campaign):
    manifest, registry, _ = campaign
    malformed = copy.deepcopy(manifest)
    malformed["samples"][0]["labels"]["Fe"]["value"] = 10**400
    result = final.preflight_final_campaign(malformed, registry)
    assert result["status"] == "blocked" and result["final_metrics"] is None
    manifest["support_policy"]["confidence_level"] = float(np.nextafter(1.0, 0))
    result = final.preflight_final_campaign(manifest, registry)
    assert "precision_support_insufficient" in codes(result)


def test_file_symlink_and_numeric_file_as_artifact_cannot_bypass_freeze(campaign):
    manifest, _, root = campaign
    first = root / manifest["samples"][0]["local_spectrum"]["path"]
    link = root / "spectrum-link.csv"
    link.symlink_to(first)
    manifest["samples"][0]["local_spectrum"]["path"] = link.name
    with pytest.raises(ValueError, match="Symbolic"):
        freeze(campaign)
    manifest["samples"][0]["local_spectrum"]["path"] = first.name
    manifest["profile"]["artifact_files"] = [
        {"path": first.name, "sha256": sha(first.read_bytes())}
    ]
    with pytest.raises(ValueError, match="numerical input"):
        freeze(campaign)


def test_changed_data_source_cannot_be_locked_replay(campaign):
    evaluate(campaign, freeze(campaign))
    campaign[0]["data_source"]["source_sha256"] = sha("different-data-source")
    changed = freeze(campaign, "changed-source-freeze.json")
    with pytest.raises(ValueError, match="identity differs"):
        evaluate(campaign, changed, replay=True)


def test_conflicting_sample_and_blank_identity_is_rejected(campaign):
    manifest, registry, _ = campaign
    sample, blank = manifest["samples"][2], manifest["samples"][4]
    registry["aliases"] = [
        {
            "field": "physical_sample_ids",
            "left": sample["physical_sample_id"],
            "right": blank["physical_sample_id"],
            **evidence(),
        }
    ]
    assert "conflicting_control_type" in codes(final.preflight_final_campaign(manifest, registry))


@pytest.mark.parametrize("composition_first", [False, True])
def test_mixed_blank_evidence_cannot_change_denominator_with_row_order(campaign, composition_first):
    manifest, registry, root = campaign
    comparison = copy.deepcopy(manifest["profile"])
    manifest["comparisons"] = {"baseline": comparison}
    for i, original in enumerate(list(manifest["samples"][4:6])):
        replica = add_sample(root, f"blankFe-composition-{i}", "blankFe", 80 + i)
        replica["physical_sample_id"] = original["physical_sample_id"]
        replica["family_id"] = original["family_id"]
        label = replica["labels"]["Fe"]
        label["physical_sample_id"] = original["physical_sample_id"]
        label["basis"] = "independent_composition"
        label["value"] = 0.05
        del label["upper_bound"]
        if composition_first:
            manifest["samples"].insert(0, replica)
        else:
            manifest["samples"].append(replica)
    report = final.preflight_final_campaign(manifest, registry)
    assert "inconsistent_blank_qualification" in codes(report)
    assert report["status"] == "blocked" and report["final_metrics"] is None
    with pytest.raises(ValueError, match="blocked"):
        freeze(campaign)


@pytest.mark.parametrize("medium", ["vacuum", "air"])
def test_historical_effective_checksum_blocks_before_numerical_reads(campaign, monkeypatch, medium):
    manifest, registry, root = campaign
    sample = manifest["samples"][0]
    registry["input_sha256s"] = [sample["local_spectrum"]["sha256"]]
    if medium == "air":
        sample["input_sha256"] = sha("new-original-air-input")
        sample["raw_spectrum"]["local_path"] = "original-air.csv"
        sample["axis"]["medium"] = "air"
        sample["axis"]["conversion"] = {
            "target": "vacuum",
            "applied": True,
            "method": "fictional conversion",
            **evidence(),
            "output_sha256": sample["local_spectrum"]["sha256"],
            "coverage_intervals_nm": [[400, 401]],
            "max_sampling_step_nm": 0.25,
            "resolution_fwhm_nm": 0.1,
        }
    monkeypatch.setattr(final, "verify_spectrum", lambda *_, **__: pytest.fail("Seen file opened"))
    report = final.preflight_final_campaign(manifest, registry, data_root=root, verify_files=True)
    assert "previously_seen_evaluated_input" in codes(report)
    assert report["status"] == "blocked" and report["numerical_files_read"] == 0


def test_public_freeze_loader_rejects_tampered_artifact_before_any_artifact_read(
    campaign, monkeypatch
):
    frozen, _ = freeze(campaign)
    changed = json.loads(frozen.read_text())
    sample = campaign[0]["samples"][0]
    changed["manifest"]["profile"]["artifact_files"] = [
        {"path": sample["local_spectrum"]["path"], "sha256": sample["input_sha256"]}
    ]
    frozen.write_text(json.dumps(changed))
    monkeypatch.setattr(
        final, "read_bytes", lambda *_: pytest.fail("Artifact opened before freeze check")
    )
    with pytest.raises(ValueError, match="changed immutable"):
        final.load_final_freeze(frozen, data_root=campaign[2])


@pytest.mark.parametrize("verify_pinned", [False, True])
def test_hardlink_artifact_is_rejected_by_stat_before_read(campaign, monkeypatch, verify_pinned):
    frozen, _ = freeze(campaign)
    root = campaign[2]
    artifact = root / "profile.json"
    artifact.unlink()
    artifact.hardlink_to(root / campaign[0]["samples"][0]["local_spectrum"]["path"])
    monkeypatch.setattr(
        final, "read_bytes", lambda *_: pytest.fail("Hardlink opened before receipt")
    )
    with pytest.raises(ValueError, match="hardlink"):
        final.load_final_freeze(frozen, data_root=root, verify_pinned=verify_pinned)


def test_declared_artifact_hash_matching_any_numerical_input_is_rejected(campaign, monkeypatch):
    manifest, _, root = campaign
    manifest["profile"]["artifact_files"][0]["sha256"] = manifest["samples"][0]["input_sha256"]
    monkeypatch.setattr(
        final, "read_bytes", lambda *_: pytest.fail("Artifact read before separation")
    )
    with pytest.raises(ValueError, match="numerical input"):
        freeze(campaign)
    assert not (root / "freeze.json").exists()


def test_public_loader_returns_full_validated_freeze_for_cli(campaign):
    frozen, identity = freeze(campaign)
    loaded = final.load_final_freeze(frozen, data_root=campaign[2])
    assert loaded["runtime_identity"] == identity
    assert loaded["freeze_sha256"] == json.loads(frozen.read_text())["freeze_sha256"]


def test_air_callback_receives_effective_vacuum_axis_without_double_conversion(campaign):
    manifest, _, root = campaign
    sample = manifest["samples"][0]
    original = b"fictional archived air input\n"
    (root / "original-air.csv").write_bytes(original)
    sample["input_sha256"] = sha(original)
    sample["raw_spectrum"]["local_path"] = "original-air.csv"
    sample["axis"]["medium"] = "air"
    sample["axis"]["conversion"] = {
        "target": "vacuum",
        "applied": True,
        "method": "fictional conversion",
        **evidence(),
        "output_sha256": sample["local_spectrum"]["sha256"],
        "coverage_intervals_nm": [[400, 401]],
        "max_sampling_step_nm": 0.25,
        "resolution_fwhm_nm": 0.1,
    }
    observed = []

    def callback(metadata, axis, intensity):
        assert metadata["axis"]["medium"] == "vacuum"
        if metadata["measurement_id"] == sample["measurement_id"]:
            assert metadata["original_axis"]["medium"] == "air"
            assert axis[0] == 400
            observed.append(metadata)
        return dict.fromkeys(("Na", "Mg", "Ca", "Fe"), 0.2)

    assert evaluate(campaign, freeze(campaign), predictor=callback)["status"] == "completed"
    assert len(observed) == 1


def test_converted_or_other_spectrum_cannot_be_independent_chemical_evidence(campaign):
    manifest, registry, _ = campaign
    first, second = manifest["samples"][:2]
    first["labels"]["Fe"]["source_sha256"] = second["local_spectrum"]["sha256"]
    report = final.preflight_final_campaign(manifest, registry)
    assert "spectral_evidence_not_independent" in codes(report)
    assert report["numerical_files_read"] == 0
