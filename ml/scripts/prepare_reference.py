"""Rebuild the checked-in NIST catalog offline from unmodified saved exports."""

from __future__ import annotations

import hashlib
import json
from urllib.parse import urlencode

from spectralab_ml.config import repository_root, stable_hash
from spectralab_ml.data.reference import ReferenceCatalog


def main() -> None:
    directory = repository_root() / "data/reference/nist_neutral_350_800"
    all_lines, sources, reports = [], [], {}
    for element in ("H", "He", "Na", "Mg", "Ca", "Fe"):
        path = directory / f"{element}_I.csv"
        checksum = hashlib.sha256(path.read_bytes()).hexdigest()
        params = dict(
            spectra=f"{element} I",
            low_w=350,
            upp_w=800,
            unit=1,
            format=2,
            line_out=0,
            remove_js="on",
            en_unit=0,
            output=0,
            bibrefs=1,
            page_size=1500 if element == "H" else 15000,
            show_obs_wl=1,
            show_calc_wl=1,
            order_out=0,
            show_av=3,
            tsb_value=0,
            A_out=0,
            intens_out="on",
            allowed_out=1,
            forbid_out=1,
        )
        metadata = {
            "source": "nist_asd_saved_export",
            "source_version": "saved-2026-10-13",
            "retrieved_at": "2026-10-13",
            "source_url": "https://physics.nist.gov/cgi-bin/ASD/lines1.pl?" + urlencode(params),
            "element": element,
            "ion_stage": 1,
            "units": "nm",
            "wavelength_medium": "vacuum",
            "checksum_sha256": checksum,
            "export_parameters": params,
            "raw_file": path.name,
            "official_nist_export": True,
        }
        existing_metadata = path.with_suffix(".metadata.json")
        if existing_metadata.exists():
            expected = json.loads(existing_metadata.read_text())["checksum_sha256"]
            if checksum != expected:
                raise ValueError(f"Saved raw export checksum mismatch: {path.name}")
        catalog, report = ReferenceCatalog.from_delimited(path, metadata=metadata)
        if not catalog.lines:
            raise ValueError(f"No accepted lines for {element}")
        all_lines.extend(catalog.lines)
        reports[element] = report.to_dict()
        sources.append(metadata)
        existing_metadata.write_text(json.dumps(metadata, indent=2) + "\n")
    metadata = {
        "source": "nist_asd_saved_export",
        "source_version": "neutral-vacuum-350-800-2026-10-13",
        "schema_version": 2,
        "retrieved_at": "2026-10-13",
        "units": "nm",
        "wavelength_medium": "vacuum",
        "ion_stage": 1,
        "checksum_sha256": stable_hash({s["raw_file"]: s["checksum_sha256"] for s in sources}),
        "sources": sources,
        "official_nist_export": True,
        "cleaning_rules": (
            "observed preferred within transition; Ritz fallback; "
            "exact duplicates removed; raw fields preserved"
        ),
        "notice": (
            "NIST atomic lines are reference data, not measured mixture spectra. "
            "Relative intensities from different elements are not concentration calibration."
        ),
    }
    catalog = ReferenceCatalog(
        sorted(all_lines, key=lambda line: (line.wavelength_nm, line.element)), metadata
    )
    catalog.to_cache(directory / "catalog.json")
    (directory / "import_report.json").write_text(json.dumps(reports, indent=2) + "\n")
    print(
        json.dumps(
            {
                "cache": str(directory / "catalog.json"),
                "accepted_lines": {e: r["accepted_rows"] for e, r in reports.items()},
                "dropped_lines": {e: len(r["dropped_rows"]) for e, r in reports.items()},
                "content_sha256": catalog.metadata["content_sha256"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
