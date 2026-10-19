"""Metadata-only exclusion compilation; every specimen here is fictional."""

import copy
import importlib.util
import json
from pathlib import Path

import pytest
from spectralab_ml.evaluation.final_campaign_v3 import _Aliases, _Checks

_PATH = Path(__file__).resolve().parents[2] / "scripts/prepare_final_seen_registry_v6.py"
_SPEC = importlib.util.spec_from_file_location("registry_v6", _PATH)
compiler = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(compiler)


@pytest.fixture
def metadata():
    chemistry = [
        {"Target": "TARGET-A", "Spectrum Name": "A-1", "Sample Name": "Shared name"},
        {"Target": "TARGET-B", "Spectrum Name": "B-1", "Sample Name": "Shared name"},
        {"Target": "TARGET-OLD", "Spectrum Name": "Old-1", "Sample Name": "Old material"},
    ]
    source_metadata = {
        key: {"path": value, "sha256": "a" * 64, "bytes": 1}
        for key, value in compiler.SOURCES.items()
    }
    base = {key: ["historical-" + key] for key in (*compiler.FIELDS, "assay_ids")}
    record = {
        "measurement_id": "measurement-a",
        "physical_sample_id": "physical-a",
        "family_id": "family-a",
        "session_id": "session-a",
        "input_sha256": "b" * 64,
        "folder": "A-1",
        "composition_row_number": 2,
        "composition_row": chemistry[0],
    }
    files = {
        name: {"sha256": str(i) * 64, "bytes": 1}
        for i, name in enumerate(
            (
                "aalto_Cu_spectrum.csv",
                "aalto_Cu_plot.html",
                "labtrace_avantes_7mJ.h5",
                "firefly_avantes_multi_20mJ.h5",
            ),
            1,
        )
    }
    documents = {
        "historical": {"previously_seen_ids": base},
        "development": {"records": [record]},
        "alias_audit": {
            "source_url": "https://example.invalid/fictional-chemistry",
            "source_compositions": {"sha256": "a" * 64},
        },
        "preflight": {
            "test_sample_ids": ["Old-1"],
            "acquisition_session_id": "old-session",
            "input_sha256": {"msl_ccam_libs_calib.csv": "c" * 64},
        },
        "previous_candidate_audit": {"files": files},
    }
    return documents, chemistry, source_metadata


def test_preserves_history_and_source_qualified_quarantine(metadata):
    documents, chemistry, sources = metadata
    original = copy.deepcopy(documents)
    registry = compiler.compile_registry(documents, chemistry, sources)
    assert documents == original
    for field, previous in documents["historical"]["previously_seen_ids"].items():
        assert set(previous) <= set(registry[field])
    assert f"ccam_composition_csv:{'a' * 64}:row:4" in registry["assay_ids"]
    assert "1" * 64 in registry["input_sha256s"]
    assert "3" * 64 in registry["input_sha256s"]
    assert len(registry["quarantined_artifacts"]) == 2
    assert not any(v.startswith("figshare:") for v in registry["physical_sample_ids"])
    assert registry["complete_independence_review"] is False
    aliases = {e[side] for e in registry["aliases"] for side in ("left", "right")}
    assert "aalto-lasolibs:Cu" in aliases
    assert "Cu" not in aliases and "copper" not in aliases
    checks = _Checks()
    closure = _Aliases(registry, checks)
    assert not checks.blockers
    assert closure.value("physical_sample_ids", "TARGET-A") == closure.value(
        "physical_sample_ids", "chemcam-calibration:a1"
    )


def test_ambiguous_published_name_never_merges_unreviewed_materials(metadata):
    documents, chemistry, sources = metadata
    registry = compiler.compile_registry(documents, chemistry, sources)
    assert "Shared name" in registry["ambiguous_published_aliases_not_merged"]
    assert all(
        "sharedname" not in e[side].lower().replace(" ", "")
        for e in registry["aliases"]
        for side in ("left", "right")
    )
    record = copy.deepcopy(documents["development"]["records"][0])
    record.update(
        measurement_id="measurement-b",
        composition_row_number=3,
        composition_row=chemistry[1],
        folder="B-1",
    )
    # Both rows may merge only after the source plan explicitly attests the same physical ID.
    documents["development"]["records"].append(record)
    registry = compiler.compile_registry(documents, chemistry, sources)
    assert "Shared name" not in registry["ambiguous_published_aliases_not_merged"]
    assert any("Shared name" in (e["left"], e["right"]) for e in registry["aliases"])


def test_changed_chemistry_or_ambiguous_preflight_mapping_fails(metadata):
    documents, chemistry, sources = metadata
    altered = copy.deepcopy(sources)
    altered["chemistry"]["sha256"] = "d" * 64
    with pytest.raises(ValueError, match="Pinned chemistry"):
        compiler.compile_registry(documents, chemistry, altered)
    documents = copy.deepcopy(documents)
    documents["development"]["records"][0]["composition_row"]["Target"] = "SUBSTITUTED"
    with pytest.raises(ValueError, match="composition row changed"):
        compiler.compile_registry(documents, chemistry, sources)
    documents, chemistry, sources = metadata
    with pytest.raises(ValueError, match="no longer unique"):
        compiler.compile_registry(documents, [*chemistry, chemistry[2]], sources)


def test_saved_registry_covers_actual_prior_202_and_64_plus_aalto():
    """Read only committed metadata snapshots, never any spectral payload."""
    root = _PATH.parents[1]
    registry = json.loads((root / "data/reference/final_seen_registry_v6.json").read_bytes())
    previous = json.loads((root / compiler.SOURCES["historical"]).read_bytes())
    development = json.loads((root / compiler.SOURCES["development"]).read_bytes())["records"]
    preflight = json.loads((root / compiler.SOURCES["preflight"]).read_bytes())
    assert len(development) == 202 and len(preflight["test_sample_ids"]) == 64
    for field, key in compiler.FIELDS.items():
        assert {r[key] for r in development} <= set(registry[field])
        assert set(previous["previously_seen_ids"][field]) <= set(registry[field])
    assert set(map(compiler.normalized, preflight["test_sample_ids"])) <= set(
        map(compiler.normalized, registry["physical_sample_ids"])
    )
    assert (
        "6d1007726b733b589563cb95bbede4c4dce1ef72d02c0d21e58899d75c646e42"
        in registry["input_sha256s"]
    )
    checks = _Checks()
    closure = _Aliases(registry, checks)
    assert not checks.blockers
    assert closure.value("physical_sample_ids", "aalto-lasolibs:Cu") == closure.value(
        "physical_sample_ids", "https://users.aalto.fi/~lainei1/assets/elements/plotly/Cu.html"
    )
    assert registry["complete_independence_review"] is False


def test_saved_registry_rebuild_is_deterministic_from_available_historical_metadata():
    root = _PATH.parents[1]
    missing = [p for p in compiler.SOURCES.values() if not (root / p).is_file()]
    if missing:
        pytest.skip(f"Historical metadata snapshots not restored: {missing}")
    saved = json.loads((root / "data/reference/final_seen_registry_v6.json").read_bytes())
    assert compiler.build_registry(root) == saved
    assert compiler.build_registry(root) == saved
