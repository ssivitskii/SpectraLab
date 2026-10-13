"""Download pinned public measurements or inspect their admission to external test."""

from __future__ import annotations

import argparse
import hashlib
import json
import urllib.request

from spectralab_ml.config import repository_root
from spectralab_ml.evaluation.external import inspect_external_test


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--download", action="store_true", help="Fetch public pinned PDS files")
    args = parser.parse_args()
    root = repository_root()
    manifest = json.loads((root / "data/reference/external_pds_intake.json").read_text())
    destination = root / "data/raw/pds_libs"
    destination.mkdir(parents=True, exist_ok=True)
    if args.download:
        for record in manifest["downloads"]:
            path = destination / record["name"]
            if path.exists() and hashlib.sha256(path.read_bytes()).hexdigest() == record["sha256"]:
                continue
            raw = urllib.request.urlopen(record["url"], timeout=60).read()
            if hashlib.sha256(raw).hexdigest() != record["sha256"]:
                raise ValueError("Public dataset changed; review provenance before updating hashes")
            path.write_bytes(raw)
    result = inspect_external_test(manifest, destination)
    output = root / "reports/verification/external_data_intake.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {"report": str(output), "status": result["status"], "blockers": result["blockers"]},
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
