from __future__ import annotations

import json

import pytest
from spectralab_ml.cli import main
from spectralab_ml.workflow_cli import read_json


@pytest.mark.parametrize(
    "raw",
    [
        '{"value":NaN}',
        '{"value":1e999}',
        '{"nested":{"value":-1e999}}',
        '{"value":1,"value":2}',
        "[1,2]",
    ],
)
def test_workflow_json_rejects_nonfinite_duplicates_and_wrong_shape(tmp_path, raw):
    path = tmp_path / "invalid.json"
    path.write_text(raw)
    with pytest.raises(ValueError):
        read_json(path)


def test_assay_cli_retains_blocked_rows_and_returns_nonzero(tmp_path, capsys):
    path = tmp_path / "assays.json"
    rows = [
        {"element": "Fe", "reported_species": "FeOT", "units": "wt_percent", "value": 0.05},
        {"element": "Fe", "reported_species": "FeO", "units": "wt_percent", "value": 0.05},
    ]
    path.write_text(json.dumps({"schema_version": 1, "assays": rows}))
    with pytest.raises(SystemExit) as exc:
        main(["normalize-assays", "--input", str(path)])
    assert exc.value.code == 2
    result = json.loads(capsys.readouterr().out)
    assert result["normalized_count"] == 1
    assert result["blocked_count"] == 1
    assert result["rows"][0]["qualified_blank"] is False


def test_workflow_output_cannot_replace_assay_input(tmp_path, capsys):
    path = tmp_path / "assays.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "assays": [
                    {
                        "element": "Mg",
                        "reported_species": "MgO",
                        "units": "wt_percent",
                        "value": 0.02,
                    }
                ],
            }
        )
    )
    original = path.read_bytes()
    with pytest.raises(SystemExit) as exc:
        main(["normalize-assays", "--input", str(path), "--output", str(path)])
    assert exc.value.code == 2
    assert path.read_bytes() == original
    assert "overwrite" in capsys.readouterr().err


def test_missing_ml_installation_reports_readiness_failure_not_scientific_success(tmp_path, capsys):
    with pytest.raises(SystemExit) as exc:
        main(["ml-check", "--root", str(tmp_path)])
    assert exc.value.code == 2
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "blocked"
    assert result["final_metrics"] is None
    assert result["numerical_spectra_opened"] is False


def test_diagnostics_validates_effective_project_and_selected_reference(tmp_path):
    from spectralab_ml.config import repository_root
    from spectralab_ml.diagnostics import check_ml_environment

    configs = tmp_path / "ml/configs"
    (configs / "experiments").mkdir(parents=True)
    original = (repository_root() / "ml/configs/project.yaml").read_text()
    (configs / "project.yaml").write_text(original + "\nreference_cache: missing-catalog.json\n")
    result = check_ml_environment(tmp_path)
    project_check = next(c for c in result["checks"] if c["name"] == "project_config")
    assert project_check["status"] == "failed"
    assert "missing-catalog" in project_check["reason"]
    (configs / "project.yaml").write_text(original)
    (configs / "experiments/invalid.yaml").write_text(
        "schema_version: 1\nfactor: lorentzian_fwhm_nm\nvalues: [0.1]\n"
    )
    result = check_ml_environment(tmp_path)
    experiment_check = next(c for c in result["checks"] if c["name"].endswith("invalid.yaml"))
    assert experiment_check["status"] == "failed"
    assert "voigt" in experiment_check["reason"].lower()


def test_diagnostics_checks_new_evidence_and_rejects_conflicting_manifests(tmp_path):
    import hashlib

    from spectralab_ml.diagnostics import check_ml_environment

    docs = tmp_path / "docs"
    docs.mkdir()
    evidence = tmp_path / "evidence.txt"
    evidence.write_text("metadata only")
    item = {"path": evidence.name, "sha256": hashlib.sha256(evidence.read_bytes()).hexdigest()}
    for name in ("final_validation_search_v3_summary.json", "final_validation_v5_summary.json"):
        (docs / name).write_text(json.dumps({"evidence_snapshots": [item]}))
    newest = tmp_path / "v6-evidence.txt"
    newest.write_text("new independent metadata, no intensities")
    latest_item = {
        "path": newest.name,
        "sha256": hashlib.sha256(newest.read_bytes()).hexdigest(),
    }
    (docs / "final_validation_v6_summary.json").write_text(
        json.dumps({"evidence_snapshots": [item, latest_item]})
    )
    result = check_ml_environment(tmp_path, verify_sources=True)
    checks = [c for c in result["checks"] if c["name"] == evidence.name]
    assert len(checks) == 1 and checks[0]["status"] == "passed"
    assert next(c for c in result["checks"] if c["name"] == newest.name)["status"] == "passed"
    newest.write_text("corrupt newest-only metadata")
    result = check_ml_environment(tmp_path, verify_sources=True)
    assert next(c for c in result["checks"] if c["name"] == newest.name)["status"] == "failed"
    evidence.write_text("changed metadata")
    result = check_ml_environment(tmp_path, verify_sources=True)
    assert next(c for c in result["checks"] if c["name"] == evidence.name)["status"] == "failed"
    (docs / "final_validation_v5_summary.json").write_text(
        json.dumps({"evidence_snapshots": [{**item, "sha256": "0" * 64}]})
    )
    result = check_ml_environment(tmp_path, verify_sources=True)
    assert any("disagree" in c.get("reason", "") for c in result["checks"])
    assert result["numerical_spectra_opened"] is False


def test_final_template_stays_blocked_and_cannot_replace_an_existing_plan(tmp_path, capsys):
    from spectralab_ml.config import repository_root

    path = tmp_path / "final.json"
    main(["final-template", "--output", str(path)])
    original = path.read_bytes()
    capsys.readouterr()
    with pytest.raises(SystemExit) as exc:
        main(
            [
                "final-preflight",
                "--manifest",
                str(path),
                "--registry",
                str(repository_root() / "data/reference/final_search_v3_admission.json"),
            ]
        )
    assert exc.value.code == 2
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "blocked"
    assert result["final_metrics"] is None
    assert result["numerical_files_read"] == 0
    with pytest.raises(SystemExit):
        main(["final-template", "--output", str(path)])
    assert path.read_bytes() == original


def test_invalid_final_freeze_is_checked_before_profile_binder(tmp_path, monkeypatch, capsys):
    path = tmp_path / "invalid-freeze.json"
    path.write_text(json.dumps({"manifest": {}, "freeze_sha256": "0" * 64}))

    def forbidden(*args, **kwargs):
        raise AssertionError("Invalid freeze must not reach any artifact loader")

    monkeypatch.setattr("spectralab_ml.inference.measured.bind_final_line_profile", forbidden)
    with pytest.raises(SystemExit) as exc:
        main(["final-evaluate", "--freeze", str(path), "--receipt-dir", str(tmp_path / "receipts")])
    assert exc.value.code == 2
    assert not (tmp_path / "receipts").exists()
    assert "freeze" in capsys.readouterr().err.lower()


def test_final_report_cannot_overwrite_permanent_receipts_before_reading_freeze(tmp_path, capsys):
    receipts = tmp_path / "receipts"
    with pytest.raises(SystemExit) as exc:
        main(
            [
                "final-evaluate",
                "--freeze",
                str(tmp_path / "not-read.json"),
                "--receipt-dir",
                str(receipts),
                "--output",
                str(receipts / "campaign.json"),
            ]
        )
    assert exc.value.code == 2
    assert "receipts" in capsys.readouterr().err
    assert not receipts.exists()


@pytest.mark.parametrize("descriptor", ["spectrum", "raw", "artifact", "implementation"])
def test_final_preflight_protects_all_declared_inputs_before_admission(
    tmp_path, capsys, descriptor
):
    selected = tmp_path / "selected.dat"
    selected.write_text("unopened fixture")
    plan = {"samples": [], "profile": {}, "implementation_files": []}
    if descriptor == "spectrum":
        plan["samples"] = [{"local_spectrum": {"path": selected.name}}]
    elif descriptor == "raw":
        plan["samples"] = [{"raw_spectrum": {"local_path": selected.name}}]
    elif descriptor == "artifact":
        plan["profile"] = {"artifact_files": [{"path": selected.name}]}
    else:
        plan["implementation_files"] = [{"path": selected.name}]
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps(plan))
    with pytest.raises(SystemExit) as exc:
        main(
            [
                "final-preflight",
                "--manifest",
                str(manifest),
                "--registry",
                str(tmp_path / "not-read.json"),
                "--data-root",
                str(tmp_path),
                "--output",
                str(selected),
            ]
        )
    assert exc.value.code == 2
    assert "overwrite" in capsys.readouterr().err
    assert selected.read_text() == "unopened fixture"


def test_final_line_point_limit_is_checked_before_binding_or_consumption(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.setattr(
        "spectralab_ml.evaluation.final_campaign_v3.load_final_freeze",
        lambda *a, **k: {"manifest": {"samples": [{"local_spectrum": {"rows": 100001}}]}},
    )
    receipts = tmp_path / "receipts"
    with pytest.raises(SystemExit) as exc:
        main(
            [
                "final-evaluate",
                "--freeze",
                str(tmp_path / "fixture.json"),
                "--receipt-dir",
                str(receipts),
            ]
        )
    assert exc.value.code == 2
    assert "100000" in capsys.readouterr().err
    assert not receipts.exists()
