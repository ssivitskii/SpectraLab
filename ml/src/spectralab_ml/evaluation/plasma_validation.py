from __future__ import annotations

from dataclasses import asdict

import numpy as np
from joblib import Parallel, delayed
from sklearn.metrics import roc_auc_score

from spectralab_ml.datasets import DatasetBundle, DatasetSplit, split_base_sample_ids
from spectralab_ml.evaluation.real_validation import object_sha256
from spectralab_ml.simulation.plasma import (
    PlasmaModel,
    RandomizationRanges,
    generate_plasma_sample,
    sample_composition,
)

OPERATIONAL_POSITIVE_WT = 1.0
OPERATIONAL_NEGATIVE_WT = 0.1


def _base_sample(
    model: PlasmaModel,
    grid: np.ndarray,
    bands: tuple,
    classes: tuple[str, ...],
    ranges: RandomizationRanges,
    seed_sequence: np.random.SeedSequence,
    variations: int,
) -> list[tuple[np.ndarray, dict, dict]]:
    rng = np.random.default_rng(seed_sequence)
    composition, labels = sample_composition(rng, classes, ranges)
    rows = []
    for _ in range(variations):
        sample = generate_plasma_sample(
            model,
            grid,
            bands,
            rng,
            targets=classes,
            ranges=ranges,
            composition=composition,
            labels=labels,
        )
        rows.append((sample.intensity, labels, sample.record))
    return rows


def make_plasma_dataset(
    model: PlasmaModel,
    grid: np.ndarray,
    mask: np.ndarray,
    bands: tuple,
    class_order: tuple[str, ...],
    reference_metadata: dict,
    *,
    seed: int,
    base_samples: int,
    variations: int,
    ranges: RandomizationRanges | None = None,
    n_jobs: int = -1,
) -> DatasetBundle:
    if base_samples < 30 or variations < 1:
        raise ValueError("Insufficient synthetic development data")
    ranges = ranges or RandomizationRanges()
    ids = [f"plasma-{seed}-{index:04d}" for index in range(base_samples)]
    split = split_base_sample_ids(ids, seed)
    children = np.random.SeedSequence(seed).spawn(base_samples)
    generated = Parallel(n_jobs=n_jobs)(
        delayed(_base_sample)(model, grid, bands, class_order, ranges, child, variations)
        for child in children
    )
    features = {key: [] for key in split}
    labels = {key: [] for key in split}
    groups = {key: [] for key in split}
    records = []
    for base_id, rows in zip(ids, generated, strict=True):
        target = next(name for name, values in split.items() if base_id in values)
        for variation, (intensity, truth, record) in enumerate(rows):
            spectrum = np.where(mask, intensity, 0.0)
            features[target].append(spectrum)
            labels[target].append([truth[element] for element in class_order])
            groups[target].append(base_id)
            records.append(
                {"base_sample_id": base_id, "variation": variation, "split": target, **record}
            )
    splits = {
        key: DatasetSplit(np.asarray(features[key]), np.asarray(labels[key]), tuple(groups[key]))
        for key in split
    }
    return DatasetBundle(
        **splits,
        class_order=class_order,
        dataset_hash=object_sha256(
            {
                "records": records,
                "reference": reference_metadata.get("content_sha256"),
                "ranges": asdict(ranges),
                "mask": np.flatnonzero(mask).tolist(),
            }
        ),
        split_hash=object_sha256({key: sorted(value) for key, value in split.items()}),
        records=records,
        reference_metadata=reference_metadata,
    )


def operational_labels(oxide_wt_percent: float | None) -> bool | None:
    if oxide_wt_percent is None or not np.isfinite(oxide_wt_percent):
        return None
    if oxide_wt_percent >= OPERATIONAL_POSITIVE_WT:
        return True
    if 0 < oxide_wt_percent <= OPERATIONAL_NEGATIVE_WT:
        return False
    return None


def operational_report(
    scores: np.ndarray,
    detections: np.ndarray,
    labels: list[list[bool | None]],
    sample_ids: list[str],
    class_order: tuple[str, ...],
    *,
    bootstrap_repeats: int = 1000,
    bootstrap_seed: int = 20261022,
) -> dict:
    """Summarize rates with each physical material receiving equal weight.

    The pointwise 95% intervals invert Hoeffding's two-sided bound for independent
    variables in [0, 1]. Each variable is a material's mean binary detection over
    its replicates, which need not itself be binary. This conservative interval
    also expresses uncertainty when all observed detections agree. The legacy
    bootstrap arguments remain accepted for call compatibility but are unused.
    """
    scores = np.asarray(scores, dtype=float)
    detections = np.asarray(detections)
    expected_shape = (len(sample_ids), len(class_order))
    if not class_order or len(set(class_order)) != len(class_order):
        raise ValueError("class_order must contain distinct elements")
    if scores.shape != expected_shape or detections.shape != expected_shape:
        raise ValueError("Scores and detections must match sample_ids and class_order")
    if not np.isfinite(scores).all() or not np.isin(detections, [0, 1]).all():
        raise ValueError("Scores must be finite and detections must be binary")
    if len(labels) != len(sample_ids) or any(len(row) != len(class_order) for row in labels):
        raise ValueError("Operational labels must match sample_ids and class_order")
    if any(
        value is not None and not isinstance(value, (bool, np.bool_))
        for row in labels
        for value in row
    ):
        raise ValueError("Operational labels must be boolean or None")
    normalized_labels = [
        [None if value is None else bool(value) for value in row] for row in labels
    ]
    unique = sorted(set(sample_ids))
    index = {sample: i for i, sample in enumerate(unique)}
    rows = np.asarray([index[s] for s in sample_ids])
    mean_detection = np.zeros((len(unique), len(class_order)))
    mean_score = np.zeros_like(mean_detection)
    for i in range(len(unique)):
        mean_detection[i] = detections[rows == i].mean(axis=0)
        mean_score[i] = scores[rows == i].mean(axis=0)
    truth: list[list[bool | None]] = [None] * len(unique)
    for row, sample in zip(normalized_labels, sample_ids, strict=True):
        if truth[index[sample]] is None:
            truth[index[sample]] = row
        elif truth[index[sample]] != row:
            raise ValueError("Replicates have conflicting operational labels")
    result = {}
    for k, element in enumerate(class_order):
        column = [row[k] for row in truth]
        positive = np.asarray([value is True for value in column])
        negative = np.asarray([value is False for value in column])

        def rate(selected: np.ndarray, k: int = k) -> dict:
            support = int(selected.sum())
            if not support:
                return {"value": None, "ci95": None, "support": 0}
            values = mean_detection[selected, k]
            value = float(values.mean())
            # P(|mean - E(mean)| >= radius) <= 2 exp(-2 n radius**2).
            # n counts physical materials, never individual replicate spectra.
            radius = float(np.sqrt(np.log(2.0 / 0.05) / (2 * support)))
            return {
                "value": value,
                "ci95": [max(0.0, value - radius), min(1.0, value + radius)],
                "support": support,
            }

        auc = None
        if positive.any() and negative.any():
            selected = positive | negative
            auc = float(roc_auc_score(positive[selected], mean_score[selected, k]))
        result[element] = {
            "recall": rate(positive),
            "false_positive_rate": rate(negative),
            "roc_auc": auc,
            "unknown_samples": int(len(unique) - positive.sum() - negative.sum()),
        }
    return {
        "per_element": result,
        "physical_samples": len(unique),
        "rate_interval": {
            "method": "hoeffding_bounded_mean",
            "confidence_level": 0.95,
            "scope": "pointwise_per_element_per_rate",
            "unit": "physical_material",
            "estimand": "unweighted mean of within-material detection fractions",
            "radius": "sqrt(log(2 / 0.05) / (2 * physical_material_support))",
            "assumptions": [
                "Distinct sample_ids identify independent physical materials",
                "Within-material binary detections are averaged before estimation",
            ],
            "reference": "https://doi.org/10.1080/01621459.1963.10500830",
        },
        "label_rule": {
            "positive": f"published oxide >= {OPERATIONAL_POSITIVE_WT} wt%",
            "negative": f"0 < published oxide <= {OPERATIONAL_NEGATIVE_WT} wt%",
            "unknown": "otherwise, including missing and zero",
        },
        "caveat": (
            "Published assays without per-value uncertainty; low content is not certified "
            "absence. Conservative pointwise Hoeffding intervals use independent physical "
            "materials, not replicate count, and are wide at small support even with zero "
            "observed errors. They do not cover assay uncertainty, material dependence or "
            "simultaneous comparisons, and do not establish independent blank validation."
        ),
    }
