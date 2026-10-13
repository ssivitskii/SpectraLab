#!/usr/bin/env python3
"""Freeze metadata-only ChemCam adaptation intake, then fetch pinned measurements.

No spectrum is parsed or scored here. A failed transfer never selects a replacement.
Reruns resume the existing locked inventory and verify downloaded file hashes.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import subprocess
import time
import urllib.parse
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, date, datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
BASE = "https://pds-geosciences.wustl.edu/msl/msl-m-chemcam-libs-4_5-rdr-v1/mslccm_1xxx/calib/"
AUDIT = Path("data/reference/chemcam_adaptation_alias_audit.json")
LUT = Path("data/raw/adaptation_candidates_root/std_idx_2_file_lut.csv")
INVENTORY = Path("data/reference/chemcam_adaptation_intake.json")
AMENDMENTS = Path("data/reference/chemcam_adaptation_source_amendments.json")
RAW = Path("data/raw/chemcam_adaptation")
OXIDES = ("Na2O", "MgO", "CaO", "FeOT")
MAX_BYTES = 16 * 1024 * 1024
PROTOCOL = "chemcam-instrument-adaptation-intake-v1"
DATASET_ID = "MSL-M-CHEMCAM-LIBS-4/5-RDR-V1.0"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def normalize(value: str) -> str:
    return re.sub("[^a-z0-9]", "", value.lower())


def operational_labels(row: dict[str, str]) -> dict[str, int | None]:
    """Reported oxide threshold labels; zero/missing/intermediate remain unknown."""
    result: dict[str, int | None] = {}
    for oxide in OXIDES:
        text = row[oxide].strip()
        value = float(text) if text else None
        result[oxide] = (
            0
            if value is not None and 0 < value <= 0.1
            else 1
            if value is not None and value >= 1
            else None
        )
    return result


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n")
    temporary.replace(path)


def download(url: str, destination: Path, *, expected_sha256: str | None = None) -> dict:
    """Bounded HTTPS download with two retries, atomic files, and SHA256 verification."""
    if not url.startswith("https://"):
        raise ValueError("Only HTTPS sources are permitted")
    if destination.exists():
        digest = sha256(destination)
        if expected_sha256 is not None and digest != expected_sha256:
            raise ValueError(f"Pinned file changed: {destination}")
        if destination.stat().st_size > MAX_BYTES:
            raise ValueError(f"Cached file exceeds size limit: {destination}")
        return {"sha256": digest, "bytes": destination.stat().st_size, "cached": True}
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".part")
    for attempt in range(3):
        try:
            # System curl uses the host's trusted certificate store; TLS verification stays on.
            result = subprocess.run(
                [
                    "curl",
                    "--fail",
                    "--location",
                    "--silent",
                    "--show-error",
                    "--proto",
                    "=https",
                    "--proto-redir",
                    "=https",
                    "--max-time",
                    "120",
                    "--max-filesize",
                    str(MAX_BYTES),
                    "--output",
                    str(temporary),
                    url,
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=130,
            )
            if result.returncode:
                raise OSError(f"curl exit {result.returncode}: {result.stderr.strip()[:1000]}")
            count = temporary.stat().st_size
            if not 0 < count <= MAX_BYTES:
                raise ValueError(f"Source size {count} is outside1..{MAX_BYTES} bytes")
            digest = sha256(temporary)
            if expected_sha256 is not None and digest != expected_sha256:
                raise ValueError("Downloaded source differs from pinned SHA256")
            temporary.replace(destination)
            return {"sha256": digest, "bytes": count, "cached": False}
        except (OSError, ValueError, subprocess.SubprocessError):
            temporary.unlink(missing_ok=True)
            if attempt == 2:
                raise
            time.sleep(attempt + 1)
    raise RuntimeError("Unreachable download state")


class Links(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.hrefs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() == "a":
            self.hrefs.extend(value for key, value in attrs if key == "href" and value)


def first_measurement(listing: str, directory_url: str) -> tuple[str, str, str]:
    parser = Links()
    parser.feed(listing)
    candidates: dict[str, set[str]] = defaultdict(set)
    for href in parser.hrefs:
        url = urllib.parse.urljoin(directory_url, href)
        filename = urllib.parse.unquote(urllib.parse.urlparse(url).path.rsplit("/", 1)[-1])
        if not re.fullmatch(r"\d{4}_\d{2}_\d{2}_\d{6}_ccs\.csv", filename.lower()):
            continue
        if url.rsplit("/", 1)[0] + "/" != directory_url:
            raise ValueError("Spectrum link leaves the expected source directory")
        candidates[filename.lower()].add(url)
    if not candidates:
        raise ValueError("No CCS CSV measurement in source listing")
    filename = min(candidates)
    if len(candidates[filename]) != 1:
        raise ValueError("Ambiguous first measurement URL")
    measurement_date = date.fromisoformat(filename[:10].replace("_", "-"))
    if measurement_date < date(2016, 1, 1):
        raise ValueError("First measurement precedes 2016-01-01")
    return filename, next(iter(candidates[filename])), measurement_date.isoformat()


def record(role: str, folder: str, match: dict, filename: str, url: str) -> dict:
    stem = filename.removesuffix(".csv")
    day = date.fromisoformat(filename[:10].replace("_", "-")).isoformat()
    sample = match["canonical_physical_id"]
    relative = RAW / role / folder / filename
    return {
        "role": role,
        "folder": folder,
        "measurement_id": f"{role}:{folder}:{stem}",
        "physical_sample_id": sample,
        "family_id": "wmb24" if sample in {"wmb24a", "wmb24b"} else sample,
        "session_id": f"chemcam-testbed-{day}",
        "instrument_id": "chemcam-lanl-testbed",
        "filename": filename,
        "local_relative_path": str(relative),
        "url": url,
        "label_url": url.removesuffix(".csv") + ".lbl",
        "label_local_relative_path": str(relative.with_suffix(".lbl")),
        "composition_row": match["composition_row"],
        "composition_row_number": match["row_number"],
        "operational_labels": operational_labels(match["composition_row"]),
        "status": "pending_download",
        "input_sha256": None,
        "label_sha256": None,
    }


def development_selection(audit: dict, lut_rows: list[list[str]]) -> tuple[list[dict], list[dict]]:
    lut: dict[str, set[str]] = defaultdict(set)
    for name, index, filename in lut_rows:
        if index.strip() == "1":
            lut[normalize(name)].add(filename.lower().removesuffix(".csv") + "_ccs.csv")
    candidates, exclusions = [], []
    for folder, entry in sorted(audit["calib_2015"].items()):
        reason = None
        if entry["status"] != "matched":
            reason = entry["status"] + "_composition_alias"
        elif entry["matches"][0]["composition_row"]["Used for 2015 calibration"] != "1":
            reason = "not_used_for_2015_calibration"
        elif len(lut[normalize(folder)]) != 1:
            reason = "lut_first_spatial_point_missing_or_ambiguous"
        if reason:
            exclusions.append({"role": "development", "folder": folder, "reasons": [reason]})
            continue
        filename = next(iter(lut[normalize(folder)]))
        url = BASE + f"calib_2015/1600mm/{folder}/{filename}"
        candidates.append(record("development", folder, entry["matches"][0], filename, url))
    low = [r for r in candidates if 0 in r["operational_labels"].values()]
    others = sorted(
        [r for r in candidates if 0 not in r["operational_labels"].values()],
        key=lambda r: hashlib.sha256(("20261013:" + r["folder"]).encode()).hexdigest(),
    )
    if len(low) > 128:
        raise ValueError("All-low-label inclusion exceeds the preregistered 128 development cap")
    chosen = low + others[: 128 - len(low)]
    chosen_ids = {r["measurement_id"] for r in chosen}
    exclusions.extend(
        {"role": "development", "folder": r["folder"], "reasons": ["hash_sample_cap_128"]}
        for r in candidates
        if r["measurement_id"] not in chosen_ids
    )
    return sorted(chosen, key=lambda r: r["folder"]), exclusions


def test_metadata(project_root: Path, folder: str, entry: dict) -> tuple[dict | None, dict]:
    url = BASE + f"calib_2021/1600mm/{folder}/"
    path = RAW / "metadata" / f"calib_2021_{folder}.html"
    info: dict[str, Any] = {"role": "test", "folder": folder, "url": url, "path": str(path)}
    try:
        info.update(download(url, project_root / path))
        filename, spectrum_url, _ = first_measurement((project_root / path).read_text(), url)
        return record("test", folder, entry["matches"][0], filename, spectrum_url), info
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        info["error"] = f"{type(exc).__name__}: {exc}"
        return None, info


def support(records: list[dict]) -> dict:
    result = {}
    for role in ("development", "test"):
        selected = [r for r in records if r["role"] == role]
        result[role] = {
            "measurements": len(selected),
            "physical_samples": len({r["physical_sample_id"] for r in selected}),
            "families": len({r["family_id"] for r in selected}),
            "session_count": len({r["session_id"] for r in selected}),
            "session_min": min((r["session_id"] for r in selected), default=None),
            "session_max": max((r["session_id"] for r in selected), default=None),
            "labels": {
                oxide: {
                    "negative": sum(r["operational_labels"][oxide] == 0 for r in selected),
                    "positive": sum(r["operational_labels"][oxide] == 1 for r in selected),
                    "unknown": sum(r["operational_labels"][oxide] is None for r in selected),
                }
                for oxide in OXIDES
            },
        }
    return result


def select(project_root: Path) -> dict:
    audit = json.loads((project_root / AUDIT).read_text())
    with (project_root / LUT).open() as stream:
        records, exclusions = development_selection(audit, list(csv.reader(stream)))
    eligible = {}
    for folder, entry in sorted(audit["calib_2021"].items()):
        reasons = list(entry["final_holdout_exclusion_reasons"])
        if entry["status"] == "matched":
            row = entry["matches"][0]["composition_row"]
            if row["Used for 2021 Mn calibration"] != "1":
                reasons.append("not_used_for_2021_calibration")
            if all(v is None for v in operational_labels(row).values()):
                reasons.append("no_known_operational_labels")
        if reasons:
            exclusions.append({"role": "test", "folder": folder, "reasons": reasons})
        else:
            eligible[folder] = entry
    snapshots = []
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {
            pool.submit(test_metadata, project_root, folder, entry): folder
            for folder, entry in eligible.items()
        }
        for i, future in enumerate(as_completed(futures), 1):
            measurement, info = future.result()
            snapshots.append(info)
            if measurement is None:
                exclusions.append(
                    {
                        "role": "test",
                        "folder": info["folder"],
                        "reasons": ["first_measurement_metadata_failed"],
                        "error": info["error"],
                    }
                )
            else:
                records.append(measurement)
            suffix = f" ERROR: {info['error']}" if "error" in info else ""
            print(f"Metadata {i}/{len(futures)}: {info['folder']}{suffix}", flush=True)
    records.sort(key=lambda r: (r["role"], r["folder"]))
    identities = [r["physical_sample_id"] for r in records]
    if len(identities) != len(set(identities)):
        raise ValueError("Duplicate physical material in frozen selected inventory")
    inventory = {
        "schema_version": 1,
        "protocol_id": PROTOCOL,
        "selection_locked_at": datetime.now(UTC).isoformat(),
        "phase": "selection_locked_before_measurement_downloads",
        "no_predictions_read_or_generated": True,
        "selection_rule": {
            "development": (
                "calib_2015: unique published alias, Used2015=1, unique LUT index=1. "
                "Include all standards with at least one reported 0<oxide wt%<=0.1; "
                "fill to128 with SHA256('20261013:'+folder) ordering."
            ),
            "test": (
                "All audit-eligible calib_2021 folders, Used2021=1 and >=1 known operational "
                "label; unique lexicographically first CCS CSV must date>=2016-01-01. "
                "Exclude ambiguous/unmatched names, every2015 alias, prior preflight materials, "
                "Li/Mn derivatives and Nau2b. Group WMB24a/b as wmb24."
            ),
            "measurement": "One first spatial point per standard. Reader averages shots6..N.",
            "label_definition": (
                "Reported oxide wt%: 0<value<=0.1 negative; value>=1 positive; "
                "zero, blank, and intermediate values unknown. This is operational "
                "exceedance of0.5wt% with a guard band, not chemical absence or LoD."
            ),
            "download_failures": "Preserve selected sample and report failure; no replacement.",
        },
        "pinned_inputs": {
            str(AUDIT): sha256(project_root / AUDIT),
            str(LUT): sha256(project_root / LUT),
            audit["source_compositions"]["path"]: audit["source_compositions"]["sha256"],
        },
        "provenance": {
            "dataset_id": DATASET_ID,
            "doi": "10.17189/1519485",
            "license_id": "us-pd",
            "license_url": (
                "https://data.nasa.gov/dataset/msl-mars-chemcam-libs-spectra-4-5-rdr-v1-0-f9501"
            ),
            "license_basis": "NASA explicit us-pd; DATA_SET_ID must match each PDS label.",
            "wavelength_medium": "vacuum",
            "wavelength_units": "nm",
            "intensity_units": "photons/shot/mm^2/sr/nm",
            "calibration_documentation": BASE + "calinfo.txt",
            "composition_url": BASE + "ccam_calibration_compositions.csv",
            "wavelength_method_sources": [
                "https://doi.org/10.1016/j.sab.2016.12.003",
                "https://doi.org/10.1016/j.sab.2021.106223",
                "https://www.osti.gov/pages/servlets/purl/1785483",
            ],
            "wavelength_method_evidence": (
                "Clegg2017 wavelength calibration section: Ti baseline wavelength-channel map "
                "uses known vacuum lines and spectra are resampled to that map. Gasda2021 "
                "sections2.1/2.3.1 identify the same LANL testbed and Ti calibration before "
                "each laboratory sample run, citing Clegg2017. CALINFO links calib_2015 "
                "and calib_2021 to those methods. Medium is based on this method chain, "
                "not chamber pressure."
            ),
            "composition_evidence": (
                "Clegg2017 standard provenance: independent XRF/dry combustion or certified "
                "values; missing values are not zero. Gasda2021 section2.2: commercial "
                "reference standards or independent ACTLabs fusion ICP-MS. Values are "
                "published assay estimates, not certified per-cell upper bounds."
            ),
            "evidence_snapshots": [
                {"path": str(path), "sha256": sha256(project_root / path)}
                for path in (
                    Path("data/raw/chemcam_external/clegg2017.pdf"),
                    Path("data/raw/adaptation_candidates_ml/gasda2021.pdf"),
                    Path("data/raw/chemcam_external/calinfo.txt"),
                    Path("data/raw/chemcam_external/license.html"),
                )
            ],
        },
        "directory_snapshots": sorted(snapshots, key=lambda v: v["folder"]),
        "records": records,
        "exclusions": sorted(exclusions, key=lambda v: (v["role"], v["folder"])),
        "selection_support": support(records),
    }
    return inventory


def download_measurement(project_root: Path, item: dict) -> dict:
    item = dict(item)
    try:
        spectrum = download(
            item["url"],
            project_root / item["local_relative_path"],
            expected_sha256=item["input_sha256"],
        )
        item.update(input_sha256=spectrum["sha256"], input_bytes=spectrum["bytes"])
        label = download(
            item["label_url"],
            project_root / item["label_local_relative_path"],
            expected_sha256=item["label_sha256"],
        )
        item.update(label_sha256=label["sha256"], label_bytes=label["bytes"])
        label_text = (project_root / item["label_local_relative_path"]).read_text()
        if DATASET_ID not in label_text:
            raise ValueError("PDS label has an unexpected DATA_SET_ID")
        if item["filename"].upper() not in label_text.upper():
            raise ValueError("PDS label does not identify the selected CSV file")
        with (project_root / item["local_relative_path"]).open("rb") as stream:
            if not stream.read(256).lstrip().startswith(b"#"):
                raise ValueError("CSV is not a comment-header ChemCam CCS product")
        item["status"] = "downloaded"
        item.pop("error", None)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        item["status"] = "download_failed"
        item["error"] = f"{type(exc).__name__}: {exc}"
    return item


def source_amendments(project_root: Path, inventory: dict, inventory_path: Path) -> set[str]:
    """Only documented missing source files can bypass retries of a frozen failure."""
    path = project_root / AMENDMENTS
    if not path.exists():
        return set()
    amendment = json.loads(path.read_text())
    if (
        amendment.get("schema_version") != 1
        or amendment.get("locked_before_predictions") is not True
    ):
        raise ValueError("Invalid source amendment schema or lock")
    if amendment.get("intake_sha256") != sha256(inventory_path):
        raise ValueError("Amended intake hash changed; refusing to rewrite or repin it")
    if amendment.get("alias_audit_sha256") != sha256(project_root / AUDIT):
        raise ValueError("Amended alias audit hash changed")
    by_id = {r["measurement_id"]: r for r in inventory["records"]}
    excluded: set[str] = set()
    for entry in amendment["exclusions"]:
        identity = entry["measurement_id"]
        if identity in excluded:
            raise ValueError("Duplicate amendment identity")
        item = by_id.get(identity)
        if item is None or item["status"] != "download_failed":
            raise ValueError("Source amendment must identify an existing failed record")
        evidence = project_root / entry["evidence_path"]
        if entry["evidence_url"] != item["url"].rsplit("/", 1)[0] + "/":
            raise ValueError("Source amendment evidence identifies a different folder")
        if not evidence.exists():
            download(entry["evidence_url"], evidence, expected_sha256=entry["evidence_sha256"])
        if sha256(evidence) != entry["evidence_sha256"]:
            raise ValueError("Source amendment evidence hash changed")
        parser = Links()
        parser.feed(evidence.read_text())
        if not parser.hrefs:
            raise ValueError("Source amendment evidence is not a directory listing")
        if any(
            href.rsplit("/", 1)[-1].lower() == item["filename"].lower() for href in parser.hrefs
        ):
            raise ValueError("Selected source file is present in the amendment evidence")
        excluded.add(identity)
    return excluded


def verify_completed_inventory(project_root: Path, inventory: dict, excluded: set[str]) -> bool:
    """Verify or restore pinned bytes without changing a finished inventory's hash."""
    if any(
        r["status"] != "downloaded" and r["measurement_id"] not in excluded
        for r in inventory["records"]
    ):
        return False
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [
            pool.submit(download_measurement, project_root, item)
            for item in inventory["records"]
            if item["measurement_id"] not in excluded
        ]
        for future in as_completed(futures):
            verified = future.result()
            if verified["status"] != "downloaded":
                raise ValueError(f"Frozen source verification failed: {verified['error']}")
    return True


def verify_pinned_inputs(project_root: Path, inventory: dict) -> None:
    sources = {
        str(LUT): BASE + "std_idx_2_file_lut.csv",
        "data/raw/chemcam_external/ccam_calibration_compositions.csv": (
            BASE + "ccam_calibration_compositions.csv"
        ),
    }
    for relative, expected in inventory["pinned_inputs"].items():
        path = project_root / relative
        if not path.exists() and relative in sources:
            download(sources[relative], path, expected_sha256=expected)
        if sha256(path) != expected:
            raise ValueError(f"Frozen metadata changed: {relative}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=ROOT)
    parser.add_argument("--metadata-only", action="store_true")
    args = parser.parse_args()
    project_root = args.project_root.resolve()
    output = project_root / INVENTORY
    if output.exists():
        inventory = json.loads(output.read_text())
        if inventory["protocol_id"] != PROTOCOL:
            raise ValueError("Refusing to overwrite an unrelated intake inventory")
        verify_pinned_inputs(project_root, inventory)
    else:
        inventory = select(project_root)
        atomic_json(output, inventory)
    if args.metadata_only:
        print(json.dumps(inventory["selection_support"], indent=2), flush=True)
        return
    excluded = source_amendments(project_root, inventory, output)
    if verify_completed_inventory(project_root, inventory, excluded):
        print("Pinned bytes verified; intake and source amendment remain unchanged.", flush=True)
        print(json.dumps(inventory["downloaded_support"], indent=2), flush=True)
        return
    if excluded:
        raise ValueError("Amended inventory has an undocumented failure or pending measurement")
    inventory["phase"] = "downloading_pinned_measurements"
    atomic_json(output, inventory)
    index = {r["measurement_id"]: i for i, r in enumerate(inventory["records"])}
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [
            pool.submit(download_measurement, project_root, item) for item in inventory["records"]
        ]
        for i, future in enumerate(as_completed(futures), 1):
            item = future.result()
            inventory["records"][index[item["measurement_id"]]] = item
            atomic_json(output, inventory)
            print(
                f"Download {i}/{len(futures)} {item['status']}: {item['measurement_id']}",
                flush=True,
            )
    inventory["download_status_counts"] = dict(Counter(r["status"] for r in inventory["records"]))
    inventory["phase"] = (
        "complete"
        if all(r["status"] == "downloaded" for r in inventory["records"])
        else "incomplete_downloads"
    )
    inventory["completed_at"] = datetime.now(UTC).isoformat()
    inventory["downloaded_support"] = support(
        [r for r in inventory["records"] if r["status"] == "downloaded"]
    )
    inventory["total_downloaded_bytes"] = sum(
        r.get("input_bytes", 0) + r.get("label_bytes", 0) for r in inventory["records"]
    )
    atomic_json(output, inventory)
    print(json.dumps(inventory["downloaded_support"], indent=2), flush=True)


if __name__ == "__main__":
    main()
