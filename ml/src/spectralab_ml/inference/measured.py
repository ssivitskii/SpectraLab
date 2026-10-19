"""Apply numeric measured research profiles with explicit instrument/axis admission."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np

from spectralab_ml.data.chemcam_measurements import read_chemcam_ccs
from spectralab_ml.data.measured_spectra import (
    read_bounded,
    read_measured_csv,
    read_measurement_metadata,
    validate_measurement_metadata,
)
from spectralab_ml.data.wavelengths import CONVERSION_SOURCE, air_to_vacuum
from spectralab_ml.evaluation.real_validation import interpolate_observed
from spectralab_ml.models.line_adapter_v3 import BANDS, CLASSES, GRID, LineAdapterV3


def load_measured_profile(path: Path):
    """Read one bounded numeric buffer and dispatch only supported measured models."""
    from spectralab_ml.models.line_adapter_v3 import _object, digest

    raw_bytes = read_bounded(Path(path), 5_000_000)
    try:
        raw = json.loads(raw_bytes, object_pairs_hook=_object)
        if not isinstance(raw, dict):
            raise ValueError("Measured profile must be a JSON object")
        identity = raw.pop("identity", None)
        if digest(raw) != identity:
            raise ValueError("Measured profile integrity mismatch")
    except (UnicodeError, RecursionError) as exc:
        raise ValueError("Bounded finite numeric profile JSON required") from exc
    if type(raw.get("schema_version")) is not int:
        raise ValueError("Explicit measured profile schema required")
    if raw["schema_version"] == 3 and raw.get("artifact_type") == "line_adapter_v3":
        if len(raw_bytes) > 2_000_000:
            raise ValueError("Line-v3 profile exceeds its 2 MB limit")
        model = LineAdapterV3._from_payload(raw)
    elif raw["schema_version"] == 4 and raw.get("artifact_type") == "calcium_adapter_v4":
        from spectralab_ml.models.calcium_adapter_v4 import CalciumAdapterV4

        model = CalciumAdapterV4.from_payload(raw)
    elif raw["schema_version"] == 4 and raw.get("artifact_type") == "calcium_ensemble_v4":
        from spectralab_ml.models.calcium_ensemble_v4 import CalciumEnsembleV4

        model = CalciumEnsembleV4.from_payload(raw)
    else:
        raise ValueError("Supported numeric line-v3 or calcium-v4 profile required")
    # A metadata view exposes actual thresholds for both artifact schemas.
    metadata = {**raw, "identity": identity, "thresholds": list(model.thresholds)}
    return model, metadata, hashlib.sha256(raw_bytes).hexdigest()


def predict_line_arrays(
    model: LineAdapterV3, profile: dict, wave: np.ndarray, intensity: np.ndarray, metadata: dict
) -> dict:
    """No labels enter this interface; missing bands and instrument mismatches fail."""
    validate_measurement_metadata(metadata)
    if metadata["instrument_id"] != profile["provenance"]["instrument_id"]:
        raise ValueError("Measurement instrument differs from the trained profile")
    conversion = None
    if metadata["axis"]["medium"] == "air":
        wave = air_to_vacuum(wave)
        conversion = {
            "method": "Peck-Reeder 1972 standard air",
            "source": CONVERSION_SOURCE,
            "from": "air",
            "to": "vacuum",
        }
    vector = interpolate_observed(
        np.asarray(wave, float), np.asarray(intensity, float), GRID, BANDS, max_gap_nm=0.5
    )
    with np.errstate(over="raise", divide="raise", invalid="raise"):
        try:
            result = model.predict_spectra(vector[None, :], GRID)
        except FloatingPointError as exc:
            raise ValueError("Measured inference exceeded finite numeric range") from exc
    predictions = []
    for k, element in enumerate(CLASSES):
        probability = float(result["probabilities"][0, k])
        evidence = float(result["line_snr"][0, k])
        if not np.isfinite([probability, evidence]).all() or not 0 <= probability <= 1:
            raise ValueError("Invalid finite model result")
        accepted = bool(result["accepted"][0, k])
        predictions.append(
            {
                "element": element,
                "status": "evaluated" if accepted else "unknown",
                "score": probability,
                "threshold": float(model.thresholds[k]),
                "detected": bool(result["detections"][0, k]) if accepted else None,
                "accepted": accepted,
                "line_snr": evidence,
                "reason": None if accepted else "below_frozen_evidence_or_score_margin",
            }
        )
    predictions.extend(
        {
            "element": e,
            "status": "unknown",
            "score": None,
            "threshold": None,
            "detected": None,
            "accepted": False,
            "line_snr": None,
            "reason": "unsupported_by_profile",
        }
        for e in ("H", "He")
    )
    return {
        "schema_version": 3,
        "scope": "research_application_not_final_validation",
        "measurement_id": metadata["measurement_id"],
        "instrument_id": metadata["instrument_id"],
        "profile_identity": profile["identity"],
        "profile_artifact_type": profile["artifact_type"],
        "score_kind": model.score_kind,
        "calibrated": True,
        "calibration_scope": (
            "adaptive_grouped_development_only"
            if profile["schema_version"] == 4
            else "grouped_development_only"
        ),
        "qualification": profile["qualification"],
        "final_test_status": "not_performed",
        "predictions": predictions,
        "wavelength_conversion": conversion,
        "input_axis": metadata["axis"],
        "model_axis": {"units": "nm", "medium": "vacuum"},
        "bands_nm": BANDS,
    }


def predict_measured_profile(
    profile_path: Path, input_path: Path, metadata_path: Path, *, input_format: str = "csv"
) -> dict:
    model, profile, profile_sha = load_measured_profile(Path(profile_path))
    metadata, metadata_sha = read_measurement_metadata(Path(metadata_path))
    if input_format == "csv":
        wave, intensity, provenance = read_measured_csv(
            Path(input_path), expected_sha256=metadata.get("input_sha256")
        )
    elif input_format == "chemcam-ccs":
        checksum = hashlib.sha256(read_bounded(Path(input_path))).hexdigest()
        if metadata.get("input_sha256", checksum) != checksum:
            raise ValueError("Measurement input checksum mismatch")
        wave, intensity, aggregation = read_chemcam_ccs(Path(input_path), checksum)
        provenance = {
            "input_sha256": checksum,
            "input_points": len(wave),
            "aggregation": aggregation,
        }
    else:
        raise ValueError("Supported input formats: csv, chemcam-ccs")
    result = predict_line_arrays(model, profile, wave, intensity, metadata)
    result["profile_sha256"] = profile_sha
    result["input"] = {**provenance, "format": input_format, "metadata_sha256": metadata_sha}
    return result


def bind_final_line_profile(contract: dict, data_root: Path):
    """Bind a frozen final contract to actual numeric line-v3 model semantics.

    This adapter evaluates binary scores/thresholds only. Selective refusal is
    intentionally not folded into this final estimand. The campaign runner
    supplies verified vacuum arrays, including when original data were air.
    """
    from spectralab_ml.evaluation._final_campaign_io import local_path

    files = contract.get("artifact_files")
    if not isinstance(files, list) or len(files) != 1:
        raise ValueError("The measured CLI requires exactly one numeric JSON profile artifact")
    item = files[0]
    path = local_path(Path(data_root), item["path"])
    model, profile, artifact_sha = load_measured_profile(path)
    expected_thresholds = dict(zip(CLASSES, profile["thresholds"], strict=True))
    if (
        artifact_sha != item["sha256"]
        or contract.get("identity") != profile["identity"]
        or contract.get("instrument_id") != profile["provenance"]["instrument_id"]
        or contract.get("class_order") != CLASSES
        or contract.get("thresholds") != expected_thresholds
        or contract.get("units") != "nm"
        or contract.get("medium") != "vacuum"
        or contract.get("require_positive_signal") is not True
    ):
        raise ValueError("Frozen profile contract differs from the actual line-v3/v4 artifact")
    intervals = contract.get("required_intervals_nm", [])
    if not all(any(lower <= a and upper >= b for lower, upper in intervals) for a, b in BANDS):
        raise ValueError("Frozen profile omits line-v3 normalization bands or feature windows")
    step = contract.get("max_sampling_step_nm")
    if isinstance(step, bool) or not isinstance(step, (float, int)) or not 0 < step <= 0.5:
        raise ValueError("Frozen native sampling must be no coarser than 0.5 nm")

    def predictor(metadata, wave, intensity):
        if (
            metadata["instrument_id"] != contract["instrument_id"]
            or metadata["axis"]["medium"] != "vacuum"
        ):
            raise ValueError(
                "Final runner must supply the matching instrument and effective vacuum axis"
            )
        vector = interpolate_observed(wave, intensity, GRID, BANDS, max_gap_nm=step)
        probabilities = model.predict_spectra(vector[None, :], GRID)["probabilities"][0]
        return dict(zip(CLASSES, map(float, probabilities), strict=True))

    predictor.requires_calcium_implementation = profile["schema_version"] == 4
    predictor.requires_calcium_ensemble = profile["artifact_type"] == "calcium_ensemble_v4"
    return predictor, {
        "profile_identity": profile["identity"],
        "artifact_sha256": {item["path"]: artifact_sha},
    }
