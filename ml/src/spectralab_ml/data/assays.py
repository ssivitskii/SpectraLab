"""Explicit chemistry conversions for intake, without certifying an analytical blank.

Values describe mass per mass of the original sample. Oxide equivalents may
exceed 100 wt% for an elemental metal. An FeO/Fe2O3 column is ambiguous unless
the provider explicitly says it represents total iron; never guess from size.
The fixed thresholds follow the project's operational estimand, not absence.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from numbers import Real
from typing import Any

# Same atomic masses and conversion basis as the frozen v3 source-search audit.
ATOMIC_MASS = {"Fe": 55.845, "Mg": 24.305, "O": 15.999}
UNIT_TO_WT_PERCENT = {
    "wt_percent": 1.0,
    "mass_fraction": 100.0,
    "mg_per_kg": 0.0001,
    "ug_per_g": 0.0001,
    "ppm_mass": 0.0001,
}
OXIDES = {"Fe": "FeOT", "Mg": "MgO"}
NEGATIVE_MAX = 0.1
POSITIVE_MIN = 1.0
QUALIFIERS = {"measured", "certified", "informational", "estimated", "not_reported"}
FIELDS = {
    "element",
    "reported_species",
    "units",
    "value",
    "upper_bound",
    "total_element",
    "uncertainty",
    "uncertainty_type",
    "coverage_factor",
    "qualifier",
    "sample_id",
    "source",
    "source_sha256",
    "assay_id",
}


def _number(value: Any, name: str) -> float:
    if not isinstance(value, Real) or isinstance(value, bool) or not math.isfinite(value):
        raise ValueError(f"{name} must be a finite number, not a string or boolean")
    return float(value)


def oxide_factor(element: str, reported_species: str, *, total_element: bool = False) -> float:
    """Convert a documented total elemental/species mass to its oxide equivalent."""
    if not isinstance(total_element, bool):
        raise ValueError("total_element must be an explicit boolean")
    if element not in OXIDES:
        raise ValueError("Only Fe and Mg operational assays are supported")
    if element == "Mg":
        if reported_species == "MgO":
            return 1.0
        if reported_species == "Mg":
            return (ATOMIC_MASS["Mg"] + ATOMIC_MASS["O"]) / ATOMIC_MASS["Mg"]
        raise ValueError("Mg requires reported_species Mg or MgO")
    if reported_species == "FeOT":
        return 1.0
    if reported_species in {"Fe", "FeO", "Fe2O3"} and not total_element:
        raise ValueError("Fe/FeO/Fe2O3 requires explicit total_element=true; no basis inference")
    if reported_species in {"FeO", "Fe"}:
        return (
            1.0
            if reported_species == "FeO"
            else ((ATOMIC_MASS["Fe"] + ATOMIC_MASS["O"]) / ATOMIC_MASS["Fe"])
        )
    if reported_species in {"Fe2O3", "Fe2O3T"}:
        return (
            2
            * (ATOMIC_MASS["Fe"] + ATOMIC_MASS["O"])
            / (2 * ATOMIC_MASS["Fe"] + 3 * ATOMIC_MASS["O"])
        )
    raise ValueError("Fe requires Fe, FeO, FeOT, Fe2O3 or Fe2O3T")


def _state(lower: float, upper: float) -> tuple[str, str]:
    if lower >= POSITIVE_MIN:
        return "positive", "entire_reported_interval_at_or_above_positive_threshold"
    if 0 < lower <= upper <= NEGATIVE_MAX:
        return "negative", "entire_reported_interval_in_low_content_region"
    return "unknown", "zero_intermediate_or_interval_crosses_operational_regions"


def normalize_assay(assay: Mapping[str, Any]) -> dict:
    """Normalize one explicitly specified assay; never promote it to a blank bound.

    ``uncertainty`` is an absolute expanded uncertainty in the reported units;
    its coverage factor must be specified. This function does not infer a
    confidence level from k, or turn mean +/- uncertainty into certification.
    An upper bound can be classified conditionally; independent certification,
    preparation and provenance are checked by the separate campaign admission.
    """
    if not isinstance(assay, Mapping) or set(assay) - FIELDS:
        raise ValueError("Assay must be an object with only documented fields")
    element, species, units = (assay.get(k) for k in ("element", "reported_species", "units"))
    if not isinstance(element, str) or not isinstance(species, str):
        raise ValueError("Explicit element and reported_species strings required")
    if not isinstance(units, str) or units not in UNIT_TO_WT_PERCENT:
        raise ValueError("Explicit mass-based units required; ambiguous ppm/percent not supported")
    factor = oxide_factor(element, species, total_element=assay.get("total_element", False))
    qualifier = assay.get("qualifier", "measured")
    if not isinstance(qualifier, str) or qualifier not in QUALIFIERS:
        raise ValueError("Unsupported assay qualifier")
    available = [k for k in ("value", "upper_bound") if assay.get(k) is not None]
    if len(available) != 1 and not (not available and qualifier == "not_reported"):
        raise ValueError("Exactly one value or upper_bound is required")
    unit_scale = UNIT_TO_WT_PERCENT[units]
    conversion = unit_scale * factor
    normalized: float | None = None
    interval = None
    state, reason = "unknown", "not_reported"
    uncertainty = assay.get("uncertainty")
    if not available and any(
        assay.get(k) is not None for k in ("uncertainty", "uncertainty_type", "coverage_factor")
    ):
        raise ValueError("Uncertainty requires a measured point estimate")
    if available:
        kind = available[0]
        raw = _number(assay[kind], kind)
        if not 0 <= raw * unit_scale <= 100:
            raise ValueError("Reported mass content must be in [0,100] wt% before oxide conversion")
        normalized = raw * conversion
        if uncertainty is not None:
            if kind != "value" or assay.get("uncertainty_type") != "expanded_absolute":
                raise ValueError(
                    "Only explicit expanded_absolute uncertainty on a value is supported"
                )
            width = _number(uncertainty, "uncertainty")
            coverage_factor = _number(assay.get("coverage_factor"), "coverage_factor")
            if width < 0 or coverage_factor <= 0:
                raise ValueError("Nonnegative uncertainty and positive coverage_factor required")
            half_width = width * conversion
            interval = [max(0.0, normalized - half_width), normalized + half_width]
            if not all(math.isfinite(v) for v in interval):
                raise ValueError("Converted uncertainty interval must remain finite")
            state, reason = _state(*interval)
        elif any(assay.get(k) is not None for k in ("uncertainty_type", "coverage_factor")):
            raise ValueError("Uncertainty metadata without uncertainty is not meaningful")
        elif kind == "upper_bound":
            state = "negative" if 0 < normalized <= NEGATIVE_MAX else "unknown"
            reason = "conditional_reported_upper_bound_requires_independent_admission"
        else:
            state, reason = _state(normalized, normalized)
        if qualifier in {"informational", "estimated", "not_reported"}:
            state, reason = "unknown", "reported_qualifier_does_not_establish_a_fixed_label"
    # Canonical JSON is also the intake identity; it rejects NaN in unused fields.
    try:
        encoded = json.dumps(dict(assay), sort_keys=True, allow_nan=False, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise ValueError("Assay must contain finite JSON values") from exc
    result = {
        "schema_version": 1,
        "element": element,
        "oxide": OXIDES[element],
        "units": "oxide_wt_percent",
        "value" if available == ["value"] else "upper_bound": normalized,
        "reported_interval_oxide_wt_percent": interval,
        "operational_state": state,
        "reason": reason,
        "conversion_factor_to_oxide_wt_percent": conversion,
        "thresholds": {"negative_max": NEGATIVE_MAX, "positive_min": POSITIVE_MIN},
        "input_sha256": hashlib.sha256(encoded.encode()).hexdigest(),
        "input": dict(assay),
        "qualification": "chemistry_normalization_only_not_final_admission",
        "qualified_blank": False,
        "assumptions": [
            "Mass content refers to the original sample, not surface loading or solution volume.",
            "Zero is unknown; oxide-equivalent values can exceed 100 wt% for pure metals.",
            "A reported interval is not a certified impurity bound "
            "or an inferred confidence level.",
        ],
    }
    return result


def normalize_assay_manifest(manifest: Mapping[str, Any]) -> dict:
    """Batch normalization with per-row errors and no silent coercion or dropping."""
    if (
        not isinstance(manifest, Mapping)
        or set(manifest) != {"schema_version", "assays"}
        or type(manifest.get("schema_version")) is not int
        or manifest["schema_version"] != 1
        or not isinstance(manifest.get("assays"), list)
        or not manifest["assays"]
    ):
        raise ValueError("Schema 1 manifest with a nonempty assays list required")
    rows = []
    for index, assay in enumerate(manifest["assays"]):
        try:
            rows.append({"row_index": index, "status": "normalized", **normalize_assay(assay)})
        except (ValueError, TypeError, OverflowError) as exc:
            rows.append({"row_index": index, "status": "blocked", "reason": str(exc)})
    return {
        "schema_version": 1,
        "status": "blocked" if any(r["status"] == "blocked" for r in rows) else "normalized",
        "scope": "chemistry_normalization_only_not_final_admission",
        "rows": rows,
        "normalized_count": sum(r["status"] == "normalized" for r in rows),
        "blocked_count": sum(r["status"] == "blocked" for r in rows),
        "qualified_blank_count": 0,
    }
