"""Apply a numeric development-only v2 line profile to one ChemCam CCS file.

The caller asserts the instrument identity and vacuum-nm convention. This command
does not fit, tune, register a model, or establish independent blank validation.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
from spectralab_ml.data.chemcam_measurements import MAX_CCS_BYTES, read_chemcam_ccs
from spectralab_ml.evaluation.real_validation import interpolate_observed
from spectralab_ml.models.line_adapter_v2 import (
    BANDS,
    CLASSES,
    LineAdapterV2,
    extract_line_features,
)

GRID_NM = np.linspace(240.0, 900.0, 6601)
MAX_GAP_NM = 0.5
MAX_ARTIFACT_BYTES = 2_000_000
QUALIFICATION = "development_only_no_independent_blank_validation"
FEATURES = [
    "log1p(1000*positive_peak_excess/band_positive_sum)",
    "log1p(positive_peak_excess/(positive_sideband_mean+floor))",
]


def _number(value: object) -> bool:
    return type(value) in (int, float) and np.isfinite(value)


def _unique_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate artifact key: {key}")
        result[key] = value
    return result


def _invalid_constant(value: str) -> None:
    raise ValueError(f"Nonfinite JSON constant: {value}")


def validate_plan(plan: object) -> None:
    """Bound every feature window before invoking the feature extraction loops."""
    if not isinstance(plan, dict):
        raise ValueError("A v2 line plan is required")
    if type(plan.get("schema_version")) is not int or plan["schema_version"] != 2:
        raise ValueError("Unsupported line plan schema")
    if plan.get("wavelength_medium") != "vacuum":
        raise ValueError("The v2 line plan requires vacuum wavelengths")
    if plan.get("class_order") != CLASSES:
        raise ValueError("The v2 line plan requires Na, Mg, Ca, Fe in the fixed class order")
    bands = plan.get("bands")
    if (
        not isinstance(bands, list)
        or len(bands) != len(BANDS)
        or any(
            not isinstance(band, list)
            or len(band) != 2
            or not all(_number(value) for value in band)
            for band in bands
        )
        or bands != BANDS
    ):
        raise ValueError("The v2 line plan requires the exact supported BANDS")
    for field, expected in (
        ("core_half_width_nm", 0.2),
        ("sideband_inner_nm", 0.5),
        ("sideband_outer_nm", 1.0),
    ):
        if not _number(plan.get(field)) or plan[field] != expected:
            raise ValueError(f"Unsupported v2 line window width: {field}")
    if (
        plan.get("baseline") != "sideband lower quartile"
        or plan.get("noise_floor") != "1e-6 band positive p95"
        or plan.get("features") != FEATURES
    ):
        raise ValueError("Unsupported v2 feature transform")
    lines = plan.get("lines_nm")
    if not isinstance(lines, dict) or set(lines) != set(CLASSES):
        raise ValueError("Line centers must specify exactly the four supported classes")
    for element in CLASSES:
        centers = lines[element]
        if (
            not isinstance(centers, list)
            or not 1 <= len(centers) <= 32
            or not all(_number(center) for center in centers)
        ):
            raise ValueError("Each class requires 1..32 finite numeric line centers")
        for center in centers:
            if not any(a + 1.0 <= center <= b - 1.0 for a, b in BANDS):
                raise ValueError(
                    "Every line core and both sidebands must lie within one BANDS interval"
                )


def load_profile(path: Path, instrument_id: str) -> tuple[LineAdapterV2, dict]:
    """Validate bounded JSON semantics, then use the existing numeric v2 loader."""
    if not isinstance(instrument_id, str) or not instrument_id.strip():
        raise ValueError("An explicit nonempty instrument_id is required")
    path = Path(path)
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_ARTIFACT_BYTES:
        raise ValueError("Artifact must be a regular JSON file of at most 2 MB")
    with path.open("rb") as stream:
        encoded = stream.read(MAX_ARTIFACT_BYTES + 1)
    if len(encoded) > MAX_ARTIFACT_BYTES:
        raise ValueError("Artifact exceeds the 2 MB size limit")
    try:
        raw = json.loads(
            encoded, object_pairs_hook=_unique_object, parse_constant=_invalid_constant
        )
        if not isinstance(raw, dict):
            raise ValueError("Artifact must be a JSON object")
        validate_plan(raw.get("plan"))
        if raw.get("score_kind") != LineAdapterV2.score_kind:
            raise ValueError("Unsupported v2 score kind")
        if raw.get("qualification") != QUALIFICATION:
            raise ValueError("Unsupported v2 qualification")
        if not _number(raw.get("C")) or raw["C"] <= 0:
            raise ValueError("A positive finite model C is required")
        provenance = raw.get("provenance")
        if not isinstance(provenance, dict) or provenance.get("instrument_id") != instrument_id:
            raise ValueError("Instrument identity does not match the saved v2 provenance")
        if provenance.get("estimand") != "operational_above_threshold":
            raise ValueError("Unsupported operational estimand")
        policy = provenance.get("oxide_policy")
        if not isinstance(policy, dict) or set(policy) != set(CLASSES):
            raise ValueError("The saved operational oxide policy must specify all four classes")
        for element, oxide in zip(CLASSES, ("Na2O", "MgO", "CaO", "FeOT"), strict=True):
            item = policy[element]
            if not isinstance(item, dict) or any(
                item.get(key) != value
                for key, value in {
                    "oxide": oxide,
                    "units": "oxide_wt_percent",
                    "zero_policy": "unknown",
                }.items()
            ):
                raise ValueError("Unsupported operational oxide policy")
            for key, expected in (
                ("negative_max", 0.1),
                ("positive_min", 1.0),
                ("nominal_decision_level", 0.5),
            ):
                if not _number(item.get(key)) or item[key] != expected:
                    raise ValueError("Unsupported operational oxide decision levels")
        for element in CLASSES:
            state = raw["states"][element]
            for key in ("mean", "scale", "coef"):
                values = state[key]
                if not isinstance(values, list) or not all(_number(v) for v in values):
                    raise ValueError("Model state must contain numeric arrays")
            if not _number(state["intercept"]):
                raise ValueError("Model intercept must be finite numeric")
        if not isinstance(raw["thresholds"], list) or not all(
            _number(v) for v in raw["thresholds"]
        ):
            raise ValueError("Thresholds must be finite numeric")
        model, metadata = LineAdapterV2.load(path)
        if metadata != raw:
            raise ValueError("Artifact changed during validation")
    except (KeyError, TypeError, OverflowError, RecursionError, UnicodeError) as exc:
        raise ValueError("Malformed v2 numeric artifact") from exc
    return model, metadata


def predict_measurement(artifact: Path, input_path: Path, instrument_id: str) -> dict:
    model, metadata = load_profile(artifact, instrument_id)
    input_path = Path(input_path)
    if not input_path.is_file() or input_path.stat().st_size > MAX_CCS_BYTES:
        raise ValueError("CCS input must be an existing file of at most 16 MB")
    with input_path.open("rb") as stream:
        raw = stream.read(MAX_CCS_BYTES + 1)
    if len(raw) > MAX_CCS_BYTES:
        raise ValueError("CCS file exceeds the 16 MB size limit")
    input_sha256 = hashlib.sha256(raw).hexdigest()
    del raw
    wavelengths, intensity, aggregation = read_chemcam_ccs(input_path, input_sha256)
    spectrum = interpolate_observed(wavelengths, intensity, GRID_NM, BANDS, max_gap_nm=MAX_GAP_NM)
    # Reject every signal-free detector band before the per-element line loops.
    if any(not np.any(spectrum[(a <= GRID_NM) & (b >= GRID_NM)] > 0) for a, b in BANDS):
        raise ValueError("No positive emission in an observed band; cannot classify")
    with np.errstate(over="raise", divide="raise", invalid="raise"):
        try:
            features = extract_line_features(spectrum[np.newaxis, :], GRID_NM, metadata["plan"])
            scores = model.scores(features)[0]
        except FloatingPointError as exc:
            raise ValueError("Line inference exceeded finite numeric range") from exc
    if scores.shape != (4,) or not np.isfinite(scores).all():
        raise ValueError("Invalid model scores")
    predictions = [
        {
            "element": element,
            "status": "evaluated",
            "score": float(score),
            "threshold": threshold,
            "detected": bool(score >= threshold),
        }
        for element, score, threshold in zip(CLASSES, scores, metadata["thresholds"], strict=True)
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
        for element in ("H", "He")
    )
    return {
        "schema_version": 2,
        "artifact_type": "line_adapter_v2_prediction",
        "instrument_id": instrument_id,
        "profile_identity": metadata["identity"],
        "input_sha256": input_sha256,
        "input_file": input_path.name,
        "score_kind": model.score_kind,
        "calibrated": False,
        "estimand": metadata["provenance"]["estimand"],
        "oxide_policy": metadata["provenance"]["oxide_policy"],
        "qualification": QUALIFICATION,
        "final_test_status": "not_performed",
        "class_order": CLASSES,
        "aggregation": aggregation,
        "preprocessing": {
            "wavelength_medium": "vacuum",
            "wavelength_units": "nm",
            "observed_intervals": BANDS,
            "grid_start_nm": 240.0,
            "grid_end_nm": 900.0,
            "grid_points": len(GRID_NM),
            "max_gap_nm": MAX_GAP_NM,
            "input_points": len(wavelengths),
            "line_plan": metadata["plan"],
        },
        "predictions": predictions,
        "limitations": [
            "Scores are uncalibrated, not probabilities or composition estimates.",
            "A negative decision predicts the operational below-threshold class, not absence.",
            "H and He remain unknown; this profile does not support them.",
            "Instrument identity and vacuum-nm wavelengths are caller assertions.",
            "This profile has no independent final validation with documented blank controls.",
            "Retained shots are averaged within one measurement, not independent test samples.",
        ],
    }


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--artifact", type=Path, required=True, help="Saved numeric v2 profile JSON"
    )
    parser.add_argument("--input", type=Path, required=True, help="ChemCam CCS CSV measurement")
    parser.add_argument("--instrument-id", required=True, help="Must match profile provenance")
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
        parser.exit(2, f"V2 line prediction failed: {exc}\n")
    print(json.dumps({"output": str(args.output), "profile_identity": result["profile_identity"]}))


if __name__ == "__main__":
    main()
