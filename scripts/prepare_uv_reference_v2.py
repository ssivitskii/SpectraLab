"""Fetch explicitly vacuum NIST lines for ChemCam v2; pin every unmodified export."""

from __future__ import annotations

import hashlib
import json
import subprocess
from urllib.parse import urlencode

from spectralab_ml.config import repository_root
from spectralab_ml.data.reference import ReferenceCatalog


def main() -> None:
    directory = repository_root() / "data/reference/nist_uv_visible_v2"
    directory.mkdir(parents=True, exist_ok=True)
    if (directory / "catalog.json").exists():
        catalog = ReferenceCatalog.from_cache(directory / "catalog.json")
        for source in catalog.metadata["sources"]:
            if (
                hashlib.sha256((directory / source["raw_file"]).read_bytes()).hexdigest()
                != source["checksum_sha256"]
            ):
                raise ValueError("Pinned NIST export changed")
        print(f"Verified {len(catalog.lines)} pinned vacuum lines")
        return
    lines, sources, reports = [], [], {}
    for element in ("Na", "Mg", "Ca", "Fe"):
        for stage, roman in ((1, "I"), (2, "II")):
            params = dict(
                spectra=f"{element} {roman}",
                low_w=240,
                upp_w=900,
                unit=1,
                format=2,
                line_out=0,
                remove_js="on",
                en_unit=0,
                output=0,
                bibrefs=1,
                page_size=50000,
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
            url = "https://physics.nist.gov/cgi-bin/ASD/lines1.pl?" + urlencode(params)
            path = directory / f"{element}_{roman}.csv"
            if not path.exists():
                temporary = path.with_suffix(".download")
                subprocess.run(
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
                        "16000000",
                        url,
                        "-o",
                        str(temporary),
                    ],
                    check=True,
                )
                temporary.replace(path)
            metadata = {
                "source": "nist_asd_saved_export",
                "source_version": "2026-10-13-v2",
                "retrieved_at": "2026-10-13",
                "source_url": url,
                "element": element,
                "ion_stage": stage,
                "units": "nm",
                "wavelength_medium": "vacuum",
                "checksum_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "export_parameters": params,
                "raw_file": path.name,
                "official_nist_export": True,
            }
            catalog, report = ReferenceCatalog.from_delimited(path, metadata=metadata)
            if not catalog.lines or any(
                line.wavelength_medium != "vacuum" for line in catalog.lines
            ):
                raise ValueError("NIST source did not provide explicit vacuum lines")
            sources.append(metadata)
            lines.extend(catalog.lines)
            reports[path.stem] = report.to_dict()
            path.with_suffix(".metadata.json").write_text(json.dumps(metadata, indent=2) + "\n")
            print(path.stem, len(catalog.lines), flush=True)
    catalog = ReferenceCatalog(
        lines,
        {
            "schema_version": 2,
            "source": "nist_asd_saved_export",
            "source_version": "uv-visible-v2-2026-10-13",
            "units": "nm",
            "wavelength_medium": "vacuum",
            "ion_stages": [1, 2],
            "sources": sources,
            "notice": "Atomic reference, not mixture composition labels.",
        },
    )
    catalog.to_cache(directory / "catalog.json")
    (directory / "import_report.json").write_text(json.dumps(reports, indent=2) + "\n")


if __name__ == "__main__":
    main()
