"""Full installed CLI integration on FICTIONAL fixtures, never real final data.

The deliberately constant model has FPR=1. These checks establish execution and
receipt contracts only; fictional assay/license statements qualify no real data.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
import pytest
from spectralab_ml.config import repository_root
from spectralab_ml.data.reference import ReferenceCatalog, SpectralLine
from spectralab_ml.evaluation.final_campaign_v3 import (
    DIMENSIONS,
    ELEMENTS,
    GROUPS,
    IDENTITIES,
    empty_final_manifest,
)
from spectralab_ml.models.line_adapter_v2 import LineAdapterV2
from spectralab_ml.models.line_adapter_v3 import (
    BANDS,
    CLASSES,
    DECISION_POLICY,
    GRID,
    LineAdapterV3,
    feature_count,
    feature_plan,
)


def checksum(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fictional_evidence() -> dict:
    return {
        "source": "https://example.org/fictional-cli-test-evidence-not-a-real-dataset",
        "source_sha256": hashlib.sha256(b"FICTIONAL CLI FIXTURE ONLY").hexdigest(),
    }


def write_profile(directory: Path, profile_version: int | str = 3) -> tuple[Path, dict]:
    lines = [
        SpectralLine(e, 1, w, "vacuum", "observed", 1.0, "1", "", "fixture", str(w), {})
        for e, w in zip(CLASSES, (589.2, 285.3, 422.8, 404.7), strict=True)
    ]
    catalog = ReferenceCatalog(
        lines, {"content_sha256": "b" * 64, "wavelength_medium": "vacuum", "units": "nm"}
    )
    if profile_version != 3:
        catalog = ReferenceCatalog.from_cache(
            repository_root() / "data/reference/nist_uv_visible_v2/catalog.json"
        )
    plan = feature_plan(
        catalog, "continuum-snr-ion" if profile_version != 3 else "v2-line-baseline"
    )
    model = LineAdapterV3()
    model.base = LineAdapterV2()
    model.base.C = 1.0
    model.base.states = {
        e: {
            "mean": [0.0] * feature_count(plan, e),
            "scale": [1.0] * feature_count(plan, e),
            "coef": [0.0] * feature_count(plan, e),
            "intercept": 0.0,
        }
        for e in CLASSES
    }
    model.calibration = {e: {"slope": 1.0, "intercept": 0.0} for e in CLASSES}
    path = directory / "fictional-profile.json"
    model.save(
        path,
        plan=plan,
        thresholds=[0.4] * 4,
        # Final binary scoring must explicitly ignore this application-only refusal.
        selective={e: {"minimum_snr": 5 if e in ("Fe", "Ca") else 0, "margin": 0} for e in CLASSES},
        provenance={
            "instrument_id": "fictional-cli-instrument",
            "scope": "nested_grouped_development_cv_not_independent_test",
            "decision_policy": DECISION_POLICY,
        },
    )
    profile = json.loads(path.read_text())
    if profile_version != 3:
        from spectralab_ml.models.calcium_adapter_v4 import (
            CalciumAdapterV4,
            CalciumClassifier,
            feature_dimension,
        )

        dimension = feature_dimension(plan, "blue-ca-agreement")
        calcium = CalciumClassifier.from_payload(
            {
                "state": {
                    "learner": "logistic",
                    "C": 0.1,
                    "mean": [0.0] * dimension,
                    "scale": [1.0] * dimension,
                    "coef": [0.0] * dimension,
                    "intercept": 0.0,
                },
                "calibration": {"slope": 1.0, "intercept": 0.0},
            },
            dimension,
        )
        path = directory / "fictional-calcium-profile.json"
        CalciumAdapterV4.save(
            path,
            original_profile=profile,
            calcium=calcium,
            family="blue-ca-agreement",
            threshold=0.4,
            provenance={
                "instrument_id": "fictional-cli-instrument",
                "scope": "fictional-test-only",
            },
        )
        profile = json.loads(path.read_text())
        if profile_version == "ensemble":
            from spectralab_ml.models.calcium_ensemble_v4 import CalciumEnsembleV4

            path = directory / "fictional-calcium-ensemble.json"
            CalciumEnsembleV4.save(
                path,
                original_profile=profile["original_v3_profile"],
                members=[calcium.payload()] * 4,
                family="blue-ca-agreement",
                threshold=0.4,
                provenance={
                    "instrument_id": "fictional-cli-instrument",
                    "scope": "fictional-test-only",
                },
            )
            profile = json.loads(path.read_text())
    return path, profile


def fictional_sample(root: Path, directory: Path, group: str, number: int) -> dict:
    name = f"fictional-{group}-{number}"
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
            "evidence_id": f"fictional-assay:{name}:{element}",
            "physical_sample_id": physical,
            **fictional_evidence(),
        }
        if group.startswith("blank"):
            label.update(upper_bound=0.05, certified=True, method="FICTIONAL test assay")
        else:
            label["value"] = 2 if group == "positive" else 0.05
        labels[element] = label
    path = directory / f"{name}.csv"
    # Distinct bytes prevent independently named fixtures becoming one checksum group.
    offset = ("positive", "negative", "blankFe", "blankMg").index(group) * 2 + number
    np.savetxt(
        path,
        np.column_stack((GRID, np.full(len(GRID), 1 + offset / 100))),
        delimiter=",",
        header="wavelength_nm,intensity",
        comments="",
        fmt="%.10f",
    )
    sha = checksum(path)
    return {
        "measurement_id": name,
        "physical_sample_id": physical,
        "family_id": f"family:{name}",
        "session_id": f"session:{name}",
        "instrument_id": "fictional-cli-instrument",
        "data_source_id": "fictional-cli-source",
        "input_sha256": sha,
        "raw_spectrum": {
            "uri": f"https://example.org/fictional/{name}.csv",
            "format": "csv",
            "numeric_spectrum_verified": True,
        },
        "local_spectrum": {
            "path": path.relative_to(root).as_posix(),
            "sha256": sha,
            "format": "csv",
            "columns": ["wavelength_nm", "intensity"],
            "rows": len(GRID),
        },
        "license": {"id": "CC0-1.0", "confirmed": True, **fictional_evidence()},
        "axis": {
            "units": "nm",
            "medium": "vacuum",
            "coverage_intervals_nm": BANDS,
            "max_sampling_step_nm": 0.1,
            "resolution_fwhm_nm": 0.1,
            **fictional_evidence(),
        },
        "control_type": "method_blank" if group.startswith("blank") else "sample",
        "labels": labels,
    }


def invoke(root: Path, *arguments: str) -> subprocess.CompletedProcess:
    # The editable package is the actual runtime, not a copied or patched callback.
    return subprocess.run(
        [sys.executable, "-m", "spectralab_ml.cli", *arguments],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=45,
        check=False,
    )


@pytest.mark.parametrize("profile_version", [3, 4, "ensemble"])
def test_fictional_full_cli_freeze_evaluate_consume_and_replay(profile_version):
    root = repository_root().resolve()
    cache = root / ".cache"
    cache.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="fictional-final-cli-", dir=cache) as temporary:
        directory = Path(temporary)
        profile_path, profile = write_profile(directory, profile_version)
        samples = [
            fictional_sample(root, directory, group, i)
            for group in ("positive", "negative", "blankFe", "blankMg")
            for i in range(2)
        ]
        measurement_metadata = directory / "measurement.json"
        measurement_metadata.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "measurement_id": samples[0]["measurement_id"],
                    "instrument_id": "fictional-cli-instrument",
                    "input_sha256": samples[0]["input_sha256"],
                    "axis": {"units": "nm", "medium": "vacuum", **fictional_evidence()},
                }
            )
        )
        application = invoke(
            root,
            "instrument-predict",
            "--profile",
            str(profile_path),
            "--input",
            str(root / samples[0]["local_spectrum"]["path"]),
            "--metadata",
            str(measurement_metadata),
        )
        assert application.returncode == 0, application.stderr
        applied = json.loads(application.stdout)
        assert applied["profile_identity"] == profile["identity"]
        assert applied["profile_sha256"] == checksum(profile_path)
        assert applied["profile_artifact_type"] == (
            "calcium_ensemble_v4"
            if profile_version == "ensemble"
            else "calcium_adapter_v4"
            if profile_version == 4
            else "line_adapter_v3"
        )
        assert applied["scope"] == "research_application_not_final_validation"
        by_element = {p["element"]: p for p in applied["predictions"]}
        assert by_element["Fe"]["detected"] is None
        assert by_element["H"]["score"] is None
        assert by_element["Ca"]["detected"] is (True if profile_version != 3 else None)
        required = [
            f"ml/src/spectralab_ml/{name}"
            for name in (
                "workflow_cli.py",
                "inference/measured.py",
                "data/measured_spectra.py",
                "models/line_adapter_v3.py",
                "models/line_adapter_v2.py",
                "evaluation/real_validation.py",
                "preprocessing/pipeline.py",
            )
        ]
        if profile_version != 3:
            required.append("ml/src/spectralab_ml/models/calcium_adapter_v4.py")
        if profile_version == "ensemble":
            required.append("ml/src/spectralab_ml/models/calcium_ensemble_v4.py")
        manifest = {
            **empty_final_manifest("fictional-cli-contract-test-only"),
            "selection_frozen_before_predictions": True,
            "data_source": {"id": "fictional-cli-source", **fictional_evidence()},
            "profile": {
                "identity": profile["identity"],
                "instrument_id": "fictional-cli-instrument",
                "class_order": CLASSES,
                "thresholds": dict.fromkeys(CLASSES, 0.4),
                "artifact_files": [
                    {
                        "path": profile_path.relative_to(root).as_posix(),
                        "sha256": checksum(profile_path),
                    }
                ],
                "units": "nm",
                "medium": "vacuum",
                "required_intervals_nm": BANDS,
                "max_sampling_step_nm": 0.5,
                "max_resolution_fwhm_nm": 0.2,
                "require_positive_signal": True,
            },
            "implementation_files": [
                {"path": name, "sha256": checksum(root / name)} for name in required
            ],
            "support_policy": {
                "ci_estimand": "cluster_any_error",
                "confidence_level": 0.95,
                # Tiny fictional integration support only, not a scientific recommendation.
                "max_wilson_half_width": 0.49,
                "min_support": {
                    e: {g: dict.fromkeys(DIMENSIONS, 2) for g in GROUPS} for e in ELEMENTS
                },
            },
            "samples": samples,
        }
        registry = {field: [] for field in IDENTITIES}
        registry["aliases"] = [
            {
                "field": "family_ids",
                "left": samples[0]["family_id"],
                "right": "fictional-unused-family-alias",
                **fictional_evidence(),
            }
        ]
        manifest_path, registry_path = directory / "manifest.json", directory / "seen.json"
        manifest_path.write_text(json.dumps(manifest))
        registry_path.write_text(json.dumps(registry))
        inputs = [
            manifest_path,
            registry_path,
            profile_path,
            *[root / sample["local_spectrum"]["path"] for sample in samples],
            *[root / name for name in required],
        ]
        original_hashes = {path: checksum(path) for path in inputs}
        frozen_path, receipts = directory / "freeze.json", directory / "receipts"
        frozen = invoke(
            root,
            "final-freeze",
            "--manifest",
            str(manifest_path),
            "--registry",
            str(registry_path),
            "--data-root",
            str(root),
            "--output",
            str(frozen_path),
        )
        assert frozen.returncode == 0, frozen.stderr
        announced = json.loads(frozen.stdout)
        assert announced["numerical_inputs_read"] is False and announced["final_metrics"] is None
        frozen_bytes = frozen_path.read_bytes()
        saved = json.loads(frozen_bytes)
        assert saved["seen_registry"] == registry
        assert saved["preflight"]["status"] == "metadata_ready"
        assert saved["runtime_identity"]["implementation_sha256"] == {
            name: original_hashes[root / name] for name in required
        }
        common = [
            "final-evaluate",
            "--freeze",
            str(frozen_path),
            "--receipt-dir",
            str(receipts),
            "--data-root",
            str(root),
        ]
        # Reject destructive output before consuming any fictional final identities.
        unsafe = invoke(root, *common, "--output", str(profile_path))
        assert unsafe.returncode == 2 and "overwrite" in unsafe.stderr
        assert not receipts.exists()
        report_path = directory / "result.json"
        first = invoke(root, *common, "--output", str(report_path))
        assert first.returncode == 0, first.stderr + first.stdout
        report = json.loads(first.stdout)
        assert json.loads(report_path.read_text()) == report
        assert report["status"] == "completed"
        assert report["freeze_sha256"] == announced["freeze_sha256"]
        assert report["profile_identity"] == profile["identity"]
        assert report["evaluation_kind"] == "first_locked_evaluation"
        assert report["assessment_mode"] == "binary_score_threshold_only"
        assert report["selective_policy_evaluated"] is False
        assert report["qualification"] == "not_automatically_qualified"
        assert len(report["predictions"]) == 8
        assert all(row["scores"] == dict.fromkeys(CLASSES, 0.5) for row in report["predictions"])
        for element in ELEMENTS:
            assert report["final_metrics"][element]["positive"]["physical_recall"] == 1
            for group in ("negative", "blank"):
                cell = report["final_metrics"][element][group]
                assert cell["physical_support"] == 2
                assert cell["physical_false_positive_rate"] == 1
        claims = {path: path.read_bytes() for path in receipts.glob("*.json")}
        assert len(list(receipts.glob("campaign-*.json"))) == 1
        assert len(claims) > 8
        blocked_path = directory / "blocked-repeat.json"
        repeated = invoke(root, *common, "--output", str(blocked_path))
        assert repeated.returncode == 2 and "already consumed" in repeated.stderr
        assert not blocked_path.exists()
        unsafe_receipt = invoke(
            root, *common, "--replay", "--output", str(receipts / "report-overwrite.json")
        )
        assert unsafe_receipt.returncode == 2 and "permanent receipts" in unsafe_receipt.stderr
        replayed = invoke(root, *common, "--replay", "--output", str(directory / "replay.json"))
        assert replayed.returncode == 0, replayed.stderr
        replay = json.loads(replayed.stdout)
        assert replay["evaluation_kind"] == "locked_replay"
        assert replay["final_metrics"] == report["final_metrics"]
        assert replay["predictions"] == report["predictions"]
        assert replay["freeze_sha256"] == report["freeze_sha256"]
        assert replay["receipt"]["consumed_at"] == report["receipt"]["consumed_at"]
        assert {path: path.read_bytes() for path in receipts.glob("*.json")} == claims
        assert {path: checksum(path) for path in inputs} == original_hashes
        assert frozen_path.read_bytes() == frozen_bytes
    assert not directory.exists()
