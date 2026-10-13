"""Apply a saved instrument profile to one new ChemCam CCS measurement.

The caller explicitly identifies the instrument; this does not infer instrument
identity, chemical absence or wavelength medium from a filename. The saved
profile fixes vacuum nm, bands, feature transform and development-only thresholds.
Scores are uncalibrated and decisions refer to its operational oxide policy.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
from spectralab_ml.data.chemcam_measurements import MAX_CCS_BYTES, read_chemcam_ccs
from spectralab_ml.evaluation.real_validation import interpolate_observed
from spectralab_ml.models.instrument_adaptation import InstrumentAdapter

MAX_GAP_NM = 0.5
UNSUPPORTED_ELEMENTS = ("H", "He")


def predict_measurement(artifact: Path, input_path: Path, instrument_id: str) -> dict:
    """Predict without fitting, calibration, threshold selection or API registration."""
    if not isinstance(instrument_id, str) or not instrument_id.strip():
        raise ValueError("An explicit nonempty instrument_id is required")
    model = InstrumentAdapter.load(artifact, expected_instrument_id=instrument_id)
    manifest = model.manifest
    thresholds = manifest["thresholds"]
    if thresholds is None:
        raise ValueError("Saved per-element thresholds are required for instrument prediction")
    if set(model.class_order) & set(UNSUPPORTED_ELEMENTS):
        raise ValueError("This ChemCam prediction contract does not support H or He")
    input_path = Path(input_path)
    if not input_path.is_file():
        raise ValueError("CCS input must be an existing file")
    if input_path.stat().st_size > MAX_CCS_BYTES:
        raise ValueError("CCS file exceeds the 16 MB size limit")
    with input_path.open("rb") as stream:
        raw = stream.read(MAX_CCS_BYTES + 1)
    if len(raw) > MAX_CCS_BYTES:
        raise ValueError("CCS file exceeds the 16 MB size limit")
    input_sha256 = hashlib.sha256(raw).hexdigest()
    del raw
    wavelengths, intensity, aggregation = read_chemcam_ccs(input_path, input_sha256)
    spectrum = interpolate_observed(
        wavelengths,
        intensity,
        model.grid_nm,
        model.observed_intervals,
        max_gap_nm=MAX_GAP_NM,
    )
    scores = model.scores(spectrum[np.newaxis, :])[0]
    predictions = [
        {
            "element": element,
            "status": "evaluated",
            "score": float(score),
            "threshold": thresholds[element],
            "detected": bool(score >= thresholds[element]),
        }
        for element, score in zip(model.class_order, scores, strict=True)
    ]
    predictions.extend(
        {
            "element": element,
            "status": "unknown",
            "reason": "unsupported_by_instrument_profile",
            "score": None,
            "threshold": None,
            "detected": None,
        }
        for element in UNSUPPORTED_ELEMENTS
    )
    return {
        "schema_version": 1,
        "artifact_type": "instrument_prediction",
        "instrument_id": manifest["instrument_id"],
        "profile_identity": model.identity,
        "input_sha256": input_sha256,
        "input_file": input_path.name,
        "score_kind": model.score_kind,
        "calibrated": False,
        "estimand": manifest["provenance"]["estimand"],
        "oxide_policy": manifest["provenance"]["oxide_policy"],
        "threshold_rule": manifest["threshold_rule"],
        "class_order": list(model.class_order),
        "aggregation": aggregation,
        "preprocessing": {
            "units": manifest["units"],
            "wavelength_medium": manifest["wavelength_medium"],
            "observed_intervals": model.observed_intervals,
            "grid_sha256": manifest["grid_sha256"],
            "mask_sha256": manifest["mask_sha256"],
            "feature_spec": manifest["feature_spec"],
            "max_gap_nm": MAX_GAP_NM,
            "input_points": len(wavelengths),
            "grid_points": len(model.grid_nm),
        },
        "predictions": predictions,
        "limitations": [
            "Scores are uncalibrated model scores, not probabilities or composition estimates.",
            "A negative decision predicts the operational below-threshold class, not absence.",
            "H and He remain unknown; this instrument profile does not support them.",
            "Instrument identity and vacuum-nm wavelength convention are caller assertions.",
            "One CCS file is one measurement; retained shots are averaged, not independent tests.",
        ],
    }


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact", type=Path, required=True, help="Saved instrument profile")
    parser.add_argument("--input", type=Path, required=True, help="New ChemCam CCS CSV")
    parser.add_argument("--instrument-id", required=True, help="Must match the saved profile")
    parser.add_argument("--output", type=Path, required=True, help="New JSON result path")
    args = parser.parse_args(argv)
    try:
        if args.output.exists() or args.output.is_symlink():
            raise ValueError("Output already exists; choose a new JSON result path")
        result = predict_measurement(args.artifact, args.input, args.instrument_id)
        serialized = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x", encoding="utf-8") as stream:
            stream.write(serialized)
    except (OSError, ValueError) as exc:
        parser.exit(2, f"Instrument prediction failed: {exc}\n")
    print(json.dumps({"output": str(args.output), "profile_identity": result["profile_identity"]}))


if __name__ == "__main__":
    main()
