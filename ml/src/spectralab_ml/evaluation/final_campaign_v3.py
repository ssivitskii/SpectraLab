"""Strict final-campaign readiness, immutable freeze and one-shot evaluation.

This is a new protocol; the pinned v1/v2 implementation and reports are untouched.
Schema 3 extends v2's evidence with a frozen instrument/profile contract, explicit
support/precision policy and local numerical descriptors. Fe and Mg may use
different certified blank families. Ordinary negative samples and blanks have
separate support requirements. Nominal zeros, dark spectra and LIBS-derived
chemical labels remain inadmissible.

``preflight_final_campaign`` is metadata-only unless ``verify_files=True``.
``freeze_final_campaign`` checks that input files exist but does not read spectra.
``evaluate_final_campaign`` consumes durable receipts BEFORE reading numerical
inputs. It verifies and parses each file from one byte buffer, then calls a trusted
predictor(metadata_without_labels, wavelength_nm, intensity) -> class-score dict.
It never accepts precomputed CV predictions. Use ONE persistent receipt directory
for every campaign: per-identity claims prevent campaign renaming, subset reuse
and partial overlaps. Deleting receipts or lying in evidence/alias declarations
cannot be prevented by a local library; retain receipts and review source evidence.

Point metrics weight physical materials equally after averaging replicate scores.
Wilson intervals describe the separate conservative *any error in a connected
physical/family/session cluster* estimand, not an IID interval over spectra or a
confidence bound for physical point metrics. Predeclared precision uses the worst
case Wilson half width at p=0.5. No result automatically qualifies a model.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from collections import defaultdict
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from itertools import pairwise
from pathlib import Path
from statistics import NormalDist
from typing import Any

import numpy as np

from spectralab_ml.evaluation import _final_campaign_io as campaign_io
from spectralab_ml.evaluation import blank_admission_v2, test_lock
from spectralab_ml.evaluation._final_campaign_io import (
    MAX_JSON_BYTES,
    MAX_ROWS,
    MAX_SPECTRUM_BYTES,
    digest,
    encoded,
    exclusive_json,
    local_name,
    local_path,
    read_bytes,
    read_json,
    regular_file,
    validate_coverage,
    verify_spectrum,
)

ELEMENTS = ("Fe", "Mg")
GROUPS = ("positive", "negative", "blank")
DIMENSIONS = ("physical", "family", "session", "independent_clusters")
IDENTITIES = {**blank_admission_v2.SEEN_FIELDS, "assay_ids": "evidence_id"}
LIMITATIONS = [
    "Evidence attestations and alias-registry completeness require independent human review.",
    "Instrument resolution is independently documented; numerical spacing is not resolution.",
    "Use one permanent receipt directory across campaigns; deleting it defeats local reuse guards.",
    "The predictor callback and runtime identity are trusted integration code, not a sandbox.",
    "Cluster Wilson intervals assume independent clusters; residual unrecorded dependence remains.",
    "Physical point metrics have no binomial CI; cluster-any-error CI has a different estimand.",
    "Air conversion binds original/output hashes; their conversion relationship is attested.",
    "No automatic scientific qualification follows from successful software evaluation.",
]


def _text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip()) and value == value.strip()


def _sha(value: Any) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[a-f0-9]{64}", value) is not None


def _number(value: Any) -> bool:
    try:
        return type(value) in (float, int) and math.isfinite(value)
    except OverflowError:
        return False


def _intervals(value: Any) -> bool:
    return (
        isinstance(value, list)
        and bool(value)
        and all(
            isinstance(pair, list)
            and len(pair) == 2
            and all(_number(v) for v in pair)
            and 0 < pair[0] < pair[1]
            for pair in value
        )
        and all(first[1] < second[0] for first, second in pairwise(value))
    )


def _snapshot(value: Any) -> dict:
    raw = encoded(value)
    if len(raw) > MAX_JSON_BYTES or not isinstance(value, Mapping):
        raise ValueError("Expected a bounded JSON object")

    def integer(text: str) -> int:
        value = int(text)
        if not _number(value):
            raise ValueError("JSON integer exceeds the finite numerical range")
        return value

    return json.loads(raw, parse_int=integer)


class _Checks:
    def __init__(self) -> None:
        self.blockers: list[dict[str, str]] = []

    def add(self, valid: bool, code: str, path: str, message: str) -> bool:
        if not valid:
            self.blockers.append({"code": code, "path": path, "message": message})
        return valid

    def source(self, value: dict, path: str) -> None:
        self.add(
            _text(value.get("source")) and _sha(value.get("source_sha256")),
            "source_evidence_missing",
            path,
            "An attributable source and its SHA-256 are required.",
        )


class _Aliases:
    def __init__(self, registry: dict, checks: _Checks) -> None:
        self.parents: dict[tuple[str, str], tuple[str, str]] = {}
        aliases = registry.get("aliases")
        if not checks.add(
            isinstance(aliases, list) and len(aliases) <= 50_000,
            "alias_registry_missing",
            "seen_registry.aliases",
            "Supply a bounded list of reviewed identity aliases, including an empty list.",
        ):
            return
        for i, edge in enumerate(aliases):
            path = f"seen_registry.aliases[{i}]"
            if not isinstance(edge, dict):
                checks.add(False, "invalid_alias", path, "Alias must be an object.")
                continue
            field = edge.get("field")
            valid = isinstance(field, str) and field in set(IDENTITIES) - {"input_sha256s"}
            valid = valid and _text(edge.get("left")) and _text(edge.get("right"))
            checks.source(edge, path)
            if checks.add(
                valid,
                "invalid_alias",
                path,
                "Typed identity alias required; hashes cannot be aliased.",
            ):
                first, second = self.find(field, edge["left"]), self.find(field, edge["right"])
                self.parents[max(first, second)] = min(first, second)

    def find(self, field: str, value: str) -> tuple[str, str]:
        key = field, value
        self.parents.setdefault(key, key)
        while self.parents[key] != key:
            self.parents[key] = self.parents[self.parents[key]]
            key = self.parents[key]
        return key

    def value(self, field: str, value: str) -> str:
        return self.find(field, value)[1]


def _canonical(manifest: dict, registry: dict, aliases: _Aliases) -> tuple[dict, dict]:
    manifest, registry = copy.deepcopy(manifest), copy.deepcopy(registry)
    for field in IDENTITIES:
        values = registry.get(field)
        if isinstance(values, list):
            registry[field] = [aliases.value(field, v) if _text(v) else v for v in values]
    for sample in manifest.get("samples", []):
        if not isinstance(sample, dict):
            continue
        for field, key in blank_admission_v2.SEEN_FIELDS.items():
            if _text(sample.get(key)):
                sample[key] = aliases.value(field, sample[key])
        for label in (
            (sample.get("labels") or {}).values() if isinstance(sample.get("labels"), dict) else []
        ):
            if not isinstance(label, dict):
                continue
            if _text(label.get("physical_sample_id")):
                label["physical_sample_id"] = aliases.value(
                    "physical_sample_ids", label["physical_sample_id"]
                )
            if _text(label.get("evidence_id")):
                label["evidence_id"] = aliases.value("assay_ids", label["evidence_id"])
    manifest["schema_version"] = 1
    return manifest, registry


def _clusters(samples: list[dict]) -> dict[str, str]:
    parent = list(range(len(samples)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    seen = {}
    for i, sample in enumerate(samples):
        for field in ("physical_sample_id", "family_id", "session_id", "input_sha256"):
            key = field, sample[field]
            if key in seen:
                parent[find(i)] = find(seen[key])
            else:
                seen[key] = i
    members = defaultdict(list)
    for i, sample in enumerate(samples):
        members[find(i)].append(sample["measurement_id"])
    names = {key: digest(sorted(value)) for key, value in members.items()}
    return {sample["measurement_id"]: names[find(i)] for i, sample in enumerate(samples)}


def _group(sample: dict, element: str) -> str | None:
    label = sample["labels"][element]
    if sample["control_type"] == "sample":
        return label["state"] if label["state"] in {"positive", "negative"} else None
    if (
        sample["control_type"] in {"method_blank", "matrix_blank"}
        and label["state"] == "negative"
        and label["basis"] == "independent_upper_bound"
        and label.get("certified") is True
        and _number(label.get("upper_bound"))
        and 0 < label["upper_bound"] <= 0.1
    ):
        return "blank"
    return None


def _profile(manifest: dict, checks: _Checks) -> dict:
    profile = manifest.get("profile")
    profile = profile if isinstance(profile, dict) else {}
    for field in ("identity",):
        checks.add(
            _sha(profile.get(field)),
            "profile_identity_missing",
            f"profile.{field}",
            "Pin the actual trained profile identity.",
        )
    checks.add(
        _text(profile.get("instrument_id")),
        "instrument_missing",
        "profile.instrument_id",
        "One explicit instrument domain is required.",
    )
    classes = profile.get("class_order")
    valid_classes = isinstance(classes, list) and all(_text(v) for v in classes)
    valid_classes = (
        valid_classes and len(set(classes)) == len(classes) and set(ELEMENTS) <= set(classes)
    )
    checks.add(
        valid_classes,
        "class_order_invalid",
        "profile.class_order",
        "Unique profile classes must include Fe and Mg.",
    )
    thresholds = profile.get("thresholds")
    checks.add(
        valid_classes
        and isinstance(thresholds, dict)
        and set(thresholds) == set(classes)
        and all(_number(v) and 0 <= v <= np.nextafter(1.0, math.inf) for v in thresholds.values()),
        "thresholds_unfrozen",
        "profile.thresholds",
        "Pin every finite score threshold; >1 explicitly disables a class.",
    )
    checks.add(
        profile.get("units") == "nm" and profile.get("medium") == "vacuum",
        "profile_axis_invalid",
        "profile",
        "The scoring contract must explicitly use nm vacuum.",
    )
    checks.add(
        _intervals(profile.get("required_intervals_nm")),
        "profile_coverage_missing",
        "profile.required_intervals_nm",
        "All required bands and line sidebands must be covered; intervals ordered and disjoint.",
    )
    for field in ("max_sampling_step_nm", "max_resolution_fwhm_nm"):
        checks.add(
            _number(profile.get(field)) and profile[field] > 0,
            "profile_resolution_missing",
            f"profile.{field}",
            "Positive instrument resolution and native sampling limits are required.",
        )
    checks.add(
        type(profile.get("require_positive_signal")) is bool,
        "profile_signal_policy_missing",
        "profile.require_positive_signal",
        "Declare whether every observed interval requires positive emission.",
    )
    for path, files in (
        ("profile.artifact_files", profile.get("artifact_files")),
        ("implementation_files", manifest.get("implementation_files")),
    ):
        valid = isinstance(files, list) and 1 <= len(files) <= 1024
        checks.add(
            valid,
            "pinned_files_missing",
            path,
            "A bounded nonempty list of pinned local files is required.",
        )
        names = set()
        for i, item in enumerate(files if valid else []):
            item_valid = (
                isinstance(item, dict) and local_name(item.get("path")) and _sha(item.get("sha256"))
            )
            checks.add(
                item_valid,
                "pinned_file_invalid",
                f"{path}[{i}]",
                "Pin local relative path and SHA-256.",
            )
            if item_valid:
                checks.add(
                    item["path"] not in names,
                    "duplicate_pinned_file",
                    path,
                    "Pinned paths must be unique.",
                )
                names.add(item["path"])
    return profile


def _support_policy(manifest: dict, checks: _Checks) -> dict:
    policy = manifest.get("support_policy")
    policy = policy if isinstance(policy, dict) else {}
    checks.add(
        policy.get("ci_estimand") == "cluster_any_error",
        "precision_estimand_missing",
        "support_policy.ci_estimand",
        "Declare cluster_any_error CI separately from physical point metrics.",
    )
    checks.add(
        _number(policy.get("confidence_level")) and 0.5 < policy["confidence_level"] < 1,
        "confidence_invalid",
        "support_policy.confidence_level",
        "Predeclare confidence strictly between .5 and 1.",
    )
    checks.add(
        _number(policy.get("max_wilson_half_width")) and 0 < policy["max_wilson_half_width"] < 0.5,
        "precision_policy_missing",
        "support_policy.max_wilson_half_width",
        "Predeclare a worst-case cluster Wilson half width between 0 and .5.",
    )
    minimum = policy.get("min_support")
    minimum = minimum if isinstance(minimum, dict) else {}
    for element in ELEMENTS:
        for group in GROUPS:
            row = minimum.get(element, {})
            row = row.get(group, {}) if isinstance(row, dict) else {}
            row = row if isinstance(row, dict) else {}
            checks.add(
                set(row) == set(DIMENSIONS)
                and all(type(v) is int and 1 <= v <= 100_000 for v in row.values()),
                "support_policy_missing",
                f"support_policy.min_support.{element}.{group}",
                "Predeclare positive minimum physical/family/session/independent-cluster support.",
            )
    return policy


def _sample_contract(sample: dict, profile: dict, path: str, checks: _Checks) -> None:
    checks.add(
        sample.get("instrument_id") == profile.get("instrument_id"),
        "instrument_incompatible",
        f"{path}.instrument_id",
        "A different instrument needs a separately frozen compatible profile.",
    )
    descriptor = sample.get("local_spectrum")
    descriptor = descriptor if isinstance(descriptor, dict) else {}
    checks.add(
        local_name(descriptor.get("path"))
        and _sha(descriptor.get("sha256"))
        and descriptor.get("format") in ("csv", "tsv")
        and descriptor.get("columns") == ["wavelength_nm", "intensity"]
        and type(descriptor.get("rows")) is int
        and 2 <= descriptor["rows"] <= MAX_ROWS,
        "local_spectrum_invalid",
        f"{path}.local_spectrum",
        "Pin local CSV/TSV SHA, two named columns and bounded exact row count.",
    )
    axis = sample.get("axis")
    axis = axis if isinstance(axis, dict) else {}
    effective = axis
    expected_hash = sample.get("input_sha256")
    if axis.get("medium") == "air":
        effective = axis.get("conversion")
        effective = effective if isinstance(effective, dict) else {}
        expected_hash = effective.get("output_sha256")
        raw = sample.get("raw_spectrum") or {}
        checks.add(
            isinstance(raw, dict) and local_name(raw.get("local_path")),
            "original_air_file_missing",
            f"{path}.raw_spectrum.local_path",
            "Pin the original air input as well as the converted vacuum file.",
        )
    checks.add(
        descriptor.get("sha256") == expected_hash and _sha(expected_hash),
        "local_checksum_binding_invalid",
        f"{path}.local_spectrum.sha256",
        "The evaluated file must match the source or documented conversion checksum.",
    )
    coverage = effective.get("coverage_intervals_nm")
    checks.add(
        _intervals(coverage),
        "coverage_unconfirmed",
        f"{path}.axis",
        "Document complete observed intervals in the effective vacuum axis.",
    )
    if _intervals(coverage) and _intervals(profile.get("required_intervals_nm")):
        checks.add(
            all(
                any(a <= lower and b >= upper for a, b in coverage)
                for lower, upper in profile["required_intervals_nm"]
            ),
            "coverage_gap",
            f"{path}.axis",
            "Observed bands must cover every required interval; no gap interpolation.",
        )
    for actual, required in (
        ("max_sampling_step_nm", "max_sampling_step_nm"),
        ("resolution_fwhm_nm", "max_resolution_fwhm_nm"),
    ):
        value, limit = effective.get(actual), profile.get(required)
        checks.add(
            _number(value) and _number(limit) and 0 < value <= limit,
            "sampling_or_resolution_incompatible",
            f"{path}.axis.{actual}",
            "Documented native sampling and optical FWHM must meet the profile requirements.",
        )


def preflight_final_campaign(
    manifest: Mapping[str, Any],
    seen_registry: Mapping[str, Any],
    *,
    data_root: Path | None = None,
    verify_files: bool = False,
) -> dict[str, Any]:
    """Collect blockers without predictions; numerical checking is explicit opt-in.

    Metadata-ready does not mean numerically verified or scientifically qualified.
    The empty current-search manifest reproducibly stays blocked without file reads.
    """
    checks = _Checks()
    try:
        manifest, seen_registry = _snapshot(manifest), _snapshot(seen_registry)
    except (TypeError, ValueError, RecursionError):
        return {
            "schema_version": 3,
            "status": "blocked",
            "blockers": [
                {
                    "code": "invalid_json",
                    "path": "$",
                    "message": "Bounded finite JSON objects are required.",
                }
            ],
            "final_metrics": None,
            "numerical_files_read": 0,
        }
    checks.add(
        type(verify_files) is bool,
        "verification_mode_invalid",
        "verify_files",
        "An explicit boolean verification mode is required.",
    )
    checks.add(
        type(manifest.get("schema_version")) is int and manifest["schema_version"] == 3,
        "unsupported_schema",
        "schema_version",
        "Final campaign schema 3 is required.",
    )
    checks.add(
        manifest.get("evaluation_role") == "independent_final",
        "not_independent_final",
        "evaluation_role",
        "Development, CV and prior final tests cannot become an untouched final campaign.",
    )
    checks.add(
        manifest.get("assessment_mode") == "binary_score_threshold_only",
        "assessment_mode_unsupported",
        "assessment_mode",
        "This entrypoint evaluates raw scores and frozen thresholds; no selective policy metrics.",
    )
    checks.add(
        manifest.get("selection_basis") == "metadata_only_before_scores",
        "selection_basis_invalid",
        "selection_basis",
        "Select sample support and precision using metadata before any scores.",
    )
    source = manifest.get("data_source")
    source = source if isinstance(source, dict) else {}
    checks.add(
        _text(source.get("id")),
        "data_source_missing",
        "data_source.id",
        "Freeze the campaign data-source identity.",
    )
    checks.source(source, "data_source")
    samples = manifest.get("samples")
    if not isinstance(samples, list) or len(samples) > 10_000:
        checks.add(False, "samples_invalid", "samples", "A bounded sample list is required.")
        manifest["samples"] = []
    aliases = _Aliases(seen_registry, checks)
    canonical, canonical_seen = _canonical(manifest, seen_registry, aliases)
    base = blank_admission_v2.admit_final_campaign(canonical, canonical_seen)
    checks.blockers.extend(
        row for row in base["blockers"] if row["code"] != "certified_blank_family_missing"
    )
    profile = _profile(manifest, checks)
    comparisons = manifest.get("comparisons", {})
    if not checks.add(
        isinstance(comparisons, dict) and len(comparisons) <= 8,
        "comparisons_invalid",
        "comparisons",
        "Optional named comparisons must be a bounded profile map.",
    ):
        comparisons = {}
    for name, other in comparisons.items():
        checks.add(
            isinstance(name, str)
            and re.fullmatch(r"[a-z][a-z0-9_-]{0,40}", name) is not None
            and name != "primary",
            "comparison_name_invalid",
            "comparisons",
            "Comparison names must be safe and distinct from primary.",
        )
        before = len(checks.blockers)
        _profile(
            {"profile": other, "implementation_files": manifest.get("implementation_files")}, checks
        )
        for blocker in checks.blockers[before:]:
            blocker["path"] = f"comparisons.{name}." + blocker["path"]
    policy = _support_policy(manifest, checks)
    historical_hashes = seen_registry.get("input_sha256s")
    historical_hashes = {
        value
        for value in (historical_hashes if isinstance(historical_hashes, list) else [])
        if _sha(value)
    }
    numerical_hashes = set()
    for sample in manifest["samples"]:
        if isinstance(sample, dict):
            numerical = sample.get("local_spectrum")
            for value in (
                sample.get("input_sha256"),
                numerical.get("sha256") if isinstance(numerical, dict) else None,
            ):
                if _sha(value):
                    numerical_hashes.add(value)
    for i, sample in enumerate(manifest["samples"]):
        if not isinstance(sample, dict):
            continue
        _sample_contract(sample, profile, f"samples[{i}]", checks)
        labels = sample.get("labels")
        for element, label in labels.items() if isinstance(labels, dict) else []:
            if isinstance(label, dict) and _sha(label.get("source_sha256")):
                checks.add(
                    label["source_sha256"] not in numerical_hashes,
                    "spectral_evidence_not_independent",
                    f"samples[{i}].labels.{element}",
                    "An original or converted campaign spectrum cannot be chemical assay evidence.",
                )
        numerical = sample.get("local_spectrum")
        if isinstance(numerical, dict) and _sha(numerical.get("sha256")):
            checks.add(
                numerical["sha256"] not in historical_hashes,
                "previously_seen_evaluated_input",
                f"samples[{i}].local_spectrum.sha256",
                "The evaluated vacuum file overlaps earlier work, including converted inputs.",
            )
        for name, other in comparisons.items():
            if isinstance(other, dict):
                _sample_contract(sample, other, f"samples[{i}].comparison.{name}", checks)
        checks.add(
            sample.get("data_source_id") == source.get("id") and _text(source.get("id")),
            "sample_source_mismatch",
            f"samples[{i}].data_source_id",
            "Bind every sample to the frozen data-source identity.",
        )

    physical_controls, physical_families, outputs = {}, {}, set()
    for i, sample in enumerate(canonical["samples"]):
        if not isinstance(sample, dict):
            continue
        physical, control = sample.get("physical_sample_id"), sample.get("control_type")
        if _text(physical) and _text(control):
            checks.add(
                physical not in physical_controls or physical_controls[physical] == control,
                "conflicting_control_type",
                f"samples[{i}].control_type",
                "One physical material cannot count as both an ordinary sample and a blank.",
            )
            physical_controls[physical] = control
        family = sample.get("family_id")
        if _text(physical) and _text(family):
            checks.add(
                physical not in physical_families or physical_families[physical] == family,
                "inconsistent_physical_family",
                f"samples[{i}].family_id",
                "Resolve aliases before assigning one physical material to different families.",
            )
            physical_families[physical] = family
        descriptor = sample.get("local_spectrum") or {}
        checksum = descriptor.get("sha256") if isinstance(descriptor, dict) else None
        if _sha(checksum):
            checks.add(
                checksum not in outputs,
                "duplicate_evaluated_input",
                f"samples[{i}].local_spectrum",
                "Different source/conversion metadata cannot make one file independent replicates.",
            )
            outputs.add(checksum)

    # Supports only count structurally valid records. Other blockers can never be
    # repaired by inflating the support from rejected records.
    invalid_indices = {
        int(match.group(1))
        for row in checks.blockers
        if (match := re.match(r"samples\[(\d+)\](?:\.|$)", row["path"]))
    }
    physical_qualification = {}
    for i, sample in enumerate(canonical["samples"]):
        if not isinstance(sample, dict) or i in invalid_indices:
            continue
        key = sample["physical_sample_id"]
        qualification = tuple(_group(sample, element) for element in ELEMENTS)
        if not checks.add(
            key not in physical_qualification or physical_qualification[key] == qualification,
            "inconsistent_blank_qualification",
            f"samples[{i}].labels",
            "Every replicate must carry the same per-element sample/blank qualification evidence.",
        ):
            invalid_indices.add(i)
        physical_qualification[key] = qualification
    valid = [
        sample
        for i, sample in enumerate(canonical["samples"])
        if isinstance(sample, dict) and i not in invalid_indices
    ]
    clusters = _clusters(valid)
    support = {}
    precision = {}
    for element in ELEMENTS:
        support[element], precision[element] = {}, {}
        for group in GROUPS:
            rows = [sample for sample in valid if _group(sample, element) == group]
            support[element][group] = {
                "physical": len({sample["physical_sample_id"] for sample in rows}),
                "family": len({sample["family_id"] for sample in rows}),
                "session": len({sample["session_id"] for sample in rows}),
                "independent_clusters": len(
                    {clusters[sample["measurement_id"]] for sample in rows}
                ),
            }
            minimum = policy.get("min_support", {})
            minimum = minimum.get(element, {}) if isinstance(minimum, dict) else {}
            minimum = minimum.get(group, {}) if isinstance(minimum, dict) else {}
            for dimension, count in support[element][group].items():
                target = minimum.get(dimension) if isinstance(minimum, dict) else None
                if type(target) is int and target > 0:
                    checks.add(
                        count >= target,
                        "insufficient_support",
                        f"support.{element}.{group}.{dimension}",
                        f"Found {count}; frozen minimum is {target}.",
                    )
            if group == "blank":
                checks.add(
                    bool(rows),
                    "certified_element_blank_missing",
                    f"support.{element}.blank",
                    f"Need a certified independent method/matrix blank specifically for {element}.",
                )
            n, level = (
                support[element][group]["independent_clusters"],
                policy.get("confidence_level"),
            )
            half = None
            if n >= 2 and _number(level) and 0.5 < level < 1:
                z = -NormalDist().inv_cdf((1 - level) / 2)
                half = z / (2 * math.sqrt(n + z * z))
            precision[element][group] = {
                "independent_clusters": n,
                "worst_case_wilson_half_width": half,
            }
            maximum = policy.get("max_wilson_half_width")
            if _number(maximum) and 0 < maximum < 0.5:
                checks.add(
                    half is not None and half <= maximum,
                    "precision_support_insufficient",
                    f"precision.{element}.{group}",
                    "Clusters must satisfy the predeclared worst-case Wilson width; n<2 has no CI.",
                )
    files_read, files_verified = 0, 0

    def mark_read() -> None:
        nonlocal files_read
        files_read += 1

    if (
        verify_files
        and not checks.blockers
        and checks.add(
            data_root is not None,
            "data_root_missing",
            "data_root",
            "Local numerical verification requires an explicit data root.",
        )
    ):
        try:
            _verify_pinned(Path(data_root), manifest)
        except (OSError, TypeError, ValueError) as exc:
            checks.add(False, "pinned_artifact_invalid", "profile.artifact_files", str(exc))
        for i, sample in enumerate(manifest["samples"]):
            if any(item["code"] == "pinned_artifact_invalid" for item in checks.blockers):
                break
            try:
                axis, intensity = verify_spectrum(
                    Path(data_root), sample, profile, on_read=mark_read
                )
                for other in comparisons.values():
                    validate_coverage(axis, intensity, other)
                files_verified += 1
            except (OSError, TypeError, ValueError) as exc:
                checks.add(
                    False, "numerical_input_invalid", f"samples[{i}].local_spectrum", str(exc)
                )
    return {
        "schema_version": 3,
        "artifact_type": "final_campaign_preflight",
        "status": "blocked"
        if checks.blockers
        else "admitted"
        if verify_files
        else "metadata_ready",
        "campaign_id": manifest.get("campaign_id"),
        "manifest_sha256": digest(manifest),
        "seen_registry_sha256": digest(seen_registry),
        "profile_identity": profile.get("identity"),
        "data_source_sha256": digest(source),
        "blockers": checks.blockers,
        "support": support,
        "precision": precision,
        "canonical_clusters": clusters,
        "numerical_files_read": files_read,
        "numerical_spectra_verified": files_verified,
        "numerical_verification": "completed"
        if verify_files and not checks.blockers
        else "not_completed",
        "final_evaluation_performed": False,
        "final_metrics": None,
        "limitations": LIMITATIONS,
    }


def empty_final_manifest(campaign_id: str = "pending-independent-final-campaign") -> dict:
    """A deliberately blocked planning template; it invents no support policy or profile."""
    return {
        "schema_version": 3,
        "campaign_id": campaign_id,
        "evaluation_role": "independent_final",
        "assessment_mode": "binary_score_threshold_only",
        "selection_basis": "metadata_only_before_scores",
        "selection_frozen_before_predictions": False,
        "estimand": "operational_above_threshold",
        "data_source": {},
        "profile": {},
        "support_policy": {},
        "implementation_files": [],
        "samples": [],
    }


def _guard_hashes() -> dict[str, str]:
    files = {
        "final_campaign_v3.py": Path(__file__),
        "_final_campaign_io.py": Path(campaign_io.__file__),
        "blank_admission_v2.py": Path(blank_admission_v2.__file__),
        "test_lock.py": Path(test_lock.__file__),
    }
    return {name: hashlib.sha256(path.read_bytes()).hexdigest() for name, path in files.items()}


def _runtime_identity(manifest: dict) -> dict:
    result = {
        "profile_identity": manifest["profile"]["identity"],
        "artifact_sha256": {
            row["path"]: row["sha256"] for row in manifest["profile"]["artifact_files"]
        },
        "implementation_sha256": {
            row["path"]: row["sha256"] for row in manifest["implementation_files"]
        },
    }
    if manifest.get("comparisons"):
        result["comparisons"] = {
            name: {
                "profile_identity": profile["identity"],
                "artifact_sha256": {
                    row["path"]: row["sha256"] for row in profile["artifact_files"]
                },
            }
            for name, profile in manifest["comparisons"].items()
        }
    return result


def _separate_pinned_inputs(root: Path, manifest: dict) -> list[dict]:
    """Reject numerical inputs disguised as artifacts using metadata/stat only."""
    files = manifest["profile"]["artifact_files"] + manifest["implementation_files"]
    files += [
        row
        for profile in manifest.get("comparisons", {}).values()
        for row in profile["artifact_files"]
    ]
    numerical_names = {sample["local_spectrum"]["path"] for sample in manifest["samples"]}
    numerical_names.update(
        sample["raw_spectrum"]["local_path"]
        for sample in manifest["samples"]
        if sample["axis"]["medium"] == "air"
    )
    numerical_hashes = {
        value
        for sample in manifest["samples"]
        for value in (sample["input_sha256"], sample["local_spectrum"]["sha256"])
    }
    numerical_inodes = set()
    for name in numerical_names:
        path = local_path(root, name)
        regular_file(path, MAX_SPECTRUM_BYTES)
        info = path.lstat()
        numerical_inodes.add((info.st_dev, info.st_ino))
    for item in files:
        if item["path"] in numerical_names or item["sha256"] in numerical_hashes:
            raise ValueError("A numerical input cannot be opened as a model or implementation file")
        path = local_path(root, item["path"])
        regular_file(path, MAX_JSON_BYTES)
        info = path.lstat()
        if (info.st_dev, info.st_ino) in numerical_inodes:
            raise ValueError("A numerical input hardlink cannot be opened as a model file")
    return files


def _verify_pinned(root: Path, manifest: dict) -> None:
    for item in _separate_pinned_inputs(root, manifest):
        raw = read_bytes(local_path(root, item["path"]), MAX_JSON_BYTES)
        if hashlib.sha256(raw).hexdigest() != item["sha256"]:
            raise ValueError("Pinned model or implementation checksum changed")


def freeze_final_campaign(
    path: Path, manifest: Mapping[str, Any], seen_registry: Mapping[str, Any], *, data_root: Path
) -> dict:
    """Durably freeze reviewed metadata/model identities without opening spectrum bytes."""
    manifest, seen_registry = _snapshot(manifest), _snapshot(seen_registry)
    report = preflight_final_campaign(manifest, seen_registry)
    if report["status"] != "metadata_ready":
        raise ValueError("Final campaign is blocked; metadata readiness is required before freeze")
    _verify_pinned(Path(data_root), manifest)
    for sample in manifest["samples"]:
        regular_file(local_path(data_root, sample["local_spectrum"]["path"]), MAX_SPECTRUM_BYTES)
        if sample["axis"]["medium"] == "air":
            regular_file(
                local_path(data_root, sample["raw_spectrum"]["local_path"]), MAX_SPECTRUM_BYTES
            )
    freeze = {
        "schema_version": 3,
        "artifact_type": "final_campaign_freeze",
        "frozen_at": datetime.now(UTC).isoformat(),
        "manifest": manifest,
        "seen_registry": seen_registry,
        "preflight": report,
        "runtime_identity": _runtime_identity(manifest),
        "guard_sha256": _guard_hashes(),
        "numerical_inputs_read": False,
    }
    freeze["freeze_sha256"] = digest(freeze)
    exclusive_json(Path(path), freeze)
    return freeze


def load_final_freeze(freeze_path: Path, *, data_root: Path, verify_pinned: bool = True) -> dict:
    """Validate a freeze BEFORE an integration loads any model artifact.

    Metadata identity, guard hashes and replayed preflight are checked first.
    Numerical/artifact path, checksum and inode separation uses only metadata and
    stat; no numerical bytes are opened. ``verify_pinned=False`` skips artifact
    byte checks, never the separation guard. CLI model binders must call this
    function before loading artifacts selected by a freeze document.
    """
    if type(verify_pinned) is not bool:
        raise ValueError("verify_pinned must be an explicit boolean")
    freeze = read_json(Path(freeze_path))
    content = {key: value for key, value in freeze.items() if key != "freeze_sha256"}
    if (
        not _sha(freeze.get("freeze_sha256"))
        or digest(content) != freeze["freeze_sha256"]
        or type(freeze.get("schema_version")) is not int
        or freeze["schema_version"] != 3
        or freeze.get("artifact_type") != "final_campaign_freeze"
        or freeze.get("numerical_inputs_read") is not False
    ):
        raise ValueError("Invalid or changed immutable campaign freeze")
    try:
        manifest, registry = freeze["manifest"], freeze["seen_registry"]
        report = preflight_final_campaign(manifest, registry)
        if report["status"] != "metadata_ready" or report != freeze["preflight"]:
            raise ValueError("Frozen campaign no longer passes the same metadata checks")
        if freeze["guard_sha256"] != _guard_hashes():
            raise ValueError("Frozen evaluation implementation changed")
        if _runtime_identity(manifest) != freeze["runtime_identity"]:
            raise ValueError("Frozen runtime identity is inconsistent with its manifest")
        if datetime.fromisoformat(freeze["frozen_at"]).utcoffset() is None:
            raise ValueError("Frozen timestamp must include a timezone")
    except (KeyError, TypeError, OverflowError, RecursionError) as exc:
        raise ValueError("Invalid immutable campaign freeze schema") from exc
    if verify_pinned:
        _verify_pinned(Path(data_root), manifest)
    else:
        _separate_pinned_inputs(Path(data_root), manifest)
    return freeze


def _claim_keys(manifest: dict, registry: dict) -> list[str]:
    aliases = _Aliases(registry, _Checks())
    canonical, _ = _canonical(manifest, registry, aliases)
    keys = set()
    components = defaultdict(list)
    for item in list(aliases.parents):
        components[aliases.find(*item)].append(item)

    def add(field: str, value: str) -> None:
        root = aliases.find(field, value)
        # Claim every alias, not only the lexicographic representative. A later
        # registry may add a new, smaller representative for a consumed material.
        members = components.get(root, [root])
        keys.update(digest([field, member[1]]) for member in members)

    for sample in canonical["samples"]:
        for field, name in blank_admission_v2.SEEN_FIELDS.items():
            add(field, sample[name])
        keys.add(digest(["input_sha256s", sample["local_spectrum"]["sha256"]]))
        for label in sample["labels"].values():
            if _text(label.get("evidence_id")):
                add("assay_ids", label["evidence_id"])
    return sorted(keys)


def _wilson(errors: int, n: int, level: float) -> list[float] | None:
    if n < 2:
        return None
    z = -NormalDist().inv_cdf((1 - level) / 2)
    p = errors / n
    denominator = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denominator
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denominator
    return [max(0.0, center - half), min(1.0, center + half)]


def _metrics(manifest: dict, registry: dict, scores: list[dict], profile: dict) -> dict:
    canonical, _ = _canonical(manifest, registry, _Aliases(registry, _Checks()))
    samples = canonical["samples"]
    clusters = _clusters(samples)
    by_material = defaultdict(list)
    for sample, score in zip(samples, scores, strict=True):
        by_material[sample["physical_sample_id"]].append((sample, score))
    metrics = {}
    for element in ELEMENTS:
        metrics[element] = {}
        threshold = profile["thresholds"][element]
        for group in GROUPS:
            errors, cluster_errors = [], defaultdict(list)
            for rows in by_material.values():
                sample = rows[0][0]
                if _group(sample, element) != group:
                    continue
                mean = float(np.mean([score[element] for _, score in rows]))
                detected = mean >= threshold
                error = not detected if group == "positive" else detected
                errors.append(error)
                cluster_errors[clusters[sample["measurement_id"]]].append(error)
            n, count = len(cluster_errors), sum(any(value) for value in cluster_errors.values())
            error_rate = sum(errors) / len(errors) if errors else None
            metrics[element][group] = {
                "physical_support": len(errors),
                "physical_error_count": sum(errors),
                "physical_error_rate": error_rate,
                "physical_recall": 1 - error_rate
                if group == "positive" and error_rate is not None
                else None,
                "physical_false_positive_rate": error_rate if group != "positive" else None,
                "physical_ci": None,
                "independent_clusters": n,
                "clusters_with_any_error": count,
                "cluster_any_error_rate": count / n if n else None,
                "cluster_any_error_wilson_ci": _wilson(
                    count, n, manifest["support_policy"]["confidence_level"]
                ),
            }
    return metrics


def evaluate_final_campaign(
    freeze_path: Path,
    receipt_dir: Path,
    *,
    data_root: Path,
    runtime_identity: Mapping[str, Any],
    predictor: Callable[[dict, np.ndarray, np.ndarray], dict[str, float]] | None = None,
    predictors: Mapping[str, Callable] | None = None,
    replay: bool = False,
) -> dict:
    """Evaluate a frozen independent campaign once; an identical replay is labeled as such.

    Runtime identities are checked before receipts. Any numerical/callback failure
    after receipt consumption leaves the test consumed, without final metrics.
    Only the trusted callback is executable: manifests never select Python code.
    """
    freeze = load_final_freeze(freeze_path, data_root=data_root, verify_pinned=False)
    saved = freeze["freeze_sha256"]
    manifest, registry = freeze["manifest"], freeze["seen_registry"]
    report = freeze["preflight"]
    expected_runtime = _runtime_identity(manifest)
    if runtime_identity != expected_runtime or expected_runtime != freeze["runtime_identity"]:
        raise ValueError("Runtime profile or data-source implementation differs from the freeze")
    profiles = {"primary": manifest["profile"], **manifest.get("comparisons", {})}
    if predictor is not None and predictors is not None:
        raise ValueError("Supply predictor or predictors, not both")
    callbacks = {"primary": predictor} if predictors is None else dict(predictors)
    if set(callbacks) != set(profiles) or not all(callable(fn) for fn in callbacks.values()):
        raise ValueError(
            "A trusted spectrum-to-score callback is required; CV scores are not input"
        )
    _verify_pinned(Path(data_root), manifest)
    identity = {
        "test_content_hash": digest(sorted(_claim_keys(manifest, registry))),
        "protocol_hash": saved,
        "profile_identity": manifest["profile"]["identity"],
        "baseline_sha256": digest(
            {name: profile["artifact_files"] for name, profile in profiles.items()}
        ),
        "implementation_sha256": {
            **{f"runtime:{k}": v for k, v in expected_runtime["implementation_sha256"].items()},
            **{f"guard:{k}": v for k, v in freeze["guard_sha256"].items()},
        },
    }
    directory = Path(receipt_dir)
    directory.mkdir(parents=True, exist_ok=True)
    if directory.is_symlink():
        raise ValueError("Receipt directory cannot be a symbolic link")
    campaign_key = digest(["campaign_id", manifest["campaign_id"]])
    receipt = test_lock.consume_test(
        directory / f"campaign-{campaign_key}.json", identity, replay=replay
    )
    for key in _claim_keys(manifest, registry):
        test_lock.consume_test(directory / f"input-{key}.json", identity, replay=replay)
    result = {
        "schema_version": 3,
        "artifact_type": "independent_final_campaign_evaluation",
        "campaign_id": manifest["campaign_id"],
        "freeze_sha256": saved,
        "evaluation_kind": receipt["evaluation_kind"],
        "receipt": receipt,
        "profile_identity": manifest["profile"]["identity"],
        "data_source_sha256": digest(manifest["data_source"]),
        "assessment_mode": "binary_score_threshold_only",
        "selective_policy_evaluated": False,
        "support": report["support"],
        "precision": report["precision"],
        "status": "blocked",
        "final_evaluation_performed": False,
        "final_metrics": None,
        "qualification": "not_automatically_qualified",
        "limitations": LIMITATIONS,
    }
    scores = {name: [] for name in profiles}
    try:
        for sample in manifest["samples"]:
            axis, intensity = verify_spectrum(Path(data_root), sample, manifest["profile"])
            for profile in profiles.values():
                validate_coverage(axis, intensity, profile)
            metadata = {
                key: copy.deepcopy(sample[key])
                for key in ("measurement_id", "instrument_id", "axis")
            }
            if sample["axis"]["medium"] == "air":
                metadata["original_axis"] = copy.deepcopy(sample["axis"])
                metadata["axis"] = {
                    **copy.deepcopy(sample["axis"]["conversion"]),
                    "units": "nm",
                    "medium": "vacuum",
                }
            for name, callback in callbacks.items():
                score = callback(copy.deepcopy(metadata), axis, intensity)
                if (
                    not isinstance(score, dict)
                    or set(score) != set(profiles[name]["class_order"])
                    or not all(_number(value) and 0 <= value <= 1 for value in score.values())
                ):
                    raise ValueError(
                        "Predictor must return the frozen classes with finite scores in [0,1]"
                    )
                scores[name].append(copy.deepcopy(score))
    except (OSError, TypeError, ValueError, RuntimeError) as exc:
        result["blockers"] = [{"code": "evaluation_failed_test_consumed", "message": str(exc)}]
        result["completed_predictions"] = {name: len(value) for name, value in scores.items()}
        return result
    result.update(
        status="completed",
        final_evaluation_performed=True,
        final_metrics=_metrics(manifest, registry, scores["primary"], profiles["primary"]),
        predictions=[
            {"measurement_id": sample["measurement_id"], "scores": score}
            for sample, score in zip(manifest["samples"], scores["primary"], strict=True)
        ],
    )
    if len(profiles) > 1:
        result["comparison_metrics"] = {
            name: _metrics(manifest, registry, scores[name], profile)
            for name, profile in profiles.items()
        }
        result["comparison_predictions"] = {
            name: [
                {"measurement_id": sample["measurement_id"], "scores": score}
                for sample, score in zip(manifest["samples"], values, strict=True)
            ]
            for name, values in scores.items()
        }
        result["paired_physical_error_rate_delta"] = {
            name: {
                element: {
                    group: result["comparison_metrics"][name][element][group]["physical_error_rate"]
                    - result["final_metrics"][element][group]["physical_error_rate"]
                    for group in GROUPS
                }
                for element in ELEMENTS
            }
            for name in profiles
            if name != "primary"
        }
    return result
