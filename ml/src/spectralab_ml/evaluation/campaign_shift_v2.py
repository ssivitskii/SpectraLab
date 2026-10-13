"""Descriptive campaign diagnostics on already disclosed ChemCam measurements.

Campaign separability is an association, not evidence of instrumental causation:
the campaigns contain different materials and incompletely observed compositions.
No diagnostic score selects a final recognition model or a held-out evaluation.
"""

from __future__ import annotations

import hashlib
import json
import warnings
from collections import Counter
from dataclasses import asdict

import numpy as np
from sklearn.exceptions import ConvergenceWarning
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import balanced_accuracy_score, roc_auc_score
from sklearn.model_selection import StratifiedGroupKFold
from sklearn.preprocessing import StandardScaler

from spectralab_ml.data.reference import ReferenceCatalog

CLASSES = ("Na", "Mg", "Ca", "Fe")
BANDS = {"uv": (241.0, 340.0), "blue": (383.0, 468.0), "visible": (474.0, 900.0)}
SEED = 20261013
# Approximate search anchors only; actual fixed centres come from the pinned NIST
# vacuum export before any measured intensity is read. This is not air conversion.
LINE_ANCHORS = (
    ("Mg_II_279", "Mg", 2, 279.635),
    ("Mg_II_280", "Mg", 2, 280.353),
    ("Mg_I_285", "Mg", 1, 285.296),
    ("Mg_I_516", "Mg", 1, 516.880),
    ("Mg_I_517", "Mg", 1, 517.418),
    ("Mg_I_518", "Mg", 1, 518.510),
    ("Fe_I_404", "Fe", 1, 404.696),
    ("Fe_I_438", "Fe", 1, 438.478),
    ("Fe_I_527", "Fe", 1, 527.188),
)


def feature_plan(catalog: ReferenceCatalog) -> dict:
    """Resolve named transitions from reference wavelengths, without measured data."""
    if catalog.metadata.get("wavelength_medium") != "vacuum":
        raise ValueError("Diagnostic lines require an explicitly vacuum reference")
    lines = []
    for name, element, stage, anchor in LINE_ANCHORS:
        eligible = [
            line
            for line in catalog.lines
            if line.element == element
            and line.ion_stage == stage
            and line.wavelength_medium == "vacuum"
            and abs(line.wavelength_nm - anchor) <= 0.04
        ]
        if not eligible:
            raise ValueError(f"Missing pinned vacuum NIST transition for {name}")
        selected = min(
            eligible, key=lambda line: (abs(line.wavelength_nm - anchor), line.wavelength_nm)
        )
        lines.append({"name": name, **asdict(selected)})
    return {
        "schema_version": 1,
        "grid_nm": {"start": 240.0, "end": 900.0, "step": 0.1},
        "bands_nm": {name: list(bounds) for name, bounds in BANDS.items()},
        "line_core_half_width_nm": 0.2,
        "line_sideband_inner_nm": 0.4,
        "line_sideband_outer_nm": 0.8,
        "background_floor_band_p95_fraction": 1e-6,
        "lines": lines,
        "intensity_processing": (
            "Raw signed mean of shots 6..N; clip only positive-signal features."
        ),
        "line_processing": (
            "Peak of nonnegative core; median of nonnegative sidebands; "
            "floor=max(1e-6*band positive p95,1e-12*band positive maximum); "
            "log1p(core_peak/max(background,floor)); signed contrast "
            "(core_peak-background)/(core_peak+background+floor)."
        ),
        "limitations": [
            "Nearby transitions and matrix effects can affect every line-window feature.",
            "No isolated-line abundance, concentration, or instrument-causal interpretation.",
            "V1 discarded wavelengths below 350 nm, including these strong Mg UV transitions.",
        ],
    }


def extract_features(
    grid: np.ndarray, spectra: np.ndarray, plan: dict
) -> tuple[np.ndarray, list[str]]:
    """Fixed physically interpretable features; unobserved gaps never contribute."""
    grid, spectra = np.asarray(grid), np.asarray(spectra)
    expected = 240.0 + np.arange(6601) * 0.1
    if (
        grid.shape != expected.shape
        or not np.allclose(grid, expected, atol=1e-8, rtol=0)
        or spectra.ndim != 2
        or spectra.shape[1] != len(grid)
        or not len(spectra)
        or not np.isfinite(spectra).all()
    ):
        raise ValueError("Finite spectra on the fixed 240..900 nm, 0.1 nm grid required")
    names, values, areas, floors = [], [], {}, {}

    def add(name, vector):
        names.append(name)
        values.append(vector)

    for band, (start, end) in BANDS.items():
        indices = (grid >= start) & (grid <= end)
        raw = spectra[:, indices]
        positive = np.maximum(raw, 0)
        maximum = positive.max(axis=1)
        if np.any(maximum <= 0):
            raise ValueError(
                f"Every spectrum needs positive observed signal in {band}; "
                f"rows={np.flatnonzero(maximum <= 0).tolist()}"
            )
        positive_area = np.trapezoid(positive, grid[indices], axis=1)
        negative_area = np.trapezoid(np.maximum(-raw, 0), grid[indices], axis=1)
        areas[band] = positive_area
        floors[band] = np.maximum(np.quantile(positive, 0.95, axis=1) * 1e-6, maximum * 1e-12)
        add(f"{band}_log_positive_area", np.log1p(positive_area))
        add(f"{band}_negative_point_fraction", np.mean(raw < 0, axis=1))
        add(f"{band}_negative_area_fraction", negative_area / (negative_area + positive_area))
        add(f"{band}_positive_p95_to_max", np.quantile(positive, 0.95, axis=1) / maximum)
    for numerator in ("uv", "blue"):
        add(
            f"log_area_ratio_{numerator}_to_visible",
            np.log(areas[numerator]) - np.log(areas["visible"]),
        )
    for line in plan["lines"]:
        centre = line["wavelength_nm"]
        band = next(
            (name for name, (start, end) in BANDS.items() if start + 0.8 <= centre <= end - 0.8),
            None,
        )
        if band is None or line["wavelength_medium"] != "vacuum":
            raise ValueError("Line windows must be fully observed and explicitly vacuum")
        distance = np.abs(grid - centre)
        core, side = distance <= 0.2, (distance >= 0.4) & (distance <= 0.8)
        peak = np.maximum(spectra[:, core], 0).max(axis=1)
        background = np.median(np.maximum(spectra[:, side], 0), axis=1)
        floor = floors[band]
        add(f"{line['name']}_log_signal_background", np.log1p(peak / np.maximum(background, floor)))
        add(f"{line['name']}_contrast", (peak - background) / (peak + background + floor))
    features = np.column_stack(values)
    if not np.isfinite(features).all():
        raise ValueError("Diagnostic feature arithmetic must remain finite")
    return features, names


def connected_groups(records: list[dict]) -> list[str]:
    """Join shared physical, family, session and checksum IDs transitively."""
    parent = list(range(len(records)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    seen = {}
    for i, record in enumerate(records):
        for field in ("physical_sample_id", "family_id", "session_id", "input_sha256"):
            value = record.get(field)
            if value is None:
                continue
            key = field, value
            if key in seen:
                parent[find(i)] = find(seen[key])
            else:
                seen[key] = i
    members = {}
    for i, record in enumerate(records):
        members.setdefault(find(i), []).append(record["measurement_id"])
    names = {
        root: "cluster-" + hashlib.sha256(json.dumps(sorted(ids)).encode()).hexdigest()[:16]
        for root, ids in members.items()
    }
    return [names[find(i)] for i in range(len(records))]


def aggregate_physical(records: list[dict], features: np.ndarray) -> tuple[list[dict], np.ndarray]:
    """Each physical material contributes once; average its fixed feature vectors."""
    if len(records) != len(features) or len({r["measurement_id"] for r in records}) != len(records):
        raise ValueError("Unique aligned measurement identities required")
    groups = connected_groups(records)
    physical = {}
    for i, record in enumerate(records):
        physical.setdefault(record["physical_sample_id"], []).append(i)
    rows, vectors = [], []
    for sample, indices in sorted(physical.items()):
        first = records[indices[0]]
        labels = {c: first["labels"][c]["state"] for c in CLASSES}
        if any(
            records[i]["campaign"] != first["campaign"]
            or {c: records[i]["labels"][c]["state"] for c in CLASSES} != labels
            for i in indices
        ):
            raise ValueError("A physical sample cannot have conflicting campaign or labels")
        rows.append(
            {
                "physical_sample_id": sample,
                "cluster_id": groups[indices[0]],
                "campaign": first["campaign"],
                "labels": labels,
                "measurement_ids": [records[i]["measurement_id"] for i in indices],
                "replicates": len(indices),
            }
        )
        vectors.append(np.mean(features[indices], axis=0))
    return rows, np.asarray(vectors)


def descriptive_summaries(rows: list[dict], features: np.ndarray, names: list[str]) -> dict:
    """Physical-sample median/IQR by campaign and individual composition state."""
    result = {}
    for campaign in sorted({row["campaign"] for row in rows}):
        scopes = {"all": [i for i, row in enumerate(rows) if row["campaign"] == campaign]}
        scopes.update(
            {
                f"{element}:{state}": [
                    i
                    for i, row in enumerate(rows)
                    if row["campaign"] == campaign and row["labels"][element] == state
                ]
                for element in CLASSES
                for state in ("positive", "negative", "unknown")
            }
        )
        result[campaign] = {}
        for scope, indices in scopes.items():
            result[campaign][scope] = {
                "physical_samples": len(indices),
                "clusters": len({rows[i]["cluster_id"] for i in indices}),
                "features": {
                    name: dict(
                        zip(
                            ("q25", "median", "q75"),
                            np.quantile(features[indices, j], [0.25, 0.5, 0.75]).tolist(),
                            strict=True,
                        )
                    )
                    if indices
                    else None
                    for j, name in enumerate(names)
                },
            }
    return result


def domain_separability(rows: list[dict], features: np.ndarray, *, seed: int = SEED) -> dict:
    """Fixed grouped-CV domain classifier, fitted only on each training fold.

    Features are already physical averages. Every material has unit metric weight;
    class_weight balances the two campaign labels in the training objective only.
    """
    features = np.asarray(features)
    campaigns = sorted({row["campaign"] for row in rows})
    if (
        len(campaigns) != 2
        or features.ndim != 2
        or len(features) != len(rows)
        or not np.isfinite(features).all()
    ):
        raise ValueError("Two campaigns and aligned finite physical-sample features required")
    y = np.asarray([campaigns.index(row["campaign"]) for row in rows])
    groups = np.asarray([row["cluster_id"] for row in rows])
    n_splits = min(5, *(len(set(groups[y == label])) for label in (0, 1)))
    support = {
        campaign: {
            "physical_samples": int(np.sum(y == label)),
            "clusters": len(set(groups[y == label])),
        }
        for label, campaign in enumerate(campaigns)
    }
    if n_splits < 2:
        return {"status": "insufficient_groups", "physical_samples": len(rows), "support": support}
    splitter = StratifiedGroupKFold(n_splits=n_splits, shuffle=True, random_state=seed)
    folds = list(splitter.split(features, y, groups))
    # Never search splits using fitted performance, or drop a one-campaign fold.
    if any(len(np.unique(y[part])) != 2 for train, test in folds for part in (train, test)):
        return {"status": "unsupported_fixed_split", "n_splits": n_splits, "support": support}
    probabilities = np.full(len(rows), np.nan)
    reports = []
    for index, (train, test) in enumerate(folds):
        if set(groups[train]) & set(groups[test]):
            raise ValueError("Connected groups crossed a domain-CV boundary")
        scaler = StandardScaler().fit(features[train])
        estimator = LogisticRegression(
            C=1.0, class_weight="balanced", max_iter=3000, random_state=seed
        )
        with warnings.catch_warnings():
            warnings.simplefilter("error", ConvergenceWarning)
            estimator.fit(scaler.transform(features[train]), y[train])
        scores = estimator.predict_proba(scaler.transform(features[test]))[:, 1]
        probabilities[test] = scores
        reports.append(
            {
                "fold": index,
                "train_physical_ids": [rows[i]["physical_sample_id"] for i in train],
                "validation_physical_ids": [rows[i]["physical_sample_id"] for i in test],
                "train_clusters": sorted(set(groups[train])),
                "validation_clusters": sorted(set(groups[test])),
                "train_campaign_support": dict(Counter(rows[i]["campaign"] for i in train)),
                "validation_campaign_support": dict(Counter(rows[i]["campaign"] for i in test)),
                "roc_auc": float(roc_auc_score(y[test], scores)),
                "balanced_accuracy": float(balanced_accuracy_score(y[test], scores >= 0.5)),
            }
        )
    if not np.isfinite(probabilities).all():
        raise ValueError("Every physical material must receive exactly one out-of-fold score")
    return {
        "status": "completed",
        "method": "Fixed C=1 balanced logistic, training-fold StandardScaler, StratifiedGroupKFold",
        "seed": seed,
        "positive_campaign": campaigns[1],
        "n_splits": n_splits,
        "physical_samples": len(rows),
        "support": support,
        "clusters": len(set(groups)),
        "roc_auc": float(roc_auc_score(y, probabilities)),
        "balanced_accuracy": float(balanced_accuracy_score(y, probabilities >= 0.5)),
        "folds": reports,
        "out_of_fold": [
            {
                "physical_sample_id": row["physical_sample_id"],
                "campaign": row["campaign"],
                "score": float(score),
            }
            for row, score in zip(rows, probabilities, strict=True)
        ],
        "interpretation": (
            "Campaign association only: different compositions/materials confound "
            "instrumental causes."
        ),
    }


def composition_features(rows: list[dict]) -> np.ndarray:
    """Coarse label-only confounding comparator; not full composition adjustment."""
    return np.asarray(
        [
            [
                float(row["labels"][element] == state)
                for element in CLASSES
                for state in ("positive", "negative", "unknown")
            ]
            for row in rows
        ]
    )


def conditional_domain_separability(rows: list[dict], features: np.ndarray) -> dict:
    """Same diagnostic within fixed Mg/Fe strata; not full matrix adjustment."""
    reports = {}
    for element in ("Mg", "Fe"):
        for state in ("positive", "negative"):
            indices = [i for i, row in enumerate(rows) if row["labels"][element] == state]
            selected = [rows[i] for i in indices]
            if len({row["campaign"] for row in selected}) < 2:
                reports[f"{element}:{state}"] = {
                    "status": "insufficient_campaign_support",
                    "physical_samples": len(selected),
                }
            else:
                reports[f"{element}:{state}"] = domain_separability(selected, features[indices])
    return reports
