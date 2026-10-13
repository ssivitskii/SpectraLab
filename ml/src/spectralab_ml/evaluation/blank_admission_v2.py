"""Metadata-only admission for a new, independent Fe/Mg final campaign.

``admit_final_campaign(manifest, previously_seen_ids)`` never fits a model or reads
spectral intensities. Schema 1 manifests contain ``campaign_id``,
``selection_frozen_before_predictions=True`` and a nonempty ``samples`` list.
Each sample requires measurement_id, physical_sample_id, family_id, session_id,
instrument_id, input_sha256 and:

* raw_spectrum: uri, format (csv/tsv/hdf5/npz), numeric_spectrum_verified=True;
* license: id, confirmed=True, source, source_sha256;
* axis: units=nm, medium=vacuum/air, source, source_sha256. Air additionally
  requires conversion={target:vacuum, applied:True, method, source,
  source_sha256, output_sha256}; the original input checksum stays unchanged;
* labels for both Fe and Mg. Unknown has basis=unknown and a reason. Known
  evidence has basis=independent_composition or independent_upper_bound,
  independent=True, evidence_id (an assay-row/material identifier, NOT a whole
  publication), physical_sample_id, source, source_sha256, units, oxide, and
  value or upper_bound. Upper bounds also require method and certified=True;
* control_type: sample, method_blank, matrix_blank, dark, or no_laser.

The fixed operational estimand uses oxide_wt_percent, FeOT for Fe and MgO for
Mg: positive >=1, negative 0<value<=0.1, otherwise unknown. A separately
certified positive upper bound <=0.1 can establish operational negative. Zero
is never an absence assertion. At least one method/matrix blank family needs
such bounds for BOTH elements. This is not a claim of strict chemical absence.

previously_seen_ids explicitly lists measurement_ids, physical_sample_ids,
family_ids, session_ids, input_sha256s and assay_ids across ALL earlier work,
including old final tests. Common source URLs/table hashes may be shared;
assay-row identifiers and material identities may not. The caller is responsible
for alias resolution and registry completeness. Admission validates evidence
structure and its binding, not the truth of an attestation or its chronology.
Output hashes bind the supplied manifest and seen registry for later checking.
Unknown/malformed metadata returns blockers, never admission by omission.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping
from numbers import Real
from typing import Any

ELEMENTS = {"Fe": "FeOT", "Mg": "MgO"}
SEEN_FIELDS = {
    "measurement_ids": "measurement_id",
    "physical_sample_ids": "physical_sample_id",
    "family_ids": "family_id",
    "session_ids": "session_id",
    "input_sha256s": "input_sha256",
}


def _text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip()) and value == value.strip()


def _sha(value: Any) -> bool:
    return isinstance(value, str) and bool(re.fullmatch(r"[a-f0-9]{64}", value))


def _choice(value: Any, choices: set[str]) -> bool:
    return isinstance(value, str) and value in choices


def _number(value: Any) -> bool:
    return isinstance(value, Real) and not isinstance(value, bool) and math.isfinite(value)


def _digest(value: Any) -> str | None:
    try:
        data = json.dumps(value, sort_keys=True, allow_nan=False, separators=(",", ":"))
    except (TypeError, ValueError):
        return None
    return hashlib.sha256(data.encode()).hexdigest()


def admit_final_campaign(
    manifest: Mapping[str, Any], previously_seen_ids: Mapping[str, Any]
) -> dict[str, Any]:
    """Collect admission blockers; a blocked result carries no final metrics."""
    blockers: list[dict[str, str]] = []

    def check(valid: bool, code: str, path: str, message: str) -> bool:
        if not valid:
            blockers.append({"code": code, "path": path, "message": message})
        return valid

    def source(value: Mapping, path: str) -> bool:
        first = check(
            _text(value.get("source")),
            "evidence_source_missing",
            path,
            "An attributable evidence source is required.",
        )
        second = check(
            _sha(value.get("source_sha256")),
            "evidence_hash_missing",
            path,
            "A SHA-256 of the evidence source is required.",
        )
        return first and second

    manifest_hash, registry_hash = _digest(manifest), _digest(previously_seen_ids)
    check(manifest_hash is not None, "invalid_manifest_json", "$", "Finite JSON is required.")
    check(
        registry_hash is not None,
        "invalid_registry_json",
        "previously_seen_ids",
        "The seen registry must contain finite JSON values.",
    )
    manifest = manifest if isinstance(manifest, Mapping) else {}
    registry = previously_seen_ids if isinstance(previously_seen_ids, Mapping) else {}
    check(
        type(manifest.get("schema_version")) is int and manifest["schema_version"] == 1,
        "unsupported_schema",
        "schema_version",
        "Schema version 1 is required.",
    )
    check(
        _text(manifest.get("campaign_id")),
        "campaign_id_missing",
        "campaign_id",
        "A named final campaign is required.",
    )
    check(
        manifest.get("selection_frozen_before_predictions") is True,
        "selection_not_frozen",
        "selection_frozen_before_predictions",
        "Freeze selection before final predictions.",
    )
    check(
        manifest.get("estimand") == "operational_above_threshold",
        "invalid_estimand",
        "estimand",
        "The bounds define an operational threshold, not strict absence.",
    )
    seen: dict[str, set[str]] = {}
    for field in (*SEEN_FIELDS, "assay_ids"):
        values = registry.get(field)
        valid = isinstance(values, list) and all(
            _sha(v) if field == "input_sha256s" else _text(v) for v in values
        )
        check(
            valid,
            "seen_registry_incomplete",
            f"previously_seen_ids.{field}",
            "Explicit identity lists covering all earlier development and final tests required.",
        )
        seen[field] = set(values) if valid else set()

    samples = manifest.get("samples")
    check(
        isinstance(samples, list) and bool(samples),
        "no_final_samples",
        "samples",
        "No independently admissible numerical final spectra have been selected.",
    )
    samples = samples if isinstance(samples, list) else []
    measurements: set[str] = set()
    inputs: set[str] = set()
    physical_states: dict[str, tuple] = {}
    assay_materials: dict[str, str] = {}
    supports = {
        element: {state: set() for state in ("positive", "negative", "unknown")}
        for element in ELEMENTS
    }
    blank_families: set[str] = set()

    for i, item in enumerate(samples):
        path = f"samples[{i}]"
        before = len(blockers)
        if not check(
            isinstance(item, Mapping), "invalid_sample", path, "Sample must be an object."
        ):
            continue
        for field in (*SEEN_FIELDS.values(), "instrument_id"):
            valid = _sha(item.get(field)) if field == "input_sha256" else _text(item.get(field))
            check(
                valid,
                "identity_missing",
                f"{path}.{field}",
                "A valid explicit identity is required.",
            )
        for registry_field, field in SEEN_FIELDS.items():
            value = item.get(field)
            check(
                not isinstance(value, str) or value not in seen[registry_field],
                "previously_seen_identity",
                f"{path}.{field}",
                "Final data overlap earlier work.",
            )
        for field, collected in (("measurement_id", measurements), ("input_sha256", inputs)):
            value = item.get(field)
            if isinstance(value, str):
                check(
                    value not in collected,
                    "duplicate_spectrum",
                    f"{path}.{field}",
                    "A final numerical spectrum may only appear once.",
                )
                collected.add(value)

        raw = item.get("raw_spectrum")
        raw = raw if isinstance(raw, Mapping) else {}
        check(
            _text(raw.get("uri"))
            and _choice(raw.get("format"), {"csv", "tsv", "hdf5", "npz"})
            and raw.get("numeric_spectrum_verified") is True,
            "numeric_raw_unverified",
            f"{path}.raw_spectrum",
            "A verified numerical raw spectrum, URI and format are required.",
        )
        rights = item.get("license")
        rights = rights if isinstance(rights, Mapping) else {}
        check(
            rights.get("confirmed") is True and _text(rights.get("id")),
            "data_license_unconfirmed",
            f"{path}.license",
            "Confirm rights for the numerical dataset.",
        )
        source(rights, f"{path}.license")
        axis = item.get("axis")
        axis = axis if isinstance(axis, Mapping) else {}
        check(
            axis.get("units") == "nm" and _choice(axis.get("medium"), {"air", "vacuum"}),
            "wavelength_medium_unconfirmed",
            f"{path}.axis",
            "An explicit nm air/vacuum wavelength convention is required.",
        )
        source(axis, f"{path}.axis")
        if axis.get("medium") == "air":
            conversion = axis.get("conversion")
            conversion = conversion if isinstance(conversion, Mapping) else {}
            check(
                conversion.get("target") == "vacuum"
                and conversion.get("applied") is True
                and _text(conversion.get("method"))
                and _sha(conversion.get("output_sha256")),
                "air_conversion_unverified",
                f"{path}.axis.conversion",
                "Document applied air-to-vacuum conversion and pin its output checksum.",
            )
            source(conversion, f"{path}.axis.conversion")

        control = item.get("control_type")
        check(
            _choice(control, {"sample", "method_blank", "matrix_blank", "dark", "no_laser"}),
            "control_type_missing",
            f"{path}.control_type",
            "State the actual control preparation.",
        )
        check(
            not _choice(control, {"dark", "no_laser"}),
            "not_chemical_measurement",
            f"{path}.control_type",
            "Dark/no-laser records cannot replace chemical final controls.",
        )
        labels = item.get("labels")
        labels = labels if isinstance(labels, Mapping) else {}
        bounds: set[str] = set()
        states = []
        for element, oxide in ELEMENTS.items():
            label_path = f"{path}.labels.{element}"
            label = labels.get(element)
            label = label if isinstance(label, Mapping) else {}
            state, basis = label.get("state"), label.get("basis")
            states.append(state if isinstance(state, str) else None)
            check(
                _choice(state, {"positive", "negative", "unknown"}),
                "label_missing",
                label_path,
                "Every element requires a positive, negative or unknown label.",
            )
            if state == "unknown" and basis == "unknown":
                check(
                    _text(label.get("reason")),
                    "unknown_reason_missing",
                    label_path,
                    "Explain unknown chemistry; never silently replace it by zero.",
                )
                continue
            label_before = len(blockers)
            source(label, label_path)
            check(
                label.get("independent") is True
                and _text(label.get("evidence_id"))
                and label.get("physical_sample_id") == item.get("physical_sample_id")
                and _text(label.get("physical_sample_id")),
                "independent_assay_unverified",
                label_path,
                "Bind an independent assay identifier to this physical material.",
            )
            assay_id = label.get("evidence_id")
            check(
                not isinstance(assay_id, str) or assay_id not in seen["assay_ids"],
                "previously_seen_assay",
                label_path,
                "This material/assay identifier was already seen.",
            )
            if _text(assay_id) and _text(item.get("physical_sample_id")):
                check(
                    assay_id not in assay_materials
                    or assay_materials[assay_id] == item["physical_sample_id"],
                    "assay_material_alias",
                    label_path,
                    "One material/assay identifier cannot identify two independent samples.",
                )
                assay_materials[assay_id] = item["physical_sample_id"]
            check(
                label.get("source_sha256") != item.get("input_sha256"),
                "spectral_evidence_not_independent",
                label_path,
                "The input spectrum itself cannot be the independent composition evidence.",
            )
            check(
                label.get("units") == "oxide_wt_percent" and label.get("oxide") == oxide,
                "composition_units_mismatch",
                label_path,
                f"Expected oxide_wt_percent of {oxide}; no silent elemental/oxide conversion.",
            )
            if basis == "independent_composition":
                value = label.get("value")
                valid = _number(value) and 0 <= value <= 100
                check(
                    valid,
                    "invalid_composition",
                    label_path,
                    "Finite composition between 0 and 100 oxide wt% required.",
                )
                expected = (
                    ("positive" if value >= 1 else "negative" if 0 < value <= 0.1 else "unknown")
                    if valid
                    else None
                )
                check(
                    valid and state == expected,
                    "composition_state_mismatch",
                    label_path,
                    "Policy: >=1 positive; 0<value<=0.1 negative; zero/intermediate unknown.",
                )
            elif basis == "independent_upper_bound":
                bound = label.get("upper_bound")
                valid = _number(bound) and 0 < bound <= 100
                check(
                    valid and label.get("certified") is True and _text(label.get("method")),
                    "upper_bound_unverified",
                    label_path,
                    "A certified independent bound in (0,100] oxide wt% and assay method required.",
                )
                expected = "negative" if valid and bound <= 0.1 else "unknown"
                check(
                    state == expected,
                    "upper_bound_state_mismatch",
                    label_path,
                    "Only a certified upper bound <=0.1 supplies operational negative evidence.",
                )
                if len(blockers) == label_before and state == "negative":
                    bounds.add(element)
            else:
                check(
                    False,
                    "unsupported_label_basis",
                    label_path,
                    "Nominal zero, LIBS non-detection and dark signals are not independent assays.",
                )
        physical_id = item.get("physical_sample_id")
        if _text(physical_id):
            check(
                physical_id not in physical_states or physical_states[physical_id] == tuple(states),
                "inconsistent_replicate_labels",
                path,
                "One physical material cannot have conflicting labels.",
            )
            physical_states[physical_id] = tuple(states)
        if len(blockers) == before:
            for element, state in zip(ELEMENTS, states, strict=True):
                supports[element][state].add(physical_id)
            if _choice(control, {"method_blank", "matrix_blank"}) and bounds == set(ELEMENTS):
                blank_families.add(item["family_id"])

    check(
        bool(blank_families),
        "certified_blank_family_missing",
        "samples",
        "Need >=1 independent method/matrix blank family with certified FeOT/MgO bounds <=0.1 wt%.",
    )
    return {
        "schema_version": 1,
        "status": "blocked" if blockers else "admitted",
        "campaign_id": manifest.get("campaign_id") if _text(manifest.get("campaign_id")) else None,
        "estimand": "operational_above_threshold",
        "decision_policy": {
            element: {
                "oxide": oxide,
                "units": "oxide_wt_percent",
                "positive_min": 1.0,
                "negative_max": 0.1,
                "zero_policy": "unknown",
            }
            for element, oxide in ELEMENTS.items()
        },
        "manifest_sha256": manifest_hash,
        "previously_seen_sha256": registry_hash,
        "blockers": blockers,
        "certified_blank_family_ids": sorted(blank_families),
        "verified_physical_support": {
            e: {state: len(ids) for state, ids in counts.items()} for e, counts in supports.items()
        },
        "metrics_status": "pending_final_evaluation"
        if not blockers
        else "blocked_no_final_evaluation",
        "final_metrics": None,
    }
