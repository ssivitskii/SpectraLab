import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/prepare_instrument_data.py"
SPEC = importlib.util.spec_from_file_location("prepare_instrument_data", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
intake = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(intake)


def composition(value="2"):
    return {**dict.fromkeys(intake.OXIDES, value), "Used for 2015 calibration": "1"}


def match(name, row):
    return {"canonical_physical_id": name, "row_number": 2, "composition_row": row}


def test_operational_guard_band_never_turns_zero_or_missing_into_negative():
    row = {"Na2O": "0", "MgO": "", "CaO": "0.1", "FeOT": "1"}
    assert intake.operational_labels(row) == {
        "Na2O": None,
        "MgO": None,
        "CaO": 0,
        "FeOT": 1,
    }
    assert intake.operational_labels(composition("0.5")) == dict.fromkeys(intake.OXIDES)


def test_first_acquisition_is_not_replaced_by_later_eligible_acquisition():
    url = intake.BASE + "calib_2021/1600mm/a/"
    listing = (
        '<a href="2017_01_01_123456_ccs.csv">new</a><a href="2015_01_01_123456_ccs.csv">old</a>'
    )
    with pytest.raises(ValueError, match="precedes"):
        intake.first_measurement(listing, url)
    with pytest.raises(ValueError, match="leaves"):
        intake.first_measurement(
            '<a href="https://example.com/2017_01_01_123456_ccs.csv">x</a>',
            url,
        )
    filename, actual, day = intake.first_measurement(
        '<a href="2017_01_01_123456_ccs.csv">x</a>',
        url,
    )
    assert filename == "2017_01_01_123456_ccs.csv"
    assert actual == url + filename and day == "2017-01-01"


def test_development_intake_is_order_independent_and_preserves_every_low_standard():
    entries, lut = {}, []
    for i in range(140):
        name = f"standard{i:03d}"
        row = composition("0.02" if i < 4 else "2")
        entries[name] = {"status": "matched", "matches": [match(name, row)]}
        lut.append([name, "1", "2015_01_01_123456.CSV"])
    entries["uncertain"] = {"status": "ambiguous", "matches": []}
    entries["duplicatepoint"] = {
        "status": "matched",
        "matches": [match("duplicatepoint", composition("0.02"))],
    }
    lut.extend(
        [
            ["duplicatepoint", "1", "2015_01_01_123456.CSV"],
            ["duplicatepoint", "1", "2015_01_01_123457.CSV"],
        ]
    )
    records, excluded = intake.development_selection({"calib_2015": entries}, lut)
    reverse, _ = intake.development_selection(
        {"calib_2015": dict(reversed(list(entries.items())))},
        list(reversed(lut)),
    )
    assert records == reverse and len(records) == 128
    selected = {r["folder"] for r in records}
    assert {f"standard{i:03d}" for i in range(4)} <= selected
    assert "duplicatepoint" not in selected and "uncertain" not in selected
    assert len(excluded) == 14


def test_cached_source_with_changed_bytes_fails_without_network(tmp_path, monkeypatch):
    path = tmp_path / "source.csv"
    path.write_bytes(b"original")
    expected = intake.sha256(path)
    path.write_bytes(b"tampered")
    monkeypatch.setattr(intake.subprocess, "run", lambda *_a, **_kw: pytest.fail("network"))
    with pytest.raises(ValueError, match="Pinned file changed"):
        intake.download("https://example.com/source.csv", path, expected_sha256=expected)


def test_download_requires_tls_and_bounded_size_then_checks_actual_bytes(tmp_path, monkeypatch):
    def oversized_download(arguments, **_kwargs):
        assert arguments[arguments.index("--max-filesize") + 1] == "8"
        assert arguments[arguments.index("--proto") + 1] == "=https"
        assert arguments[arguments.index("--proto-redir") + 1] == "=https"
        assert "--insecure" not in arguments and "-k" not in arguments
        Path(arguments[arguments.index("--output") + 1]).write_bytes(b"0123456789")
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr(intake, "MAX_BYTES", 8)
    monkeypatch.setattr(intake.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(intake.subprocess, "run", oversized_download)
    destination = tmp_path / "source.csv"
    with pytest.raises(ValueError, match="Source size"):
        intake.download("https://example.com/source.csv", destination)
    assert not destination.exists()
    assert not destination.with_suffix(".csv.part").exists()


def test_amendment_cannot_repin_modified_intake_or_skip_existing_measurement(tmp_path):
    audit = tmp_path / intake.AUDIT
    audit.parent.mkdir(parents=True)
    audit.write_text("{}")
    inventory_path = tmp_path / intake.INVENTORY
    url = intake.BASE + "calib_2015/1600mm/missing/2013_09_12_190104_ccs.csv"
    inventory = {
        "records": [
            {
                "measurement_id": "missing",
                "status": "download_failed",
                "url": url,
                "filename": "2013_09_12_190104_ccs.csv",
            }
        ]
    }
    intake.atomic_json(inventory_path, inventory)
    evidence = tmp_path / "directory.html"
    evidence.write_text('<a href="2013_09_12_190458_ccs.csv">second point</a>')
    amendment = {
        "schema_version": 1,
        "locked_before_predictions": True,
        "intake_sha256": intake.sha256(inventory_path),
        "alias_audit_sha256": intake.sha256(audit),
        "exclusions": [
            {
                "measurement_id": "missing",
                "evidence_path": "directory.html",
                "evidence_sha256": intake.sha256(evidence),
                "evidence_url": url.rsplit("/", 1)[0] + "/",
            }
        ],
    }
    intake.atomic_json(tmp_path / intake.AMENDMENTS, amendment)
    assert intake.source_amendments(tmp_path, inventory, inventory_path) == {"missing"}
    assert intake.verify_completed_inventory(tmp_path, inventory, {"missing"})
    inventory_path.write_text(json.dumps(inventory) + " ")
    with pytest.raises(ValueError, match="intake hash changed"):
        intake.source_amendments(tmp_path, inventory, inventory_path)
    intake.atomic_json(inventory_path, inventory)
    evidence.write_text('<a href="2013_09_12_190104_ccs.csv">selected point</a>')
    amendment["exclusions"][0]["evidence_sha256"] = intake.sha256(evidence)
    intake.atomic_json(tmp_path / intake.AMENDMENTS, amendment)
    with pytest.raises(ValueError, match="present in the amendment"):
        intake.source_amendments(tmp_path, inventory, inventory_path)


def test_clean_checkout_restores_only_missing_official_metadata_with_original_pin(
    tmp_path,
    monkeypatch,
):
    audit = tmp_path / intake.AUDIT
    audit.parent.mkdir(parents=True)
    audit.write_text("audit")
    content = b"unchanged official LUT"
    expected = intake.hashlib.sha256(content).hexdigest()
    calls = []

    def restore(url, destination, *, expected_sha256):
        calls.append((url, expected_sha256))
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)

    monkeypatch.setattr(intake, "download", restore)
    inventory = {
        "pinned_inputs": {str(intake.AUDIT): intake.sha256(audit), str(intake.LUT): expected}
    }
    intake.verify_pinned_inputs(tmp_path, inventory)
    assert calls == [(intake.BASE + "std_idx_2_file_lut.csv", expected)]
    (tmp_path / intake.LUT).write_bytes(b"changed")
    with pytest.raises(ValueError, match="Frozen metadata changed"):
        intake.verify_pinned_inputs(tmp_path, inventory)
    assert len(calls) == 1
