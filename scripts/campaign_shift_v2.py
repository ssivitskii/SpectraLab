"""Diagnose campaign differences using only the already disclosed v1 ChemCam inputs."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path
from uuid import uuid4

import numpy as np
from spectralab_ml.config import repository_root
from spectralab_ml.data.chemcam_measurements import read_chemcam_ccs
from spectralab_ml.data.reference import ReferenceCatalog
from spectralab_ml.evaluation.adaptation_protocol import validate_protocol
from spectralab_ml.evaluation.campaign_shift_v2 import (
    BANDS,
    aggregate_physical,
    composition_features,
    conditional_domain_separability,
    descriptive_summaries,
    domain_separability,
    extract_features,
    feature_plan,
)
from spectralab_ml.evaluation.real_validation import interpolate_observed

V1_PROTOCOL_HASH = "f755b186a0c3fd3fce923ccc60b14a6aeb945e5f7ff06d9af718c581f3471bf1"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    with temporary.open("w") as stream:
        stream.write(json.dumps(value, indent=2, allow_nan=False) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def run(root: Path, reference_path: Path, expected_reference_sha256: str) -> Path:
    protocol_path = root / "data/reference/chemcam_adaptation_protocol.json"
    protocol = json.loads(protocol_path.read_text())
    if validate_protocol(protocol)["protocol_hash"] != V1_PROTOCOL_HASH:
        raise ValueError("Only the already disclosed v1 protocol may enter this diagnostic")
    if sha(reference_path) != expected_reference_sha256:
        raise ValueError("Pinned diagnostic NIST reference checksum mismatch")
    plan = feature_plan(ReferenceCatalog.from_cache(reference_path))
    records = [
        {**record, "campaign": "campaign_2015" if role == "development" else "campaign_2021"}
        for role in ("development", "test")
        for record in protocol[role]
    ]
    output = (
        root
        / "reports/campaign_shift_v2"
        / f"diagnostic-{datetime.now(UTC):%Y%m%dT%H%M%S}-{uuid4().hex[:8]}"
    )
    output.mkdir(parents=True)
    frozen = {
        "schema_version": 1,
        "diagnostic_only": True,
        "v1_protocol_hash": V1_PROTOCOL_HASH,
        "created_at": datetime.now(UTC).isoformat(),
        "feature_plan": plan,
        "reference_sha256": expected_reference_sha256,
        "reference_metadata": ReferenceCatalog.from_cache(reference_path).metadata,
        "measurements": [
            {
                key: record[key]
                for key in (
                    "measurement_id",
                    "input_sha256",
                    "physical_sample_id",
                    "session_id",
                    "campaign",
                )
            }
            for record in records
        ],
        "implementation_sha256": {
            str(path.relative_to(root)): sha(path)
            for path in [
                Path(__file__).resolve(),
                *(
                    root / "ml/src/spectralab_ml" / name
                    for name in (
                        "evaluation/campaign_shift_v2.py",
                        "evaluation/real_validation.py",
                        "evaluation/adaptation_protocol.py",
                        "data/chemcam_measurements.py",
                        "data/reference.py",
                        "preprocessing/pipeline.py",
                    )
                ),
            ]
        },
        "dependencies": {
            package: version(package) for package in ("numpy", "scikit-learn", "scipy")
        },
        "domain_classifier": {"C": 1.0, "maximum_folds": 5, "seed": 20261013, "threshold": 0.5},
    }
    write_json(output / "diagnostic_plan.json", frozen)
    grid = 240.0 + np.arange(6601) * 0.1
    spectra, readers = [], []
    for record in records:
        wave, mean, meta = read_chemcam_ccs(
            root / record["local_relative_path"], record["input_sha256"], discard_first_shots=5
        )
        spectra.append(
            interpolate_observed(
                wave, mean, grid, [list(b) for b in BANDS.values()], max_gap_nm=0.5
            )
        )
        readers.append({"measurement_id": record["measurement_id"], **meta})
    features, names = extract_features(grid, np.asarray(spectra), plan)
    rows, physical = aggregate_physical(records, features)
    spectral_domain = domain_separability(rows, physical)
    composition_domain = domain_separability(rows, composition_features(rows))
    conditional_domain = conditional_domain_separability(rows, physical)
    summary = {
        "schema_version": 1,
        "status": "completed",
        "diagnostic_only": True,
        "run_directory": str(output.relative_to(root)),
        "created_at": frozen["created_at"],
        "completed_at": datetime.now(UTC).isoformat(),
        "v1_protocol_hash": V1_PROTOCOL_HASH,
        "diagnostic_plan_sha256": sha(output / "diagnostic_plan.json"),
        "reference_sha256": expected_reference_sha256,
        "feature_plan": plan,
        "features": names,
        "physical_samples": len(rows),
        "measurement_spectra": len(records),
        "descriptive_summaries": descriptive_summaries(rows, physical, names),
        "domain_separability": {
            "spectral": spectral_domain,
            "composition_label_only": composition_domain,
        },
        "conditional_domain_separability": conditional_domain,
        "limitations": [
            "Both v1 development and disclosed former test are now diagnostic/development data.",
            "Campaign differences cannot establish instrument drift without shared controls.",
            "Class-conditional summaries fix one coarse label, not the full matrix composition.",
            "The label-only comparator is not complete composition adjustment or a causal test.",
            "Grouped-CV scores describe these campaigns; no final recognition model is selected.",
            "No blank controls or new unopened spectra are included in this diagnostic.",
        ],
    }
    write_json(output / "result.json", summary)
    write_json(
        output / "physical_features.json",
        {"feature_names": names, "rows": rows, "features": physical.tolist(), "reader": readers},
    )
    compact = json.loads(json.dumps(summary))
    for report in [
        *compact["domain_separability"].values(),
        *compact["conditional_domain_separability"].values(),
    ]:
        report.pop("out_of_fold", None)
        for fold in report.get("folds", []):
            for key in (
                "train_physical_ids",
                "validation_physical_ids",
                "train_clusters",
                "validation_clusters",
            ):
                fold.pop(key, None)
    write_json(root / "docs/campaign_shift_v2_summary.json", compact)
    print(output, flush=True)
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--reference-sha256", required=True)
    args = parser.parse_args()
    run(repository_root(), args.reference, args.reference_sha256)


if __name__ == "__main__":
    main()
