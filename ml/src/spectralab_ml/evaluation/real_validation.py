"""Locked synthetic-to-real validation with explicit observation windows and labels.

This research adapter never infers absent elements from missing composition data.
Unobserved wavelengths are excluded from the estimator, not interpolated as signal.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

import numpy as np

from spectralab_ml.datasets import DatasetBundle, DatasetSplit, split_base_sample_ids
from spectralab_ml.preprocessing import validate_spectrum_arrays


def object_sha256(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


def observation_mask(grid: np.ndarray, intervals: list[list[float]]) -> np.ndarray:
    if grid.ndim != 1 or len(grid) < 2 or not np.isfinite(grid).all():
        raise ValueError("Invalid model wavelength grid")
    if np.any(np.diff(grid) <= 0):
        raise ValueError("Model wavelengths must increase")
    mask = np.zeros(grid.size, dtype=bool)
    previous = -np.inf
    for start, end in intervals:
        if not np.isfinite([start, end]).all() or start >= end or start <= previous:
            raise ValueError("Observation windows must be finite, ordered and disjoint")
        if start < grid[0] or end > grid[-1]:
            raise ValueError("Observation window outside model grid")
        mask |= (grid >= start) & (grid <= end)
        previous = end
    if not np.any(mask):
        raise ValueError("At least one observed feature is required")
    return mask


def interpolate_observed(
    wavelengths: np.ndarray,
    intensity: np.ndarray,
    grid: np.ndarray,
    intervals: list[list[float]],
    *,
    max_gap_nm: float,
) -> np.ndarray:
    validate_spectrum_arrays(wavelengths, intensity)
    if not np.isfinite(max_gap_nm) or max_gap_nm <= 0:
        raise ValueError("Maximum observed gap must be positive")
    mask = observation_mask(grid, intervals)
    features = np.zeros(grid.size)
    for start, end in intervals:
        # Include bracketing samples but reject missing coverage or an internal gap.
        left = np.searchsorted(wavelengths, start, side="right") - 1
        right = np.searchsorted(wavelengths, end, side="left")
        if left < 0 or right >= len(wavelengths):
            raise ValueError("Real spectrum does not cover an observation window")
        wave = wavelengths[left : right + 1]
        if np.any(np.diff(wave) > max_gap_nm):
            raise ValueError("Refusing interpolation across an unobserved spectral gap")
        selected = (grid >= start) & (grid <= end)
        features[selected] = np.interp(grid[selected], wave, intensity[left : right + 1])
    if not np.max(np.abs(features[mask])):
        raise ValueError("Observed windows contain zero signal")
    return features


def normalize_observed(features: np.ndarray, mask: np.ndarray) -> np.ndarray:
    if features.ndim != 2 or features.shape[1] != len(mask) or mask.dtype != bool:
        raise ValueError("Features and observation mask are incompatible")
    if not np.any(mask) or not np.isfinite(features).all():
        raise ValueError("Finite features and nonempty observation mask required")
    observed = np.asarray(features[:, mask], dtype=float)
    scale = np.max(np.abs(observed), axis=1, keepdims=True)
    # A zero probe is allowed for trusted model loading; real admission rejects it.
    return np.divide(observed, scale, out=np.zeros_like(observed), where=scale > 0)


@dataclass
class ObservedWindowEstimator:
    base: Any
    mask: np.ndarray

    @property
    def score_kind(self) -> str:
        return self.base.score_kind

    @property
    def calibrated(self) -> bool:
        return self.base.calibrated

    def fit(self, X: np.ndarray, y: np.ndarray) -> ObservedWindowEstimator:
        self.base.fit(normalize_observed(X, self.mask), y)
        return self

    def scores(self, X: np.ndarray) -> np.ndarray:
        return self.base.scores(normalize_observed(X, self.mask))


def make_locked_synthetic_dataset(
    templates: np.ndarray,
    class_order: tuple[str, ...],
    reference_metadata: dict,
    *,
    seed: int,
    base_samples: int,
    variations: int,
    mask: np.ndarray,
) -> DatasetBundle:
    """Predeclared linear template mixture experiment; no real input accepted."""
    if base_samples < 30 or variations < 1:
        raise ValueError("Insufficient synthetic development data")
    rng = np.random.default_rng(seed)
    ids = [f"synthetic-{seed}-{index:04d}" for index in range(base_samples)]
    split = split_base_sample_ids(ids, seed)
    features = {key: [] for key in split}
    labels = {key: [] for key in split}
    groups = {key: [] for key in split}
    records = []
    for index, base_id in enumerate(ids):
        count = index % 3 + 1
        selected = rng.choice(len(class_order), size=count, replace=False)
        weights = rng.uniform(0.35, 1.2, size=count)
        clean = weights @ templates[selected]
        truth = np.zeros(len(class_order), dtype=int)
        truth[selected] = 1
        target = next(name for name, values in split.items() if base_id in values)
        for variation in range(variations):
            snr = float(rng.uniform(20, 40))
            noise_std = np.sqrt(np.mean(clean[mask] ** 2)) / 10 ** (snr / 20)
            spectrum = clean + rng.normal(0, noise_std, size=clean.size)
            features[target].append(spectrum)
            labels[target].append(truth)
            groups[target].append(base_id)
            records.append(
                {
                    "base_sample_id": base_id,
                    "variation": variation,
                    "split": target,
                    "elements": [class_order[k] for k in selected],
                    "component_weights": weights.tolist(),
                    "snr_db": snr,
                }
            )
    splits = {
        key: DatasetSplit(np.asarray(features[key]), np.asarray(labels[key]), tuple(groups[key]))
        for key in split
    }
    return DatasetBundle(
        **splits,
        class_order=class_order,
        dataset_hash=object_sha256(
            {"records": records, "reference": reference_metadata, "mask": mask.tolist()}
        ),
        split_hash=object_sha256({key: sorted(value) for key, value in split.items()}),
        records=records,
        reference_metadata=reference_metadata,
    )


def positive_recall_report(
    detections: np.ndarray,
    known_positive: np.ndarray,
    sample_ids: list[str],
    class_order: tuple[str, ...],
    *,
    bootstrap_repeats: int = 1000,
    bootstrap_seed: int = 20261013,
) -> dict:
    if detections.dtype != bool or known_positive.dtype != bool:
        raise ValueError("Detection and positive masks must be boolean")
    if detections.shape != known_positive.shape or detections.shape != (
        len(sample_ids),
        len(class_order),
    ):
        raise ValueError("Labels, detections and physical sample IDs must align")
    if not sample_ids or bootstrap_repeats < 2:
        raise ValueError("Physical samples and bootstrap repeats are required")
    unique = sorted(set(sample_ids))
    detected = np.zeros((len(unique), len(class_order)))
    positive = np.zeros_like(detected, dtype=bool)
    for index, sample in enumerate(unique):
        rows = np.asarray([s == sample for s in sample_ids])
        if not np.all(known_positive[rows] == known_positive[rows][0]):
            raise ValueError("Replicates have conflicting physical sample labels")
        detected[index] = detections[rows].mean(axis=0)
        positive[index] = known_positive[rows][0]
    rng = np.random.default_rng(bootstrap_seed)
    samples = rng.integers(0, len(unique), size=(bootstrap_repeats, len(unique)))

    def interval(values: np.ndarray, weights: np.ndarray) -> dict:
        support = int(weights.sum())
        if not support:
            return {"recall": None, "ci95": None, "physical_sample_support": 0}
        denominator = weights[samples].sum(axis=(1, 2))
        numerator = (values * weights)[samples].sum(axis=(1, 2))
        valid = denominator > 0
        estimates = numerator[valid] / denominator[valid]
        return {
            "recall": float((values * weights).sum() / support),
            "ci95": np.quantile(estimates, [0.025, 0.975]).tolist(),
            "physical_sample_support": support,
            "effective_detected_sample_equivalents": float((values * weights).sum()),
        }

    per_element = {}
    for column, element in enumerate(class_order):
        info = interval(detected[:, column : column + 1], positive[:, column : column + 1])
        info["replicate_positive_support"] = int(known_positive[:, column].sum())
        info["replicate_missed_positives"] = int(
            np.sum(known_positive[:, column] & ~detections[:, column])
        )
        info["unknown_label_prediction_count"] = int(
            np.sum(~known_positive[:, column] & detections[:, column])
        )
        per_element[element] = info
    # The primary estimand gives each physical sample equal weight even if its
    # number of known labels differs. Per-element recalls remain separate above.
    label_support = positive.sum(axis=1, keepdims=True)
    sample_recall = np.divide(
        (detected * positive).sum(axis=1, keepdims=True),
        label_support,
        out=np.zeros((len(unique), 1)),
        where=label_support > 0,
    )
    primary = interval(sample_recall, label_support > 0)
    primary["positive_sample_element_pairs"] = int(positive.sum())
    return {
        "primary": primary,
        "per_element": per_element,
        "physical_samples": len(unique),
        "replicate_spectra": len(sample_ids),
        "precision": None,
        "specificity": None,
        "micro_f1": None,
        "false_positive_rate": None,
        "undefined_reason": (
            "No independently confirmed negative labels; "
            "missing/zero chemistry and H/He are unknown."
        ),
        "uncertainty": {
            "method": (
                "physical-sample clustered percentile bootstrap; "
                "replicates receive equal sample weight"
            ),
            "repeats": bootstrap_repeats,
            "seed": bootstrap_seed,
            "scope": (
                "conditional on the single held-out acquisition campaign; "
                "no cross-instrument generalization"
            ),
        },
    }
