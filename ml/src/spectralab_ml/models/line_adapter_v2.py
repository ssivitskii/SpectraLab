"""Target-specific local line evidence with training-only balanced linear models.

This experimental v2 model includes UV lines. It deliberately does not estimate
concentrations or calibrate probability, and does not reuse v1 artifact schemas.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path

import numpy as np
from scipy.special import expit
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler

from spectralab_ml.data.reference import ReferenceCatalog

BANDS = [[241.0, 340.0], [383.0, 468.0], [474.0, 900.0]]
CLASSES = ["Na", "Mg", "Ca", "Fe"]


def digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


def line_plan(catalog: ReferenceCatalog, *, include_uv: bool = True) -> dict:
    """Choose separated strongest reference lines; no measured spectrum is consulted."""
    selected = {}
    for element in CLASSES:
        centers = []
        # Keep exactly the same visible evidence in both plans; UV is an additive ablation.
        stages = [(stage, BANDS[1:]) for stage in (1, 2)]
        if include_uv:
            stages.extend((stage, BANDS[:1]) for stage in (1, 2))
        for stage, selected_bands in stages:
            candidates = sorted(
                [
                    line
                    for line in catalog.lines
                    if line.element == element
                    and line.ion_stage == stage
                    and line.relative_intensity
                    and line.wavelength_medium == "vacuum"
                    and any(a + 1.05 <= line.wavelength_nm <= b - 1.05 for a, b in selected_bands)
                ],
                key=lambda line: (-line.relative_intensity, line.wavelength_nm),
            )
            kept = []
            for line in candidates:
                if all(abs(line.wavelength_nm - center) >= 0.6 for center in centers):
                    centers.append(line.wavelength_nm)
                    kept.append(line.wavelength_nm)
                    if len(kept) == 8:
                        break
        if not centers:
            raise ValueError(f"No supported vacuum reference lines for {element}")
        selected[element] = sorted(centers)
    return {
        "schema_version": 2,
        "class_order": CLASSES,
        "wavelength_medium": "vacuum",
        "bands": BANDS,
        "lines_nm": selected,
        "core_half_width_nm": 0.2,
        "sideband_inner_nm": 0.5,
        "sideband_outer_nm": 1.0,
        "baseline": "sideband lower quartile",
        "noise_floor": "1e-6 band positive p95",
        "features": [
            "log1p(1000*positive_peak_excess/band_positive_sum)",
            "log1p(positive_peak_excess/(positive_sideband_mean+floor))",
        ],
        "reference_content_sha256": catalog.metadata["content_sha256"],
        "include_uv": include_uv,
    }


def extract_line_features(X: np.ndarray, grid: np.ndarray, plan: dict) -> dict[str, np.ndarray]:
    X, grid = np.asarray(X, float), np.asarray(grid, float)
    if (
        grid.ndim != 1
        or len(grid) < 2
        or not np.isfinite(grid).all()
        or not np.all(np.diff(grid) > 0)
        or X.ndim != 2
        or X.shape[1] != len(grid)
        or not np.isfinite(X).all()
    ):
        raise ValueError("Finite aligned spectra and increasing grid required")
    bands = []
    for a, b in plan["bands"]:
        mask = (grid >= a - 1e-8) & (grid <= b + 1e-8)
        if mask.sum() < 10 or np.max(np.diff(grid[mask])) > 0.101:
            raise ValueError("Line features require observed bands on a dense <=0.1nm grid")
        positive = np.maximum(X[:, mask], 0)
        total = positive.sum(axis=1)
        if np.any(total <= 0):
            raise ValueError("No positive emission in an observed band; cannot classify")
        bands.append(
            (
                a,
                b,
                total,
                np.maximum(np.quantile(positive, 0.95, axis=1) * 1e-6, np.finfo(float).tiny),
            )
        )
    result = {}
    for element in plan["class_order"]:
        columns = []
        for center in plan["lines_nm"][element]:
            distance = np.abs(grid - center)
            core = distance <= plan["core_half_width_nm"]
            side = (distance >= plan["sideband_inner_nm"]) & (distance <= plan["sideband_outer_nm"])
            if not core.any() or side.sum() < 4:
                raise ValueError("Missing line core or sidebands")
            band = next((v for v in bands if v[0] + 1 <= center <= v[1] - 1), None)
            if band is None:
                raise ValueError("Line window crosses an unobserved interval")
            side_values = X[:, side]
            excess = np.maximum(X[:, core].max(axis=1) - np.quantile(side_values, 0.25, axis=1), 0)
            columns.extend(
                [
                    np.log1p(1000 * excess / band[2]),
                    np.log1p(excess / (np.maximum(side_values, 0).mean(axis=1) + band[3])),
                ]
            )
        result[element] = np.column_stack(columns)
    if not all(np.isfinite(values).all() for values in result.values()):
        raise ValueError("Nonfinite line feature")
    return result


class LineAdapterV2:
    score_kind = "line_evidence_balanced_logistic_uncalibrated"
    calibrated = False

    def fit(
        self, features: dict, labels: np.ndarray, sample_ids: list[str], *, C: float
    ) -> LineAdapterV2:
        labels = np.asarray(labels)
        if labels.shape != (len(sample_ids), len(CLASSES)) or not np.isin(labels, [-1, 0, 1]).all():
            raise ValueError("Aligned three-state labels required")
        if not np.isfinite(C) or C <= 0:
            raise ValueError("Positive finite C required")
        self.states = {}
        for k, element in enumerate(CLASSES):
            values = np.asarray(features[element], float)
            if values.ndim != 2 or len(values) != len(labels) or not np.isfinite(values).all():
                raise ValueError("Finite aligned feature matrix required")
            known = labels[:, k] >= 0
            y = labels[known, k]
            if set(y.tolist()) != {0, 1}:
                raise ValueError("Both positive and negative training support required")
            groups = np.asarray(sample_ids)[known]
            counts = Counter(groups)
            for group in counts:
                if len(set(y[groups == group])) != 1:
                    raise ValueError("Conflicting physical-sample labels")
            weights = np.asarray([1 / counts[g] for g in groups])
            # Equal total physical weight for each class; no tune/test contribution.
            balanced = weights.copy()
            for state in (0, 1):
                balanced[y == state] *= weights.sum() / (2 * weights[y == state].sum())
            scaler = StandardScaler().fit(values[known], sample_weight=balanced)
            estimator = LogisticRegression(C=C, solver="lbfgs", max_iter=3000, tol=1e-8)
            estimator.fit(scaler.transform(values[known]), y, sample_weight=balanced)
            self.states[element] = {
                "mean": scaler.mean_.tolist(),
                "scale": scaler.scale_.tolist(),
                "coef": estimator.coef_[0].tolist(),
                "intercept": float(estimator.intercept_[0]),
                "positive_materials": len(set(groups[y == 1])),
                "negative_materials": len(set(groups[y == 0])),
            }
        self.C = C
        return self

    def scores(self, features: dict) -> np.ndarray:
        columns = []
        for element in CLASSES:
            state = self.states[element]
            X = np.asarray(features[element], float)
            if X.ndim != 2 or X.shape[1] != len(state["mean"]) or not np.isfinite(X).all():
                raise ValueError("Invalid inference features")
            columns.append(
                expit(((X - state["mean"]) / state["scale"]) @ state["coef"] + state["intercept"])
            )
        return np.column_stack(columns)

    def save(self, path: Path, *, plan: dict, thresholds: list[float], provenance: dict) -> None:
        payload = {
            "schema_version": 2,
            "artifact_type": "line_adapter_v2",
            "score_kind": self.score_kind,
            "calibrated": False,
            "C": self.C,
            "plan": plan,
            "states": self.states,
            "thresholds": thresholds,
            "provenance": provenance,
            "qualification": "development_only_no_independent_blank_validation",
        }
        payload["identity"] = digest(payload)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("x") as stream:
            stream.write(json.dumps(payload, indent=2, allow_nan=False) + "\n")

    @classmethod
    def load(cls, path: Path) -> tuple[LineAdapterV2, dict]:
        if path.is_symlink() or path.stat().st_size > 2_000_000:
            raise ValueError("Invalid artifact file")
        raw = json.loads(path.read_text())
        identity = raw.pop("identity", None)
        if digest(raw) != identity or raw.get("schema_version") != 2:
            raise ValueError("Artifact integrity or schema mismatch")
        if raw.get("artifact_type") != "line_adapter_v2" or raw.get("calibrated") is not False:
            raise ValueError("Unsupported artifact contract")
        if set(raw["states"]) != set(CLASSES) or raw["plan"]["class_order"] != CLASSES:
            raise ValueError("Invalid class order")
        for element, state in raw["states"].items():
            count = 2 * len(raw["plan"]["lines_nm"][element])
            for key in ("mean", "scale", "coef"):
                values = np.asarray(state[key], float)
                if values.shape != (count,) or not np.isfinite(values).all():
                    raise ValueError("Invalid numeric model state")
            if np.any(np.asarray(state["scale"]) <= 0) or not np.isfinite(state["intercept"]):
                raise ValueError("Invalid scaling or intercept")
        thresholds = np.asarray(raw["thresholds"], float)
        if (
            thresholds.shape != (4,)
            or not np.isfinite(thresholds).all()
            or np.any((thresholds < 0) | (thresholds > np.nextafter(1.0, np.inf)))
        ):
            raise ValueError("Invalid thresholds")
        model = cls()
        model.states, model.C = raw["states"], raw["C"]
        return model, {**raw, "identity": identity}
