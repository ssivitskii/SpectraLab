from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import time
from pathlib import Path
from urllib.parse import urlencode

from spectralab_ml.config import repository_root
from spectralab_ml.data.plasma_reference import (
    PLASMA_SPECIES,
    build_plasma_reference,
    write_plasma_reference,
)

LINE_PARAMETERS = {
    "low_w": 240,
    "upp_w": 900,
    "unit": 1,
    "format": 2,
    "line_out": 0,
    "remove_js": "on",
    "en_unit": 1,
    "output": 0,
    "bibrefs": 1,
    "page_size": 50000,
    "show_obs_wl": 1,
    "show_calc_wl": 1,
    "order_out": 0,
    "show_av": 3,
    "tsb_value": 0,
    "A_out": 0,
    "intens_out": "on",
    "allowed_out": 1,
    "forbid_out": 1,
    "enrg_out": "on",
    "J_out": "on",
    "g_out": "on",
}
LEVEL_PARAMETERS = {
    "de": 0,
    "submit": "Retrieve Data",
    "units": 1,
    "format": 2,
    "output": 0,
    "page_size": 15,
    "multiplet_ordered": 0,
    "level_out": "on",
    "j_out": "on",
    "g_out": "on",
    "temp": "",
    "biblio": "on",
}


def fetch(url: str, path, *, retries: int = 3) -> None:
    temporary = path.with_suffix(path.suffix + ".download")
    for attempt in range(retries):
        try:
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
                    "180",
                    "--max-filesize",
                    "16000000",
                    url,
                    "-o",
                    str(temporary),
                ],
                check=True,
            )
            break
        except subprocess.CalledProcessError:
            if attempt == retries - 1:
                raise
            time.sleep(5 * (attempt + 1))
    temporary.replace(path)
    time.sleep(1)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--download", action="store_true", help="Fetch missing exports")
    args = parser.parse_args()
    directory = repository_root() / "data/reference/nist_plasma_v3"
    (directory / "raw").mkdir(parents=True, exist_ok=True)
    pins_path = directory / "sources.json"
    pins = json.loads(pins_path.read_text()) if pins_path.exists() else {}
    sources = []
    for element, stage, roman in PLASMA_SPECIES:
        spectrum = f"{element} {roman}"
        for kind, script, parameters, key in (
            ("lines", "lines1.pl", LINE_PARAMETERS, "spectra"),
            ("levels", "energy1.pl", LEVEL_PARAMETERS, "spectrum"),
        ):
            url = f"https://physics.nist.gov/cgi-bin/ASD/{script}?" + urlencode(
                {key: spectrum, **parameters}
            )
            path = directory / "raw" / f"{element}_{roman}_{kind}.csv"
            if not path.exists():
                if not args.download:
                    raise FileNotFoundError(f"{path.name} missing; rerun with --download")
                fetch(url, path)
            checksum = hashlib.sha256(path.read_bytes()).hexdigest()
            pinned = pins.get(path.name)
            if pinned is not None and pinned["sha256"] != checksum:
                raise ValueError(f"Pinned NIST export changed: {path.name}")
            sources.append(
                {
                    "file": f"raw/{path.name}",
                    "element": element,
                    "ion_stage": stage,
                    "kind": kind,
                    "source_url": url,
                    "sha256": checksum,
                    "retrieved_at": (pinned or {}).get("retrieved_at") or time.strftime("%Y-%m-%d"),
                }
            )
            print(f"{path.name}: {checksum[:12]}", flush=True)
    pins_path.write_text(
        json.dumps(
            {
                Path(item["file"]).name: {
                    "sha256": item["sha256"],
                    "retrieved_at": item["retrieved_at"],
                }
                for item in sources
            },
            indent=2,
        )
        + "\n"
    )
    reference = build_plasma_reference(directory, sources)
    write_plasma_reference(directory, reference)
    print(json.dumps(reference["report"]["totals"], indent=2))


if __name__ == "__main__":
    main()
