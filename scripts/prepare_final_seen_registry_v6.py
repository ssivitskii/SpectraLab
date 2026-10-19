"""Compile historical exclusion identities from metadata, never open spectral arrays."""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import re
from pathlib import Path

from spectralab_ml.config import repository_root

SOURCES = {
    "historical": "data/reference/final_search_v3_admission.json",
    "development": "data/reference/development_v4_source_plan.json",
    "alias_audit": "data/reference/chemcam_adaptation_alias_audit.json",
    "preflight": "data/reference/chemcam_external_protocol.json",
    "chemistry": "data/raw/chemcam_external/ccam_calibration_compositions.csv",
    "external_source": "data/reference/chemcam_external_source.json",
    "previous_candidate_audit": "data/raw/adaptation_candidates_backend/candidate_audit.json",
}
FIELDS = {
    "measurement_ids": "measurement_id",
    "physical_sample_ids": "physical_sample_id",
    "family_ids": "family_id",
    "session_ids": "session_id",
    "input_sha256s": "input_sha256",
}


def normalized(value):
    return re.sub("[^a-z0-9]", "", value.lower().strip())


def compile_registry(documents, chemistry, source_metadata):
    """Only documented aliases enter; downloaded-only files remain explicitly quarantined."""
    base = documents["historical"]["previously_seen_ids"]
    registry = {key: set(base[key]) for key in (*FIELDS, "assay_ids")}
    aliases = {}
    source_url = documents["alias_audit"]["source_url"]
    chemistry_sha = source_metadata["chemistry"]["sha256"]
    if chemistry_sha != documents["alias_audit"]["source_compositions"]["sha256"]:
        raise ValueError("Pinned chemistry differs from historical alias audit")
    alias_rows = {}
    known_physical = {
        r["composition_row_number"]: r["physical_sample_id"]
        for r in documents["development"]["records"]
    }
    for i, row in enumerate(chemistry, 2):
        for key in ("Target", "Spectrum Name", "Sample Name"):
            alias = normalized(row.get(key, ""))
            if alias:
                alias_rows.setdefault(alias, set()).add(i)
    unresolved_aliases = set()

    def edge(field, left, right, source, reason):
        if not left or not right or left == right:
            return
        a, b = sorted([left, right])
        aliases[(field, a, b)] = {
            "field": field,
            "left": a,
            "right": b,
            "source": source_metadata[source]["path"],
            "source_sha256": source_metadata[source]["sha256"],
            "reason": reason,
        }

    def material_edges(canonical, family, row, *, source, folder=None):
        published = [row.get(k, "").strip() for k in ("Target", "Spectrum Name", "Sample Name")]
        values = {v for v in published if v}
        if folder:
            values.add(folder)
        for value in list(values):
            rows = alias_rows.get(normalized(value), set())
            reviewed_same_physical = bool(rows) and all(
                known_physical.get(number) == canonical for number in rows
            )
            if len(rows) != 1 and not reviewed_same_physical:
                values.remove(value)
                unresolved_aliases.add(value)
        values.update(normalized(v) for v in list(values))
        values.update(
            "chemcam-calibration:" + normalized(v)
            for v in list(values)
            if not v.startswith("chemcam-calibration:")
        )
        for value in values:
            edge(
                "physical_sample_ids",
                canonical,
                value,
                source,
                (
                    "Same published composition row / reviewed historical physical grouping; no "
                    "fuzzy match"
                ),
            )
            edge(
                "family_ids",
                family,
                value,
                source,
                "Existing recorded parent-family grouping; no new independence inference",
            )
        registry["physical_sample_ids"].add(canonical)
        registry["family_ids"].add(family)

    records = documents["development"]["records"]
    for record in records:
        for field, key in FIELDS.items():
            registry[field].add(record[key])
        row_number = record["composition_row_number"]
        row = chemistry[row_number - 2]
        if row != record["composition_row"]:
            raise ValueError("Historical development composition row changed")
        registry["assay_ids"].add(f"ccam_composition_csv:{chemistry_sha}:row:{row_number}")
        material_edges(
            record["physical_sample_id"],
            record["family_id"],
            row,
            source="development",
            folder=record["folder"],
        )
    preflight = documents["preflight"]
    for name in preflight["test_sample_ids"]:
        matches = [
            (i + 2, r)
            for i, r in enumerate(chemistry)
            if r.get("Spectrum Name", "").strip().casefold() == name.casefold()
        ]
        if len(matches) != 1:
            raise ValueError("Historical preflight exact chemistry mapping is no longer unique")
        number, row = matches[0]
        canonical = normalized(row["Spectrum Name"])
        material_edges(canonical, canonical, row, source="preflight")
        registry["assay_ids"].add(f"ccam_composition_csv:{chemistry_sha}:row:{number}")
    registry["session_ids"].add(preflight["acquisition_session_id"])
    registry["input_sha256s"].add(preflight["input_sha256"]["msl_ccam_libs_calib.csv"])

    audit = documents["previous_candidate_audit"]
    source_id = "aalto-lasolibs-elements:Cu"
    family = "aalto-lasolibs-elements:Cu-source-stock-unresolved"
    registry["physical_sample_ids"].add(source_id)
    registry["family_ids"].add(family)
    registry["measurement_ids"].add("aalto-lasolibs-elements:Cu:processed-quantile-export")
    url = "https://users.aalto.fi/~lainei1/assets/elements/plotly/Cu.html"
    # The source-qualified aliases identify this prior export, never every copper specimen.
    for value in ("aalto-lasolibs:Cu", "aalto-elements:Cu", url):
        edge(
            "physical_sample_ids",
            source_id,
            value,
            "previous_candidate_audit",
            "Documented previously inspected Aalto Cu export; stock/lot not recovered",
        )
        edge(
            "family_ids",
            family,
            value,
            "previous_candidate_audit",
            "Conservative same-Cu-stock closure within this specific published source",
        )
    previous_files, quarantine = [], []
    for name in (
        "aalto_Cu_spectrum.csv",
        "aalto_Cu_plot.html",
        "labtrace_avantes_7mJ.h5",
        "firefly_avantes_multi_20mJ.h5",
    ):
        value = audit["files"][name]
        registry["input_sha256s"].add(value["sha256"])
        row = {
            "path": "data/raw/adaptation_candidates_backend/" + name,
            **value,
            "source": source_metadata["previous_candidate_audit"],
        }
        if name.endswith(".h5"):
            row["status"] = "quarantined_previous_download_metadata_inspection"
            row["reason"] = (
                "Published historical audit confirms download and metadata/axis "
                "inspection, not intensity viewing or fitting. Block exact bytes "
                "pending review; do not call the eleven materials model-seen."
            )
            row["source_qualified_material_ids_to_review"] = [
                f"figshare:21984989:sample-{i:02d}" for i in range(11)
            ]
            quarantine.append(row)
        else:
            row["status"] = "documented_previous_spectrum_inspection"
        previous_files.append(row)
    result = {key: sorted(values) for key, values in registry.items()}
    result["aliases"] = [aliases[key] for key in sorted(aliases)]
    result.update(
        schema_version=6,
        registry_id="final-seen-v6",
        review_status="incomplete",
        complete_independence_review=False,
        source_metadata=source_metadata,
        historical_registry_preserved=True,
        prior_numeric_artifacts=previous_files,
        quarantined_artifacts=quarantine,
        registry_counts={key: len(values) for key, values in registry.items()},
        alias_edge_count=len(aliases),
        ambiguous_published_aliases_not_merged=sorted(unresolved_aliases),
        unresolved_identity_notes=[
            (
                "Aalto Cu stock, lot, physical replicate and acquisition session IDs remain "
                "unresolved. Exclude this source-Cu family, not all copper from other "
                "sources."
            ),
            (
                "Fe-Co HDF5 historical evidence proves metadata inspection only; resolve "
                "intensity-viewing history and actual material/session IDs before considering "
                "other versions of these files."
            ),
            (
                "Historical preflight compiled matrix lacks per-replicate final-registry "
                "measurement IDs; all64 physical names, session and compiled input hash are "
                "blocked. Repacked subsets require material/session provenance."
            ),
            (
                "SGR1 and M6-HAGGERTY were excluded for chemistry mismatch in old preflight; "
                "compiled file was read but spectrum-level viewing status is not fully "
                "attested. Do not admit them merely because their names are absent."
            ),
            (
                "Aliases added here are supported within named source documents. "
                "Metadata-only candidate searches do not automatically make every assayed "
                "material model-seen."
            ),
            (
                "Current software does not enforce review_status or these notes. A successful "
                "structural preflight cannot replace the pending human source-independence "
                "audit."
            ),
        ],
        compilation_policy=(
            "Union preserves all historical blocks; source-qualified aliases only; no "
            "spectral array opened, no guesses of missing session/lot identities."
        ),
        chemistry_source_url=source_url,
    )
    return result


def build_registry(root):
    documents, source_metadata = {}, {}
    chemistry = None
    for name, relative in SOURCES.items():
        payload = (root / relative).read_bytes()
        source_metadata[name] = {
            "path": relative,
            "sha256": hashlib.sha256(payload).hexdigest(),
            "bytes": len(payload),
        }
        if name == "chemistry":
            chemistry = list(csv.DictReader(io.StringIO(payload.decode())))
        else:
            documents[name] = json.loads(payload)
    return compile_registry(documents, chemistry, source_metadata)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path, default=Path("data/reference/final_seen_registry_v6.json")
    )
    args = parser.parse_args()
    registry = build_registry(repository_root())
    with args.output.open("x") as stream:
        json.dump(registry, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")
    print(
        json.dumps(
            {
                "path": str(args.output),
                "counts": registry["registry_counts"],
                "aliases": registry["alias_edge_count"],
                "review_status": registry["review_status"],
            }
        )
    )


if __name__ == "__main__":
    main()
