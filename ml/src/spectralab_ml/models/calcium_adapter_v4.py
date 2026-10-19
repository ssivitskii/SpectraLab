"""Adaptive-development calcium classifier; frozen v3 remains the other elements."""

from __future__ import annotations

import copy
import json
import os
import stat
from pathlib import Path

import numpy as np
from scipy.optimize import minimize
from scipy.special import expit, logit
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler
from sklearn.svm import SVC

from spectralab_ml.models.line_adapter_v3 import (
    CLASSES,
    QUALIFICATION,
    LineAdapterV3,
    _numeric,
    _object,
    digest,
    extract_features,
    physical_weights,
    validate_plan,
)

FAMILIES = ("full-v3-ca", "blue-ca-agreement", "full-ca-agreement")
BLUE_LINES = [393.477, 396.959, 422.792, 445.603]
BLUE_STAGES = [2, 2, 1, 1]
SCOPE = "adaptive_grouped_development_not_independent_final"


def validate_calcium_plan(plan: dict) -> None:
    validate_plan(plan)
    if plan["variant"] != "continuum-snr-ion":
        raise ValueError("Calcium v4 requires the fixed continuum/SNR I+II v3 feature plan")
    for center, stage in zip(BLUE_LINES, BLUE_STAGES, strict=True):
        if center not in plan["lines_nm"]["Ca"]:
            raise ValueError("Required reference calcium diagnostic line missing")
        position = plan["lines_nm"]["Ca"].index(center)
        if plan["stages"]["Ca"][position] != stage:
            raise ValueError("Calcium reference ion stage mismatch")


def calcium_features(X: np.ndarray, grid: np.ndarray, plan: dict) -> dict[str, np.ndarray]:
    """Reference-defined transforms; no sample IDs or campaign metadata enter."""
    validate_calcium_plan(plan)
    full = extract_features(X, grid, plan)[0]["Ca"]
    blue_plan = copy.deepcopy(plan)
    blue_plan["lines_nm"]["Ca"], blue_plan["stages"]["Ca"] = BLUE_LINES, BLUE_STAGES
    blue = extract_features(X, grid, blue_plan)[0]["Ca"]
    pairwise = blue[:, :8].reshape(len(X), 4, 2)
    columns, means = [], []
    for stage in (1, 2):
        values = pairwise[:, np.asarray(BLUE_STAGES) == stage, :]
        mean = values.mean(axis=1)
        means.append(mean)
        columns.extend([mean, values.min(axis=1), values.max(axis=1), values.std(axis=1)])
    columns.append(means[0] - means[1])
    columns.extend(
        [(pairwise[:, :, 1] >= np.log1p(cut)).sum(axis=1)[:, None] for cut in (2, 5, 10)]
    )
    agreement = np.column_stack(columns)
    return {
        "full-v3-ca": full,
        "blue-ca-agreement": np.column_stack([blue, agreement]),
        "full-ca-agreement": np.column_stack([full, agreement]),
    }


def feature_dimension(plan: dict, family: str) -> int:
    full = 2 * len(plan["lines_nm"]["Ca"]) + 5
    return {"full-v3-ca": full, "blue-ca-agreement": 34, "full-ca-agreement": full + 21}[family]


def check_labels(y: np.ndarray, ids: list[str]) -> np.ndarray:
    y = np.asarray(y)
    if y.shape != (len(ids),) or not np.isin(y, [-1, 0, 1]).all():
        raise ValueError("Aligned calcium three-state labels required")
    physical_weights(ids)
    for name in set(ids):
        if len(set(y[np.asarray(ids) == name])) != 1:
            raise ValueError("Conflicting calcium labels for physical replicates")
    return y


class CalciumClassifier:
    def fit(self, X, y, ids, *, learner, C, gamma_multiplier=1.0):
        y = check_labels(y, ids)
        X = np.asarray(X, float)
        if X.ndim != 2 or len(X) != len(y) or not np.isfinite(X).all():
            raise ValueError("Finite aligned calcium features required")
        if learner not in ("logistic", "rbf") or not 0 < C <= 10:
            raise ValueError("Unsupported calcium learner")
        known = y >= 0
        truth = y[known]
        if set(truth) != {0, 1}:
            raise ValueError("Both known calcium label states required")
        weights = physical_weights(ids)[known]
        balanced = weights.copy()
        for label in (0, 1):
            balanced[truth == label] *= weights.sum() / (2 * weights[truth == label].sum())
        scaler = StandardScaler().fit(X[known], sample_weight=balanced)
        values = scaler.transform(X[known])
        state = {
            "learner": learner,
            "C": C,
            "mean": scaler.mean_.tolist(),
            "scale": scaler.scale_.tolist(),
        }
        if learner == "logistic":
            estimator = LogisticRegression(C=C, max_iter=3000, tol=1e-8)
            estimator.fit(values, truth, sample_weight=balanced)
            state.update(coef=estimator.coef_[0].tolist(), intercept=float(estimator.intercept_[0]))
        else:
            gamma = gamma_multiplier / X.shape[1]
            estimator = SVC(C=C, gamma=gamma, kernel="rbf", tol=1e-8)
            estimator.fit(values, truth, sample_weight=balanced)
            state.update(
                support_vectors=estimator.support_vectors_.tolist(),
                dual_coef=estimator.dual_coef_[0].tolist(),
                intercept=float(estimator.intercept_[0]),
                gamma=gamma,
            )
        self.state, self.calibration, self.fit_ids = state, None, set(ids)
        return self

    def raw_scores(self, X):
        X = np.asarray(X, float)
        s = self.state
        if X.ndim != 2 or X.shape[1] != len(s["mean"]) or not np.isfinite(X).all():
            raise ValueError("Finite aligned calcium inference features required")
        z = (X - np.asarray(s["mean"])) / np.asarray(s["scale"])
        if s["learner"] == "logistic":
            eta = z @ np.asarray(s["coef"]) + s["intercept"]
        else:
            vectors = np.asarray(s["support_vectors"])
            distance = np.maximum(
                (z * z).sum(axis=1)[:, None]
                + (vectors * vectors).sum(axis=1)[None, :]
                - 2 * z @ vectors.T,
                0,
            )
            eta = np.exp(-s["gamma"] * distance) @ np.asarray(s["dual_coef"]) + s["intercept"]
        if not np.isfinite(eta).all():
            raise ValueError("Nonfinite calcium decision arithmetic")
        return expit(eta)

    def fit_calibration(self, X, y, ids):
        y = check_labels(y, ids)
        if not hasattr(self, "fit_ids") or set(ids) & self.fit_ids:
            raise ValueError("Fresh disjoint fit and calibration identities required")
        known = y >= 0
        if set(y[known]) != {0, 1}:
            raise ValueError("Both calcium calibration states required")
        z = logit(np.clip(self.raw_scores(X)[known], 1e-8, 1 - 1e-8))
        w = physical_weights(ids)[known]
        w /= w.sum()
        truth = y[known]

        def objective(theta):
            eta = theta[0] * z + theta[1]
            loss = np.sum(w * (np.logaddexp(0, eta) - truth * eta)) + 0.001 * theta[0] ** 2
            error = w * (expit(eta) - truth)
            return loss, np.array([np.sum(error * z) + 0.002 * theta[0], np.sum(error)])

        fit = minimize(
            objective, [1.0, 0.0], jac=True, method="L-BFGS-B", bounds=[(0, 10), (-20, 20)]
        )
        if not fit.success or not np.isfinite(fit.x).all():
            raise ValueError("Calcium calibration optimization failed")
        self.calibration = {"slope": float(fit.x[0]), "intercept": float(fit.x[1])}
        return self

    def probabilities(self, X):
        if self.calibration is None:
            raise ValueError("Calcium calibration missing")
        z = logit(np.clip(self.raw_scores(X), 1e-8, 1 - 1e-8))
        return expit(z * self.calibration["slope"] + self.calibration["intercept"])

    def payload(self):
        return {"state": self.state, "calibration": self.calibration}

    @classmethod
    def from_payload(cls, value, dimension):
        if not isinstance(value, dict) or not isinstance(value.get("state"), dict):
            raise ValueError("Numeric calcium state required")
        s, cal = value["state"], value.get("calibration")
        if (
            s.get("learner") not in ("logistic", "rbf")
            or not _numeric(s.get("C"))
            or not 0 < s["C"] <= 10
        ):
            raise ValueError("Unsupported calcium learner state")
        keys = ["mean", "scale"] + (["coef"] if s["learner"] == "logistic" else [])
        for name in keys:
            v = s.get(name)
            if not isinstance(v, list) or len(v) != dimension or any(not _numeric(q) for q in v):
                raise ValueError("Invalid calcium numeric dimensions")
        if any(v <= 0 for v in s["scale"]) or not _numeric(s.get("intercept")):
            raise ValueError("Invalid calcium scaling/intercept")
        if s["learner"] == "rbf":
            vectors, dual = s.get("support_vectors"), s.get("dual_coef")
            if (
                not isinstance(vectors, list)
                or not 1 <= len(vectors) <= 256
                or not isinstance(dual, list)
                or len(dual) != len(vectors)
                or any(
                    not isinstance(v, list)
                    or len(v) != dimension
                    or any(not _numeric(x) for x in v)
                    for v in vectors
                )
                or any(not _numeric(v) for v in dual)
                or not _numeric(s.get("gamma"))
                or not 0 < s["gamma"] <= 10
            ):
                raise ValueError("Invalid numeric RBF state")
        if (
            not isinstance(cal, dict)
            or not _numeric(cal.get("slope"))
            or not 0 <= cal["slope"] <= 10
            or not _numeric(cal.get("intercept"))
            or not -20 <= cal["intercept"] <= 20
        ):
            raise ValueError("Invalid calcium calibration")
        model = cls()
        model.state, model.calibration = s, cal
        return model


class CalciumAdapterV4:
    """Safe numeric composition: original v3 Na/Mg/Fe plus new unconditional Ca."""

    score_kind = "adaptive_calcium_probability_platt_development"
    calibrated = True

    def predict_spectra(self, X, grid):
        result = self.original.predict_spectra(X, grid)
        features = calcium_features(X, grid, self.original.plan)[self.family]
        p = self.calcium.probabilities(features)
        result["probabilities"][:, 2], result["raw_scores"][:, 2] = (
            p,
            self.calcium.raw_scores(features),
        )
        result["detections"][:, 2] = p >= self.thresholds[2]
        result["accepted"][:, 2] = True
        result["decisions"][:, 2] = result["detections"][:, 2].astype(int)
        return result

    @classmethod
    def from_payload(cls, raw):
        if (
            not isinstance(raw, dict)
            or raw.get("schema_version") != 4
            or raw.get("artifact_type") != "calcium_adapter_v4"
            or raw.get("scope") != SCOPE
            or raw.get("qualification") != QUALIFICATION
            or raw.get("final_test_status") != "not_performed"
            or raw.get("calcium_feature_family") not in FAMILIES
            or raw.get("calcium_selective_refusal") is not False
            or raw.get("class_order") != CLASSES
            or raw.get("calibrated") is not True
            or raw.get("score_kind") != cls.score_kind
            or raw.get("unknown_elements") != ["H", "He"]
        ):
            raise ValueError("Unsupported adaptive calcium artifact")
        original = copy.deepcopy(raw.get("original_v3_profile"))
        if not isinstance(original, dict) or original.pop("identity", None) != digest(original):
            raise ValueError("Embedded original v3 integrity mismatch")
        base = LineAdapterV3._from_payload(original)
        validate_calcium_plan(base.plan)
        provenance = raw.get("provenance")
        if (
            not isinstance(provenance, dict)
            or provenance.get("instrument_id") != original["provenance"]["instrument_id"]
            or provenance.get("decision_policy") != original["provenance"]["decision_policy"]
        ):
            raise ValueError("Calcium instrument or operational policy differs from original")
        family = raw["calcium_feature_family"]
        calcium = CalciumClassifier.from_payload(
            raw.get("calcium"), feature_dimension(base.plan, family)
        )
        threshold = raw.get("calcium_threshold")
        if not _numeric(threshold) or not 0 <= threshold <= np.nextafter(1.0, np.inf):
            raise ValueError("Invalid calcium threshold")
        model = cls()
        model.original, model.calcium, model.family = base, calcium, family
        model.plan, model.thresholds = base.plan, list(base.thresholds)
        model.thresholds[2] = threshold
        return model

    @classmethod
    def save(cls, path, *, original_profile, calcium, family, threshold, provenance):
        raw = {
            "schema_version": 4,
            "artifact_type": "calcium_adapter_v4",
            "scope": SCOPE,
            "qualification": QUALIFICATION,
            "final_test_status": "not_performed",
            "calcium_feature_family": family,
            "calcium_selective_refusal": False,
            "class_order": CLASSES,
            "calibrated": True,
            "score_kind": cls.score_kind,
            "unknown_elements": ["H", "He"],
            "original_v3_profile": original_profile,
            "calcium": calcium.payload(),
            "calcium_threshold": float(threshold),
            "provenance": {
                **provenance,
                "instrument_id": original_profile["provenance"]["instrument_id"],
                "decision_policy": original_profile["provenance"]["decision_policy"],
            },
        }
        cls.from_payload(raw)
        raw["identity"] = digest(raw)
        with Path(path).open("x") as stream:
            json.dump(raw, stream, indent=2, allow_nan=False)
            stream.write("\n")

    @classmethod
    def load(cls, path):
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > 5_000_000:
                raise ValueError("Invalid calcium artifact file")
            buffer = stream.read(5_000_001)
        if len(buffer) > 5_000_000:
            raise ValueError("Calcium artifact exceeds size limit")
        raw = json.loads(buffer, object_pairs_hook=_object)
        if not isinstance(raw, dict):
            raise ValueError("Calcium artifact must be an object")
        identity = raw.pop("identity", None)
        if digest(raw) != identity:
            raise ValueError("Calcium artifact integrity mismatch")
        return cls.from_payload(raw), {**raw, "identity": identity}
