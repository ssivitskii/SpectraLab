"""Development-only line evidence, robust local continuum and held-out calibration.

The stored probabilities target the known operational composition labels in the
development population. They are not concentration estimates or blank validation.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import stat
from collections import Counter
from pathlib import Path

import numpy as np
from scipy.optimize import minimize
from scipy.special import expit, logit

from spectralab_ml.data.reference import ReferenceCatalog
from spectralab_ml.models.line_adapter_v2 import (
    BANDS,
    CLASSES,
    LineAdapterV2,
    extract_line_features,
    line_plan,
)

GRID = np.linspace(240.0, 900.0, 6601)
VARIANTS = ("v2-line-baseline", "continuum-snr-neutral", "continuum-snr-ion")
QUALIFICATION = "development_only_no_independent_blank_validation"
DECISION_POLICY = {
    c: {
        "oxide": oxide,
        "units": "oxide_wt_percent",
        "negative_max": 0.1,
        "positive_min": 1.0,
        "nominal_decision_level": 0.5,
        "zero_policy": "unknown",
    }
    for c, oxide in zip(CLASSES, ("Na2O", "MgO", "CaO", "FeOT"), strict=True)
}


def digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


def physical_weights(ids: list[str]) -> np.ndarray:
    if not ids or any(not isinstance(value, str) or not value for value in ids):
        raise ValueError("Nonempty physical sample identities required")
    counts = Counter(ids)
    return np.asarray([1 / counts[value] for value in ids])


def validate_labels(labels: np.ndarray, ids: list[str]) -> np.ndarray:
    labels = np.asarray(labels)
    if labels.shape != (len(ids), 4) or not np.isin(labels, [-1, 0, 1]).all():
        raise ValueError("Aligned three-state operational labels required")
    physical_weights(ids)
    for sample in set(ids):
        rows = np.asarray(ids) == sample
        if np.any(labels[rows] != labels[rows][0]):
            raise ValueError("Conflicting physical-sample labels")
    return labels


def feature_plan(catalog: ReferenceCatalog, variant: str) -> dict:
    if variant not in VARIANTS or catalog.metadata.get("wavelength_medium") != "vacuum":
        raise ValueError("Supported variant and explicitly vacuum reference required")
    plan = copy.deepcopy(line_plan(catalog, include_uv=True))
    stages = {}
    for element in CLASSES:
        stages[element] = []
        for center in plan["lines_nm"][element]:
            matching = [
                line.ion_stage
                for line in catalog.lines
                if line.element == element
                and line.wavelength_medium == "vacuum"
                and line.ion_stage in (1, 2)
                and abs(line.wavelength_nm - center) < 1e-9
            ]
            if len(set(matching)) != 1:
                raise ValueError("Reference line must have an unambiguous ion stage")
            stages[element].append(matching[0])
        if variant == "continuum-snr-neutral":
            plan["lines_nm"][element] = [
                c for c, s in zip(plan["lines_nm"][element], stages[element], strict=True) if s == 1
            ]
            stages[element] = [1] * len(plan["lines_nm"][element])
    plan.update(
        schema_version=3,
        variant=variant,
        stages=stages,
        grid_nm={"start": 240.0, "end": 900.0, "points": 6601},
        continuum="linear interpolation between medians of left/right sidebands",
        noise="1.4826 median absolute sideband residual deviation; floor=0.001 band positive p95",
        robust_noise_floor_fraction=0.001,
        snr_cap=1000.0,
        features=(
            ["unchanged v2 local quartile excess/band sum", "unchanged v2 excess/background"]
            if variant == "v2-line-baseline"
            else ["log1p(1000 positive linear-continuum excess/band sum)", "log1p(SNR)"]
        ),
        response_correction="none: no measured instrument response supplied",
        matrix_interpretation=(
            "local contrast and stage evidence are proxies, not causal corrections"
        ),
    )
    validate_plan(plan)
    return plan


def _numeric(value: object) -> bool:
    return type(value) in (int, float) and np.isfinite(value)


def validate_decision_policy(policy: dict) -> None:
    if not isinstance(policy, dict) or set(policy) != set(CLASSES):
        raise ValueError("Exact four-class operational oxide policy required")
    for c, expected in DECISION_POLICY.items():
        actual = policy[c]
        if not isinstance(actual, dict) or set(actual) != set(expected):
            raise ValueError("Unsupported operational oxide policy")
        for field, value in expected.items():
            if actual[field] != value or (isinstance(value, float) and not _numeric(actual[field])):
                raise ValueError("Unsupported operational oxide policy")


def validate_plan(plan: dict) -> None:
    if (
        not isinstance(plan, dict)
        or plan.get("schema_version") != 3
        or plan.get("variant") not in VARIANTS
        or plan.get("class_order") != CLASSES
        or plan.get("wavelength_medium") != "vacuum"
        or plan.get("bands") != BANDS
        or plan.get("grid_nm") != {"start": 240.0, "end": 900.0, "points": 6601}
    ):
        raise ValueError("Unsupported v3 feature plan")
    constants = {
        "core_half_width_nm": 0.2,
        "sideband_inner_nm": 0.5,
        "sideband_outer_nm": 1.0,
        "robust_noise_floor_fraction": 0.001,
        "snr_cap": 1000.0,
    }
    if any(not _numeric(plan.get(k)) or plan[k] != v for k, v in constants.items()):
        raise ValueError("Unsupported feature windows or numeric constants")
    for field in ("lines_nm", "stages"):
        if not isinstance(plan.get(field), dict) or set(plan[field]) != set(CLASSES):
            raise ValueError("Exact line-plan classes required")
    for element in CLASSES:
        centers, stages = plan["lines_nm"][element], plan["stages"][element]
        if (
            not isinstance(centers, list)
            or not isinstance(stages, list)
            or not 1 <= len(centers) <= 32
            or len(stages) != len(centers)
            or any(not _numeric(c) for c in centers)
            or centers != sorted(set(centers))
            or any(type(s) is not int or s not in (1, 2) for s in stages)
            or any(not any(a + 1.0 <= c <= b - 1.0 for a, b in BANDS) for c in centers)
            or (plan["variant"] == "continuum-snr-neutral" and any(s != 1 for s in stages))
        ):
            raise ValueError("Invalid line centres, stages or unobserved windows")


def extract_features(
    spectra: np.ndarray, grid: np.ndarray, plan: dict
) -> tuple[dict[str, np.ndarray], np.ndarray]:
    """Per-spectrum fixed transforms; no across-sample fitted preprocessing."""
    validate_plan(plan)
    X, grid = np.asarray(spectra, float), np.asarray(grid, float)
    if (
        grid.shape != GRID.shape
        or not np.allclose(grid, GRID, atol=1e-8, rtol=0)
        or X.ndim != 2
        or X.shape[1] != len(GRID)
        or not len(X)
        or not np.isfinite(X).all()
    ):
        raise ValueError("Finite spectra on the fixed 240..900 nm grid required")
    bands = []
    for a, b in BANDS:
        mask = (grid >= a - 1e-8) & (grid <= b + 1e-8)
        positive = np.maximum(X[:, mask], 0)
        total = positive.sum(axis=1)
        if np.any(total <= 0):
            raise ValueError("Positive emission in every observed band required")
        floor = np.maximum(np.quantile(positive, 0.95, axis=1) * 0.001, total * 1e-12)
        bands.append((a, b, total, floor))
    result, evidence = {}, []
    for element in CLASSES:
        columns, line_snr = [], []
        for center in plan["lines_nm"][element]:
            d = grid - center
            left, right = (d >= -1) & (d <= -0.5), (d >= 0.5) & (d <= 1)
            core, side = abs(d) <= 0.2, left | right
            if min(left.sum(), right.sum()) < 4 or not core.any():
                raise ValueError("Missing core or sideband support")
            yl, yr = np.median(X[:, left], axis=1), np.median(X[:, right], axis=1)
            xl, xr = np.median(d[left]), np.median(d[right])
            slope = (yr - yl) / (xr - xl)
            residual = X[:, side] - (yl[:, None] + slope[:, None] * (d[side] - xl))
            noise = 1.4826 * np.median(abs(residual - np.median(residual, axis=1)[:, None]), axis=1)
            excess = np.maximum(
                (X[:, core] - (yl[:, None] + slope[:, None] * (d[core] - xl))).max(axis=1), 0
            )
            band = next(v for v in bands if v[0] + 1 <= center <= v[1] - 1)
            snr = np.minimum(excess / np.maximum(noise, band[3]), 1000.0)
            columns.extend((np.log1p(1000 * excess / band[2]), np.log1p(snr)))
            line_snr.append(snr)
        snr_matrix = np.column_stack(line_snr)
        evidence.append(snr_matrix.max(axis=1))
        # Independent stage summaries make neutral/ionized imbalance observable;
        # they are not an LTE temperature or concentration estimate.
        if plan["variant"] != "v2-line-baseline":
            stage_means = []
            for stage in (1,) if plan["variant"] == "continuum-snr-neutral" else (1, 2):
                mask = np.asarray(plan["stages"][element]) == stage
                values = np.log1p(snr_matrix[:, mask])
                mean = values.mean(axis=1) if mask.any() else np.zeros(len(X))
                maximum = values.max(axis=1) if mask.any() else np.zeros(len(X))
                columns.extend((mean, maximum))
                stage_means.append(mean)
            if len(stage_means) == 2:
                columns.append(stage_means[0] - stage_means[1])
        result[element] = np.column_stack(columns)
    if plan["variant"] == "v2-line-baseline":
        result = extract_line_features(X, grid, plan)
    if any(not np.isfinite(v).all() for v in result.values()):
        raise ValueError("Nonfinite feature arithmetic")
    return result, np.column_stack(evidence)


def feature_count(plan: dict, element: str) -> int:
    extras = {"v2-line-baseline": 0, "continuum-snr-neutral": 2, "continuum-snr-ion": 5}
    return 2 * len(plan["lines_nm"][element]) + extras[plan["variant"]]


def _object(pairs: list) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


class LineAdapterV3:
    """Pure numeric logistic model with independently fitted monotonic calibration."""

    score_kind = "operational_composition_probability_platt_development"
    calibrated = True

    def fit(self, features: dict, labels: np.ndarray, ids: list[str], *, C: float):
        validate_labels(labels, ids)
        self.base = LineAdapterV2().fit(features, labels, ids, C=C)
        self.fit_physical_ids = set(ids)
        self.calibration = None
        return self

    def raw_scores(self, features: dict) -> np.ndarray:
        return self.base.scores(features)

    def fit_calibration(self, features: dict, labels: np.ndarray, ids: list[str]):
        labels = validate_labels(labels, ids)
        if set(ids) & getattr(self, "fit_physical_ids", set()):
            raise ValueError("Base fit and calibration physical samples must be disjoint")
        scores = self.raw_scores(features)
        weights = physical_weights(ids)
        states = {}
        for k, element in enumerate(CLASSES):
            known = labels[:, k] >= 0
            y = labels[known, k]
            if set(y.tolist()) != {0, 1}:
                raise ValueError("Calibration requires both known label states")
            z = logit(np.clip(scores[known, k], 1e-8, 1 - 1e-8))
            w = weights[known] / weights[known].sum()

            def objective(theta, z=z, w=w, y=y):
                eta = theta[0] * z + theta[1]
                loss = np.sum(w * (np.logaddexp(0, eta) - y * eta)) + 0.001 * theta[0] ** 2
                error = w * (expit(eta) - y)
                gradient = [np.sum(error * z) + 0.002 * theta[0], np.sum(error)]
                return loss, np.asarray(gradient)

            fitted = minimize(
                objective, [1.0, 0.0], jac=True, method="L-BFGS-B", bounds=[(0, 10), (-20, 20)]
            )
            if not fitted.success or not np.isfinite(fitted.x).all():
                raise ValueError("Calibration optimization failed")
            states[element] = {
                "slope": float(fitted.x[0]),
                "intercept": float(fitted.x[1]),
                "known_physical_support": len(set(np.asarray(ids)[known])),
                "positive_physical_support": len(set(np.asarray(ids)[known & (labels[:, k] == 1)])),
                "negative_physical_support": len(set(np.asarray(ids)[known & (labels[:, k] == 0)])),
            }
        self.calibration = states
        return self

    def probabilities(self, features: dict) -> np.ndarray:
        if self.calibration is None:
            raise ValueError("Independent calibration has not been fitted")
        z = logit(np.clip(self.raw_scores(features), 1e-8, 1 - 1e-8))
        return np.column_stack(
            [
                expit(z[:, k] * self.calibration[c]["slope"] + self.calibration[c]["intercept"])
                for k, c in enumerate(CLASSES)
            ]
        )

    def predict_spectra(self, X: np.ndarray, grid: np.ndarray) -> dict[str, np.ndarray]:
        features, evidence = extract_features(X, grid, self.plan)
        raw, probability = self.raw_scores(features), self.probabilities(features)
        detected = probability >= np.asarray(self.thresholds)
        accepted = np.column_stack(
            [
                (evidence[:, k] >= self.selective[c]["minimum_snr"])
                & (abs(probability[:, k] - self.thresholds[k]) >= self.selective[c]["margin"])
                for k, c in enumerate(CLASSES)
            ]
        )
        return {
            "raw_scores": raw,
            "probabilities": probability,
            "detections": detected,
            "accepted": accepted,
            "decisions": np.where(accepted, detected.astype(int), -1),
            "line_snr": evidence,
        }

    def save(
        self, path: Path, *, plan: dict, thresholds: list[float], selective: dict, provenance: dict
    ) -> None:
        raw = {
            "schema_version": 3,
            "artifact_type": "line_adapter_v3",
            "score_kind": self.score_kind,
            "calibrated": True,
            "qualification": QUALIFICATION,
            "final_test_status": "not_performed",
            "calibration_method": "monotonic_platt_ridge0.001_unbalanced_physical_weight",
            "C": self.base.C,
            "states": self.base.states,
            "calibration": self.calibration,
            "plan": plan,
            "thresholds": thresholds,
            "selective": selective,
            "provenance": provenance,
            "unknown_elements": ["H", "He"],
        }
        self._from_payload(raw)
        raw["identity"] = digest(raw)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("x") as stream:
            stream.write(json.dumps(raw, indent=2, allow_nan=False) + "\n")

    @classmethod
    def _from_payload(cls, raw: dict):
        if (
            not isinstance(raw, dict)
            or raw.get("schema_version") != 3
            or raw.get("artifact_type") != "line_adapter_v3"
            or raw.get("score_kind") != cls.score_kind
            or raw.get("calibrated") is not True
            or raw.get("qualification") != QUALIFICATION
            or raw.get("final_test_status") != "not_performed"
            or raw.get("unknown_elements") != ["H", "He"]
            or raw.get("calibration_method")
            != "monotonic_platt_ridge0.001_unbalanced_physical_weight"
            or not _numeric(raw.get("C"))
            or not 0 < raw["C"] <= 1000
        ):
            raise ValueError("Unsupported v3 artifact contract")
        validate_plan(raw.get("plan"))
        for field in ("states", "calibration", "selective"):
            if not isinstance(raw.get(field), dict) or set(raw[field]) != set(CLASSES):
                raise ValueError("Exact per-element model states required")
        for c in CLASSES:
            state = raw["states"][c]
            if not isinstance(state, dict):
                raise ValueError("Numeric linear state must be an object")
            for key in ("mean", "scale", "coef"):
                values = state.get(key)
                if (
                    not isinstance(values, list)
                    or len(values) != feature_count(raw["plan"], c)
                    or any(not _numeric(v) for v in values)
                    or (key == "scale" and any(v <= 0 for v in values))
                ):
                    raise ValueError("Invalid finite numeric linear state")
            if not _numeric(state.get("intercept")):
                raise ValueError("Invalid linear intercept")
            cal, selective = raw["calibration"][c], raw["selective"][c]
            if not isinstance(cal, dict) or not isinstance(selective, dict):
                raise ValueError("Calibration and selective policy must be objects")
            if (
                not _numeric(cal.get("slope"))
                or not 0 <= cal["slope"] <= 10
                or not _numeric(cal.get("intercept"))
                or not -20 <= cal["intercept"] <= 20
                or not _numeric(selective.get("minimum_snr"))
                or selective["minimum_snr"] not in (0, 2, 5, 10)
                or not _numeric(selective.get("margin"))
                or selective["margin"] not in (0, 0.05, 0.1, 0.2)
            ):
                raise ValueError("Invalid calibration or selective policy")
        thresholds = raw.get("thresholds")
        if (
            not isinstance(thresholds, list)
            or len(thresholds) != 4
            or any(not _numeric(v) or not 0 <= v <= np.nextafter(1.0, np.inf) for v in thresholds)
        ):
            raise ValueError("Invalid operational thresholds")
        provenance = raw.get("provenance")
        if (
            not isinstance(provenance, dict)
            or not isinstance(provenance.get("instrument_id"), str)
            or not provenance["instrument_id"]
            or provenance.get("scope") != "nested_grouped_development_cv_not_independent_test"
        ):
            raise ValueError("Explicit instrument and development provenance required")
        validate_decision_policy(provenance.get("decision_policy"))
        model = cls()
        model.base = LineAdapterV2()
        model.base.C, model.base.states = raw["C"], raw["states"]
        model.calibration, model.plan = raw["calibration"], raw["plan"]
        model.thresholds, model.selective = thresholds, raw["selective"]
        return model

    @classmethod
    def load(cls, path: Path) -> tuple[LineAdapterV3, dict]:
        # One bounded buffer from a no-follow descriptor avoids stat/read races.
        try:
            descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except OSError as exc:
            raise ValueError("Cannot safely open artifact file") from exc
        with os.fdopen(descriptor, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > 2_000_000:
                raise ValueError("Invalid artifact file")
            buffer = stream.read(2_000_001)
        if len(buffer) > 2_000_000:
            raise ValueError("Artifact exceeds size limit")

        def invalid_constant(value):
            raise ValueError(f"Nonfinite JSON constant: {value}")

        raw = json.loads(buffer, object_pairs_hook=_object, parse_constant=invalid_constant)
        if not isinstance(raw, dict):
            raise ValueError("Artifact must be a JSON object")
        identity = raw.pop("identity", None)
        if digest(raw) != identity:
            raise ValueError("Artifact integrity mismatch")
        return cls._from_payload(raw), {**raw, "identity": identity}
