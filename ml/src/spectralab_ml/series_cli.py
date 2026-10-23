"""Reproducible offline ChemCam series quality control and CSV export."""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
from pathlib import Path

from spectralab_ml.data.chemcam_measurements import read_chemcam_ccs_shots
from spectralab_ml.data.series_metadata import read_series_metadata, validate_series_coverage
from spectralab_ml.data.shot_series import ShotSeriesPolicy, aggregate_shot_series


def _csv_bytes(wavelength, values, header: str) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(["wavelength_nm", header])
    writer.writerows(zip(wavelength, values, strict=True))
    return output.getvalue().encode("utf-8")


def command_analyze_series(args: argparse.Namespace) -> None:
    metadata_path, input_path = Path(args.metadata), Path(args.input)
    metadata, metadata_sha256 = read_series_metadata(metadata_path)
    if args.discard_first_shots < 0:
        raise ValueError("discard_first_shots must be nonnegative")
    policy = ShotSeriesPolicy(
        min_snr=args.min_snr,
        min_accepted_shots=args.min_accepted_shots,
        saturation_ceiling=args.saturation_ceiling,
        outlier_mad_z=args.outlier_mad_z,
    )
    policy.validate()
    output = Path(args.output_dir)
    if output.exists() or output.is_symlink():
        raise ValueError("Output directory must be new; existing runs are never overwritten")
    wave, shots, source = read_chemcam_ccs_shots(input_path, metadata["input_sha256"])
    coverage = validate_series_coverage(wave, metadata)
    discard = args.discard_first_shots
    if discard >= len(shots):
        raise ValueError("discard_first_shots must leave at least one shot")
    result = aggregate_shot_series(wave, shots[discard:], policy=policy)
    receipt = {
        **result.receipt,
        "workflow": "chemcam-shot-series-v1",
        "measurement": metadata,
        "metadata_sha256": metadata_sha256,
        "source": source,
        "coverage": coverage,
        "discard_first_shots": discard,
        "shot_number_convention": "one_based_original_CCS_column",
        "discarded_shot_numbers": list(range(1, discard + 1)),
        "accepted_shot_numbers": [i + discard + 1 for i in result.receipt["accepted_shot_indices"]],
        "outputs": {},
    }
    for shot in receipt["shots"]:
        shot["shot_number"] = shot["shot_index"] + discard + 1
    files: dict[str, bytes] = {}
    if result.intensity is not None:
        files["aggregate.csv"] = _csv_bytes(wave, result.intensity, "intensity")
        files["dispersion.csv"] = _csv_bytes(wave, result.pointwise_mad, "intensity_mad")
        aggregate_metadata = {
            **metadata,
            "axis": {
                key: value
                for key, value in metadata["axis"].items()
                if key not in {"bands_nm", "max_step_nm"}
            },
            "input_sha256": hashlib.sha256(files["aggregate.csv"]).hexdigest(),
        }
        files["metadata.json"] = (
            json.dumps(aggregate_metadata, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
        ).encode("utf-8")
    receipt["outputs"] = {
        name: {"sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)}
        for name, raw in files.items()
    }
    text = json.dumps(receipt, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
    # Validate and serialize everything before creating a new run directory.
    output.mkdir(parents=True, exist_ok=False)
    for name, raw in files.items():
        with (output / name).open("xb") as stream:
            stream.write(raw)
    with (output / "receipt.json").open("x", encoding="utf-8") as stream:
        stream.write(text)
    print(text, end="")
    if receipt["status"] != "completed":
        raise SystemExit(2)


def register_series_workflow(subparsers: argparse._SubParsersAction) -> None:
    command = subparsers.add_parser(
        "analyze-series", help="QC a pinned ChemCam CCS shot series and export median spectrum"
    )
    command.add_argument("--input", required=True, help="ChemCam CCS CSV with shot1..shotN")
    command.add_argument(
        "--metadata", required=True, help="Series metadata JSON with pinned SHA256"
    )
    command.add_argument(
        "--output-dir", required=True, help="New run directory for CSVs and receipt"
    )
    command.add_argument("--discard-first-shots", type=int, default=5)
    command.add_argument("--min-snr", type=float, default=3.0)
    command.add_argument("--min-accepted-shots", type=int, default=2)
    command.add_argument("--saturation-ceiling", type=float)
    command.add_argument("--outlier-mad-z", type=float, default=6.0)
    command.set_defaults(handler=command_analyze_series)
