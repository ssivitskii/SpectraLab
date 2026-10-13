#!/usr/bin/env python3
"""Fetch four metadata-selected low-Mg materials for development, never final test.

The v1 test has been disclosed and is development-only from now on. This intake
does not parse intensities, replace failed samples, or evaluate any model.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from datetime import UTC, datetime
from pathlib import Path

from prepare_instrument_data import (
    BASE,
    ROOT,
    Links,
    atomic_json,
    download,
    normalize,
    operational_labels,
    sha256,
    support,
)

INVENTORY = Path("data/reference/chemcam_adaptation_v2_intake.json")
RAW = Path("data/raw/chemcam_adaptation_v2")
AUDIT = Path("data/reference/chemcam_adaptation_alias_audit.json")
COMPOSITIONS = Path("data/raw/chemcam_external/ccam_calibration_compositions.csv")
V1_PROTOCOL = Path("data/reference/chemcam_adaptation_protocol.json")
OLD_PROTOCOL = Path("data/reference/chemcam_external_protocol.json")
SELECTION = (
    ("calib_2015", "macusanite", "data/raw/chemcam_v2_candidates/macusanite.html"),
    ("calib_2021", "27-07", "data/raw/chemcam_adaptation/metadata/calib_2021_27-07.html"),
    ("calib_2021", "jch-1", "data/raw/chemcam_adaptation/metadata/calib_2021_jch-1.html"),
    ("calib_2021", "jr-3", "data/raw/chemcam_v2_candidates/jr-3.html"),
)


def aliases(row: dict) -> set[str]:
    return {
        normalize(row[key])
        for key in ("Target", "Spectrum Name", "Sample Name")
        if row[key].strip()
    }


def seen_alias_closure(rows: list[dict], v1: dict, old: dict) -> set[str]:
    seen = {normalize(x) for x in old["test_sample_ids"]}
    seen.update(a for r in v1["development"] + v1["test"] for a in aliases(r["composition_row"]))
    while True:
        expanded = seen | {a for row in rows if aliases(row) & seen for a in aliases(row)}
        if expanded == seen:
            return seen
        seen = expanded


def freeze(project: Path) -> dict:
    audit = json.loads((project / AUDIT).read_text())
    v1 = json.loads((project / V1_PROTOCOL).read_text())
    old = json.loads((project / OLD_PROTOCOL).read_text())
    with (project / COMPOSITIONS).open() as stream:
        rows = [r for r in csv.DictReader(stream) if r["Target"]]
    seen = seen_alias_closure(rows, v1, old)
    sessions = {r["session_id"] for r in v1["development"] + v1["test"]}
    records, snapshots = [], []
    for campaign, folder, listing in SELECTION:
        entry = audit[campaign][folder]
        if entry["status"] != "matched" or len(entry["matches"]) != 1:
            raise ValueError(f"Ambiguous composition for {folder}")
        match = entry["matches"][0]
        row = match["composition_row"]
        if aliases(row) & seen:
            raise ValueError(f"Previously exposed material alias: {folder}")
        if not 0 < float(row["MgO"]) <= 0.1:
            raise ValueError(f"Not a published low-Mg candidate: {folder}")
        directory = BASE + f"{campaign}/1600mm/{folder}/"
        parser = Links()
        parser.feed((project / listing).read_text())
        filenames = sorted(
            {
                href.rsplit("/", 1)[-1]
                for href in parser.hrefs
                if re.fullmatch(r"\d{4}_\d{2}_\d{2}_\d{6}_ccs\.csv", href.rsplit("/", 1)[-1])
            }
        )
        if not filenames:
            raise ValueError(f"No numeric CCS product in listing: {folder}")
        filename = filenames[0]
        day = filename[:10].replace("_", "-")
        relative = RAW / "development" / folder / filename
        sample = match["canonical_physical_id"]
        records.append(
            {
                "role": "development",
                "folder": folder,
                "campaign": campaign,
                "measurement_id": f"development-v2:{folder}:{filename.removesuffix('.csv')}",
                "physical_sample_id": sample,
                "family_id": sample,
                "session_id": f"chemcam-testbed-{day}",
                "instrument_id": "chemcam-lanl-testbed",
                "filename": filename,
                "local_relative_path": str(relative),
                "url": directory + filename,
                "label_url": directory + filename.removesuffix(".csv") + ".lbl",
                "label_local_relative_path": str(relative.with_suffix(".lbl")),
                "composition_row": row,
                "composition_row_number": match["row_number"],
                "operational_labels": operational_labels(row),
                "published_matching_fields": match["published_matching_fields"],
                "material_alias_seen": False,
                "acquisition_session_seen": f"chemcam-testbed-{day}" in sessions,
                "status": "pending_download",
                "input_sha256": None,
                "label_sha256": None,
                "warnings": (
                    [
                        "Gasda2021 section3.1 removed 27-07 as an original-paper spectral outlier; "
                        "retained as metadata-selected development with published assay labels."
                    ]
                    if folder == "27-07"
                    else []
                ),
            }
        )
        snapshots.append({"path": listing, "sha256": sha256(project / listing), "url": directory})
    return {
        "schema_version": 1,
        "protocol_id": "chemcam-development-extension-v2-four-low-mg",
        "selection_locked_at": datetime.now(UTC).isoformat(),
        "phase": "metadata_frozen",
        "no_predictions_read_or_generated": True,
        "final_test_records": 0,
        "selection_rule": "All four remaining base materials with published 0<MgO<=0.1 wt% "
        "and no previously disclosed material alias; first lexicographic CCS point per material. "
        "Li420_NCS_JR3 excluded as a derived JR3 mixture. Development only.",
        "pinned_inputs": [
            {"path": str(p), "sha256": sha256(project / p)}
            for p in (AUDIT, COMPOSITIONS, V1_PROTOCOL, OLD_PROTOCOL)
        ],
        "provenance": v1["source"],
        "directory_snapshots": snapshots,
        "records": records,
        "exclusions": [
            {"folder": "li420ncsjr3", "reason": "derived_jr3_family_duplicate", "family_id": "jr3"}
        ],
        "selection_support": support(records),
        "seen_material_aliases": sorted(seen),
        "limitations": [
            "No new low-Fe standard in the published table after seen-alias closure.",
            "These are low-content standards, not documented method or matrix blanks.",
            "Published rounded zero, missing and intermediate oxide values remain unknown.",
            "Material-alias independence does not imply independence of acquisition sessions.",
            "Mixture family provenance beyond identified JR3 parents is not inferred from spectra.",
            "All v1 development and v1 test measurements are now disclosed development data.",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--download-development", action="store_true")
    args = parser.parse_args()
    path = ROOT / INVENTORY
    value = json.loads(path.read_text()) if path.exists() else freeze(ROOT)
    for item in value["pinned_inputs"] + value["directory_snapshots"]:
        if sha256(ROOT / item["path"]) != item["sha256"]:
            raise ValueError(f"Pinned source changed: {item['path']}")
    atomic_json(path, value)
    if args.download_development:
        for record in value["records"]:
            try:
                data = download(
                    record["url"],
                    ROOT / record["local_relative_path"],
                    expected_sha256=record["input_sha256"],
                )
                label = download(
                    record["label_url"],
                    ROOT / record["label_local_relative_path"],
                    expected_sha256=record["label_sha256"],
                )
                record.update(
                    status="downloaded",
                    input_sha256=data["sha256"],
                    label_sha256=label["sha256"],
                    input_bytes=data["bytes"],
                    label_bytes=label["bytes"],
                )
                record.pop("error", None)
            except (OSError, ValueError) as exc:
                record.update(status="download_failed", error=str(exc))
            atomic_json(path, value)
            print(record["folder"], record["status"], flush=True)
        value["phase"] = (
            "download_complete"
            if all(r["status"] == "downloaded" for r in value["records"])
            else "incomplete_downloads"
        )
        value["total_downloaded_bytes"] = sum(
            r.get("input_bytes", 0) + r.get("label_bytes", 0) for r in value["records"]
        )
        value.setdefault("completed_at", datetime.now(UTC).isoformat())
        atomic_json(path, value)


if __name__ == "__main__":
    main()
