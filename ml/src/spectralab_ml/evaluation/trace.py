"""Paired synthetic weak-component checks, including explicit absence controls."""

from __future__ import annotations

import hashlib
import itertools
import json
from dataclasses import asdict, dataclass, replace

import numpy as np

from spectralab_ml.data.reference import ReferenceCatalog
from spectralab_ml.preprocessing import SpectrumPreprocessor
from spectralab_ml.simulation import GenerationConfig, generate_spectrum


@dataclass(frozen=True)
class TraceCondition:
    name: str
    ratio: float
    background_peak_ratio: float = 0
    shift_nm: float = 0
    snr_db: float = 20


TRACE_CONDITIONS = (
    TraceCondition("equal", 1),
    TraceCondition("weak_0.10", 0.1),
    TraceCondition("weak_0.03", 0.03),
    TraceCondition("weak_0.01", 0.01),
    TraceCondition("weak_0.10_background", 0.1, 2),
    TraceCondition("weak_0.03_background", 0.03, 2),
    TraceCondition("weak_0.10_shift", 0.1, shift_nm=0.5),
    TraceCondition("equal_shift", 1, shift_nm=0.75),
    TraceCondition("absent_control", 0),
    TraceCondition("absent_control_background", 0, 2),
)


def build_trace_condition(
    catalog: ReferenceCatalog,
    processor: SpectrumPreprocessor,
    classes: tuple[str, ...],
    condition: TraceCondition,
    *,
    seed: int,
    repeats: int = 2,
    ion_stage: int = 1,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, dict]:
    """Render every ordered element pair; ratio zero removes the target entirely.

    Noise/line seeds are paired across conditions, not independent new materials.
    Continuum is relative to the clean peak before normalization; it has a fixed
    slope with end-to-end change of 0.4 times its level.
    """
    if (
        len(classes) < 2
        or len(set(classes)) != len(classes)
        or type(repeats) is not int
        or not 1 <= repeats <= 20
        or type(seed) is not int
        or seed < 0
        or not np.isfinite([condition.ratio, condition.background_peak_ratio]).all()
        or not 0 <= condition.ratio <= 1
        or not 0 <= condition.background_peak_ratio <= 100
    ):
        raise ValueError("Invalid trace benchmark composition, seed or repetition count")
    rows, labels, targets, records = [], [], [], []
    for index, (major, target) in enumerate(itertools.permutations(classes, 2)):
        for repeat in range(repeats):
            base_id = f"trace-{seed}-{index}-{repeat}"
            config = GenerationConfig(
                elements=(major, target) if condition.ratio > 0 else (major,),
                component_weights=(1, condition.ratio) if condition.ratio > 0 else (1,),
                wavelength_start_nm=processor.wavelength_start_nm,
                wavelength_end_nm=processor.wavelength_end_nm,
                sampling_step_nm=processor.model_step_nm,
                instrumental_fwhm_nm=0.5,
                calibration_shift_nm=condition.shift_nm,
                snr_db=condition.snr_db,
                ion_stage=ion_stage,
                seed=10_000_000 + seed * 10_000 + index * 100 + repeat,
            )
            spectrum = generate_spectrum(
                config, catalog, class_order=classes, base_sample_id=base_id
            )
            level = condition.background_peak_ratio * float(spectrum.clean_signal.max())
            if level:
                config = replace(
                    config,
                    background_level=level,
                    background_slope=0.4
                    * level
                    / (processor.wavelength_end_nm - processor.wavelength_start_nm),
                )
                spectrum = generate_spectrum(
                    config, catalog, class_order=classes, base_sample_id=base_id
                )
            rows.append(processor.transform(spectrum.wavelength_nm, spectrum.intensity))
            labels.append([spectrum.true_labels[element] for element in classes])
            targets.append(classes.index(target))
            records.append(
                {"base_sample_id": base_id, "parameters": asdict(config), "target": target}
            )
    X, y, target_indices = np.stack(rows), np.asarray(labels), np.asarray(targets)
    digest = hashlib.sha256()
    for array in (X, y, target_indices):
        digest.update(np.ascontiguousarray(array, dtype="<f8").tobytes())
    metadata = {
        "condition": asdict(condition),
        "seed": seed,
        "repeats": repeats,
        "class_order": classes,
        "preprocessing": processor.to_dict(),
        "reference": {k: v for k, v in catalog.metadata.items() if k != "imported_at"},
        "records": records,
    }
    digest.update(json.dumps(metadata, sort_keys=True, allow_nan=False).encode())
    metadata["dataset_hash"] = digest.hexdigest()
    return X, y, target_indices, metadata


def trace_detection_metrics(y: np.ndarray, predictions: np.ndarray, targets: np.ndarray) -> dict:
    """Report weak-target recall separately from strong-component dominated F1."""
    y, predictions, targets = np.asarray(y), np.asarray(predictions), np.asarray(targets)
    if (
        y.ndim != 2
        or predictions.shape != y.shape
        or targets.shape != (len(y),)
        or not len(y)
        or not np.isin(y, [0, 1]).all()
        or not np.isin(predictions, [0, 1]).all()
        or not np.issubdtype(targets.dtype, np.integer)
        or np.any(targets < 0)
        or np.any(targets >= y.shape[1])
    ):
        raise ValueError("Trace metrics need matching binary arrays and valid target indices")
    selected = (np.arange(len(y)), targets)
    target_truth = y[selected].astype(bool)
    target_predictions = predictions[selected].astype(bool)
    negative = y == 0
    return {
        "count": len(y),
        "weak_target_positive_count": int(target_truth.sum()),
        "weak_target_recall": float(target_predictions[target_truth].mean())
        if target_truth.any()
        else None,
        "target_absent_count": int((~target_truth).sum()),
        "target_false_positive_rate": float(target_predictions[~target_truth].mean())
        if (~target_truth).any()
        else None,
        "false_positive_count": int((predictions.astype(bool) & negative).sum()),
        "negative_decision_count": int(negative.sum()),
        "false_positive_rate": float(predictions[negative].mean()) if negative.any() else None,
    }
