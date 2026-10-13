"""Preregistered band-limited mixture/trace/overlap benchmark.

Line positions come from saved NIST exports. Per-element summed line strengths
are normalized before synthesis: contribution ratios are not concentrations.
The cached renderer uses the production pixel-integrated profile functions,
with independent intrinsic and readout RNG streams and grouped noise repeats.
"""

from __future__ import annotations

import hashlib
import itertools
import platform
import uuid
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path
from typing import Any

import numpy as np
import yaml

from spectralab_ml.benchmark import measure_inference
from spectralab_ml.config import _integer, _mapping, _number, _read_yaml, _strings, stable_hash
from spectralab_ml.data.reference import ReferenceCatalog, reference_content_hash
from spectralab_ml.datasets import split_base_sample_ids
from spectralab_ml.evaluation import evaluate_multilabel
from spectralab_ml.experiments import _write_json
from spectralab_ml.models import (
    BaselineNNLS,
    LogisticOVR,
    ModelManifest,
    RandomForestMultiLabel,
    save_artifact,
)
from spectralab_ml.models.estimators import select_thresholds
from spectralab_ml.preprocessing import SpectrumPreprocessor
from spectralab_ml.simulation import GenerationConfig
from spectralab_ml.simulation.generator import pixel_integrated_gaussian, pixel_integrated_voigt

MODELS = ("baseline-nnls", "logistic-ovr", "random-forest")


@dataclass(frozen=True)
class Profile:
    condition_id: str
    profile: str
    instrumental_fwhm_nm: float
    lorentzian_fwhm_nm: float = 0.0
    response_slope: float = 0.0

    @classmethod
    def parse(cls, raw: dict) -> Profile:
        _mapping(
            raw, set(cls.__dataclass_fields__), {"condition_id", "profile", "instrumental_fwhm_nm"}
        )
        if raw["profile"] not in ("gaussian", "voigt"):
            raise ValueError("Profile must be gaussian or voigt")
        if (
            not isinstance(raw["condition_id"], str)
            or not raw["condition_id"].replace("-", "").isalnum()
        ):
            raise ValueError("Invalid condition identifier")
        values = {
            k: _number(raw.get(k, 0), k)
            for k in ("instrumental_fwhm_nm", "lorentzian_fwhm_nm", "response_slope")
        }
        GenerationConfig(
            elements=("H",), component_weights=(1,), profile=raw["profile"], **values
        ).validate()
        return cls(raw["condition_id"], raw["profile"], **values)


@dataclass(frozen=True)
class Suite:
    suite_id: str
    elements: tuple[str, ...]
    wavelength_range_nm: tuple[float, float]
    grid_steps_nm: tuple[float, ...]
    snr_db: float
    calibration_jitter_std_nm: float
    training_fwhm_nm: float
    conditions: tuple[Profile, ...]
    selected_lines_nm: dict[str, float] | None = None

    @classmethod
    def parse(cls, raw: dict) -> Suite:
        _mapping(
            raw,
            set(cls.__dataclass_fields__),
            set(cls.__dataclass_fields__) - {"selected_lines_nm"},
        )
        name = raw["suite_id"]
        if not isinstance(name, str) or not name.replace("-", "").isalnum():
            raise ValueError("Invalid suite identifier")
        elements = _strings(raw["elements"], "elements")
        if len(elements) < 2:
            raise ValueError("At least two classes required")
        span = tuple(_number(v, "wavelength_range_nm") for v in raw["wavelength_range_nm"])
        if len(span) != 2:
            raise ValueError("A wavelength range has two endpoints")
        grids = tuple(_number(v, "grid_steps_nm") for v in raw["grid_steps_nm"])
        if len(grids) < 2 or len(set(grids)) != len(grids):
            raise ValueError("At least two distinct preregistered grids required")
        conditions = tuple(Profile.parse(v) for v in raw["conditions"])
        if not conditions or len({v.condition_id for v in conditions}) != len(conditions):
            raise ValueError("Unique profile conditions required")
        training_width = _number(raw["training_fwhm_nm"], "training_fwhm_nm")
        for step in grids:
            for profile in (*conditions, Profile("training", "gaussian", training_width)):
                GenerationConfig(
                    elements=(elements[0],),
                    component_weights=(1,),
                    wavelength_start_nm=span[0],
                    wavelength_end_nm=span[1],
                    sampling_step_nm=step,
                    profile=profile.profile,
                    instrumental_fwhm_nm=profile.instrumental_fwhm_nm,
                    lorentzian_fwhm_nm=profile.lorentzian_fwhm_nm,
                    response_slope=profile.response_slope,
                ).validate()
        snr = _number(raw["snr_db"], "snr_db")
        jitter = _number(raw["calibration_jitter_std_nm"], "calibration_jitter_std_nm")
        if not 0 <= jitter <= 0.05 or not 0 <= snr <= 100:
            raise ValueError("Invalid preregistered noise/jitter")
        selected = raw.get("selected_lines_nm")
        if selected is not None:
            _mapping(selected, set(elements), set(elements))
            selected = {k: _number(v, "selected_lines_nm") for k, v in selected.items()}
        return cls(name, elements, span, grids, snr, jitter, training_width, conditions, selected)


@dataclass(frozen=True)
class RobustnessConfig:
    schema_version: int
    benchmark_id: str
    reference_cache: str
    seeds: tuple[int, ...]
    base_samples: int
    variations: int
    models: tuple[str, ...]
    bootstrap_repeats: int
    confidence_level: float
    trace_ratios: tuple[float, ...]
    trace_recall_target: float
    trace_false_positive_ceiling: float
    suites: tuple[Suite, ...]

    @classmethod
    def load(cls, path: Path) -> RobustnessConfig:
        raw = _mapping(
            _read_yaml(path), set(cls.__dataclass_fields__), set(cls.__dataclass_fields__)
        )
        if type(raw["schema_version"]) is not int or raw["schema_version"] != 1:
            raise ValueError("Robustness schema_version must be 1")
        name = raw["benchmark_id"]
        if not isinstance(name, str) or not name.replace("-", "").isalnum():
            raise ValueError("Invalid benchmark identifier")
        seeds = tuple(_integer(v, "seed", 0, 2**31 - 1) for v in raw["seeds"])
        if len(seeds) < 5 or len(set(seeds)) != len(seeds):
            raise ValueError("At least five distinct fixed seeds required")
        models = _strings(raw["models"], "models")
        if set(models) != set(MODELS):
            raise ValueError("This protocol compares all three fixed baseline models")
        ratios = tuple(_number(v, "trace_ratios") for v in raw["trace_ratios"])
        if (
            not ratios
            or 0 not in ratios
            or len(set(ratios)) != len(ratios)
            or any(v < 0 or v > 1 for v in ratios)
        ):
            raise ValueError("Trace ratios must be unique in [0,1], including absent control 0")
        confidence = _number(raw["confidence_level"], "confidence_level")
        recall = _number(raw["trace_recall_target"], "trace_recall_target")
        ceiling = _number(raw["trace_false_positive_ceiling"], "trace_false_positive_ceiling")
        if not 0.8 <= confidence <= 0.99 or not 0 < recall < 1 or not 0 < ceiling < 1:
            raise ValueError("Invalid interval/decision criteria")
        suites = tuple(Suite.parse(v) for v in raw["suites"])
        if not suites or len({s.suite_id for s in suites}) != len(suites):
            raise ValueError("Unique suites required")
        if not isinstance(raw["reference_cache"], str):
            raise ValueError("reference_cache must be a local path string")
        return cls(
            1,
            name,
            raw["reference_cache"],
            seeds,
            _integer(raw["base_samples"], "base_samples", 120, 2000),
            _integer(raw["variations"], "variations", 2, 10),
            models,
            _integer(raw["bootstrap_repeats"], "bootstrap_repeats", 200, 5000),
            confidence,
            ratios,
            recall,
            ceiling,
            suites,
        )


def focused_catalog(parent: ReferenceCatalog, suite: Suite) -> ReferenceCatalog:
    """Preserve raw provenance while explicitly normalizing cross-element priors."""
    selected = [
        line
        for line in parent.lines
        if line.element in suite.elements
        and line.ion_stage == 1
        and suite.wavelength_range_nm[0] <= line.wavelength_nm <= suite.wavelength_range_nm[1]
    ]
    if suite.selected_lines_nm:
        selected = [
            line
            for line in selected
            if abs(line.wavelength_nm - suite.selected_lines_nm[line.element]) < 1e-9
        ]
        if any(sum(line.element == e for line in selected) != 1 for e in suite.elements):
            raise ValueError(
                "Close-line scenario must identify exactly one physical transition/class"
            )
    sums = {
        e: sum(
            1 if line.relative_intensity is None else line.relative_intensity
            for line in selected
            if line.element == e
        )
        for e in suite.elements
    }
    if any(total <= 0 for total in sums.values()):
        raise ValueError("Every configured class needs a positive in-window prior")
    lines = [
        replace(
            line,
            relative_intensity=(1 if line.relative_intensity is None else line.relative_intensity)
            / sums[line.element],
            flags=line.flags + ";research_per_element_unit_area_prior",
        )
        for line in selected
    ]
    metadata = {
        "source": "nist_asd_normalized_research_window",
        "schema_version": 2,
        "source_version": "normalized-window-v1",
        "units": "nm",
        "wavelength_medium": "vacuum",
        "parent_reference": parent.metadata,
        "parent_content_sha256": parent.metadata["content_sha256"],
        "selection": asdict(suite),
        "prior_sums_before_normalization": sums,
        "normalization": (
            "Within each element/window, raw line priors sum to 1 before profile "
            "truncation. Missing intensity prior=1. Not a concentration calibration."
        ),
    }
    metadata["content_sha256"] = reference_content_hash(metadata, [asdict(line) for line in lines])
    return ReferenceCatalog(lines, metadata)


@dataclass(frozen=True)
class Composition:
    base_id: str
    weights: tuple[float, ...]
    intrinsic_seed: int
    noise_seeds: tuple[int, ...]
    shift_nm: float
    trace_index: int | None = None
    major_index: int | None = None


def compositions(
    suite: Suite, seed: int, count: int, variations: int, *, trace: bool = False
) -> list[Composition]:
    rng = np.random.default_rng(seed + (1_000_003 if trace else 0))
    result = []
    for index in range(count):
        weights = np.zeros(len(suite.elements))
        if trace:
            major, minor = rng.choice(len(weights), 2, replace=False)
            weights[major], weights[minor] = rng.uniform(0.7, 1.3), 1
        else:
            size = 1 + index % min(3, len(weights))
            chosen = rng.choice(len(weights), size, replace=False)
            weights[chosen] = np.exp(rng.uniform(np.log(0.1), 0, size))
            major, minor = None, None
        result.append(
            Composition(
                f"{suite.suite_id}-{seed}-{'trace' if trace else 'base'}-{index:04d}",
                tuple(float(v) for v in weights),
                int(rng.integers(0, 2**32)),
                tuple(int(v) for v in rng.integers(0, 2**32, variations)),
                float(rng.normal(0, suite.calibration_jitter_std_nm)),
                int(minor) if minor is not None else None,
                int(major) if major is not None else None,
            )
        )
    return result


class CachedRenderer:
    """Reuse exact line profiles with a small cache, retaining zero-shift templates.

    Calibration shifts are applied to line centers. A small deterministic set of
    shift values is not substituted for the continuous sample-specific shifts.
    """

    def __init__(self, catalog: ReferenceCatalog, suite: Suite, step: float):
        self.catalog, self.suite, self.step = catalog, suite, step
        self.processor = SpectrumPreprocessor(*suite.wavelength_range_nm, step)
        self.grid = self.processor.model_grid
        self.line_classes = np.array([suite.elements.index(line.element) for line in catalog.lines])
        self.priors = np.array([line.relative_intensity for line in catalog.lines], dtype=float)
        self._profile_cache: dict[tuple, np.ndarray] = {}

    def basis(self, profile: Profile, shift: float) -> np.ndarray:
        key = (
            profile.profile,
            profile.instrumental_fwhm_nm,
            profile.lorentzian_fwhm_nm,
            profile.response_slope,
            shift,
        )
        if key not in self._profile_cache:
            rows = []
            for line in self.catalog.lines:
                center = line.wavelength_nm + shift
                if profile.profile == "gaussian":
                    row = pixel_integrated_gaussian(self.grid, center, profile.instrumental_fwhm_nm)
                else:
                    row = pixel_integrated_voigt(
                        self.grid, center, profile.instrumental_fwhm_nm, profile.lorentzian_fwhm_nm
                    )
                rows.append(row)
            response = 1 + profile.response_slope * (
                self.grid - np.mean(self.suite.wavelength_range_nm)
            ) / (np.ptp(self.suite.wavelength_range_nm) / 2)
            result = np.stack(rows) * response
            # Keep a small cache: zero-shift templates and only the most recent sample.
            if len(self._profile_cache) > 8:
                self._profile_cache = {k: v for k, v in self._profile_cache.items() if k[-1] == 0}
            self._profile_cache[key] = result
        return self._profile_cache[key]

    def render(
        self, definitions: list[Composition], profile: Profile, ratio: float | None = None
    ) -> dict[str, Any]:
        X, y, ids, traces, realized = [], [], [], [], []
        for item in definitions:
            weights = np.asarray(item.weights).copy()
            if ratio is not None:
                weights[item.trace_index] = weights[item.major_index] * ratio
            rng = np.random.default_rng(item.intrinsic_seed)
            perturbation = rng.lognormal(-(0.08**2) / 2, 0.08, len(self.priors))
            line_weights = self.priors * perturbation * weights[self.line_classes]
            basis = self.basis(profile, item.shift_nm)
            clean = line_weights @ basis
            if ratio is not None and ratio > 0:
                masses = line_weights * basis.sum(axis=1)
                realized.append(
                    float(
                        masses[self.line_classes == item.trace_index].sum()
                        / masses[self.line_classes == item.major_index].sum()
                    )
                )
            noise_std = np.sqrt(np.mean(clean**2)) / 10 ** (self.suite.snr_db / 20)
            for noise_seed in item.noise_seeds:
                observed = clean + np.random.default_rng(
                    noise_seed ^ round(self.step * 1e9)
                ).normal(0, noise_std, len(clean))
                X.append(self.processor.transform(self.grid, observed))
                y.append((weights > 0).astype(int))
                ids.append(item.base_id)
                traces.append(item.trace_index if item.trace_index is not None else -1)
        return {
            "X": np.asarray(X),
            "y": np.asarray(y),
            "base_ids": np.asarray(ids),
            "trace_indices": np.asarray(traces),
            "realized_trace_ratio": {
                "mean": float(np.mean(realized)),
                "min": min(realized),
                "max": max(realized),
            }
            if realized
            else None,
        }

    def templates(self, profile: Profile) -> np.ndarray:
        basis = self.basis(profile, 0)
        rows = [
            self.priors[self.line_classes == index] @ basis[self.line_classes == index]
            for index in range(len(self.suite.elements))
        ]
        return np.stack([row / np.max(np.abs(row)) for row in rows])


def grouped_counts(
    truth: np.ndarray, prediction: np.ndarray, base_ids: np.ndarray, trace_indices: np.ndarray
) -> tuple[np.ndarray, list[str]]:
    """Sufficient statistics per physical group; repeated noise rows stay together."""
    groups = sorted(set(base_ids.tolist()))
    result = []
    for group in groups:
        mask = base_ids == group
        y, p = truth[mask].astype(bool), prediction[mask].astype(bool)
        tp, fp, fn = (p & y).sum(axis=0), (p & ~y).sum(axis=0), (~p & y).sum(axis=0)
        exact, entries = np.all(p == y, axis=1).sum(), len(y)
        targeted = trace_indices[mask]
        trace_tp = trace_positive = trace_fp = trace_negative = 0
        if np.all(targeted >= 0):
            actual = y[np.arange(entries), targeted]
            detected = p[np.arange(entries), targeted]
            trace_tp, trace_positive = np.sum(detected & actual), np.sum(actual)
            trace_fp, trace_negative = np.sum(detected & ~actual), np.sum(~actual)
        result.append(
            np.r_[tp, fp, fn, exact, entries, trace_tp, trace_positive, trace_fp, trace_negative]
        )
    return np.asarray(result, dtype=float), groups


def metrics_from_counts(counts: np.ndarray, class_count: int) -> dict[str, np.ndarray]:
    """Accept one sum or a batch of bootstrap sums without re-fitting any model."""
    c = np.asarray(counts)
    tp, fp, fn = (
        c[..., :class_count],
        c[..., class_count : 2 * class_count],
        c[..., 2 * class_count : 3 * class_count],
    )
    denominator = 2 * tp + fp + fn
    per_f1 = np.divide(2 * tp, denominator, out=np.zeros_like(tp), where=denominator > 0)
    total = denominator.sum(axis=-1)
    tail = c[..., 3 * class_count :]
    return {
        "micro_f1": np.divide(
            2 * tp.sum(axis=-1), total, out=np.zeros_like(total), where=total > 0
        ),
        "macro_f1": per_f1.mean(axis=-1),
        "exact_match_accuracy": tail[..., 0] / tail[..., 1],
        "hamming_loss": (fp + fn).sum(axis=-1) / (tail[..., 1] * class_count),
        "trace_recall": np.divide(
            tail[..., 2],
            tail[..., 3],
            out=np.full_like(tail[..., 2], np.nan),
            where=tail[..., 3] > 0,
        ),
        "trace_false_positive_rate": np.divide(
            tail[..., 4],
            tail[..., 5],
            out=np.full_like(tail[..., 4], np.nan),
            where=tail[..., 5] > 0,
        ),
    }


def hierarchical_bootstrap(grouped: list[np.ndarray], repeats: int, seed: int) -> np.ndarray:
    """Resample training seeds then physical base groups within each sampled seed."""
    rng = np.random.default_rng(seed)
    seed_indices = rng.integers(0, len(grouped), (repeats, len(grouped)))
    samples = np.zeros((repeats, grouped[0].shape[1]))
    for replicate in range(repeats):
        for chosen in seed_indices[replicate]:
            groups = grouped[chosen]
            selection = rng.integers(0, len(groups), len(groups))
            samples[replicate] += groups[selection].sum(axis=0)
    return samples


def summarize_counts(
    grouped: list[np.ndarray],
    classes: tuple[str, ...],
    repeats: int,
    confidence: float,
    bootstrap_seed: int,
) -> dict:
    point = metrics_from_counts(np.stack([g.sum(axis=0) for g in grouped]), len(classes))
    bootstrap = metrics_from_counts(
        hierarchical_bootstrap(grouped, repeats, bootstrap_seed), len(classes)
    )
    pooled = metrics_from_counts(sum(g.sum(axis=0) for g in grouped), len(classes))
    alpha = (1 - confidence) / 2
    results = {}
    for key, values in point.items():
        valid = values[np.isfinite(values)]
        draws = bootstrap[key][np.isfinite(bootstrap[key])]
        results[key] = {
            "mean": float(np.mean(valid)) if len(valid) else None,
            "std": float(np.std(valid, ddof=1)) if len(valid) > 1 else None,
            "count": len(valid),
            "pooled_estimate": float(pooled[key]) if np.isfinite(pooled[key]) else None,
            "ci_estimand": "pooled metric over equally sampled training seeds and physical groups",
            "ci_low": float(np.quantile(draws, alpha)) if len(draws) else None,
            "ci_high": float(np.quantile(draws, 1 - alpha)) if len(draws) else None,
            "ci_method": "hierarchical percentile bootstrap: training seed then base group",
            "bootstrap_repeats": repeats,
            "confidence_level": confidence,
        }
    return results


def _all_conditions(
    config: RobustnessConfig, suite: Suite
) -> list[tuple[str, Profile, float | None]]:
    reference = Profile("trace-reference", "gaussian", suite.training_fwhm_nm)
    return [(p.condition_id, p, None) for p in suite.conditions] + [
        (f"trace-{ratio:g}", reference, ratio) for ratio in config.trace_ratios
    ]


def _fit_models(
    config: RobustnessConfig,
    suite: Suite,
    catalog: ReferenceCatalog,
    renderer: CachedRenderer,
    definitions: list[Composition],
    seed: int,
    directory: Path,
) -> tuple[dict, dict, dict, list[Composition]]:
    allocations = split_base_sample_ids([d.base_id for d in definitions], seed)
    reference = Profile("training", "gaussian", suite.training_fwhm_nm)
    parts = {
        name: renderer.render([d for d in definitions if d.base_id in ids], reference)
        for name, ids in allocations.items()
    }
    for name in ("train", "validation"):
        if np.any(parts[name]["y"].sum(axis=0) == 0) or np.any(
            parts[name]["y"].sum(axis=0) == len(parts[name]["y"])
        ):
            raise ValueError(f"Preregistered {name} split lacks positive/negative support")
    split_ids = {k: sorted(v) for k, v in allocations.items()}
    split_hash = stable_hash(split_ids)
    dataset_hash = stable_hash(
        {
            "compositions": [asdict(d) for d in definitions],
            "reference_hash": catalog.metadata["content_sha256"],
            "grid": renderer.processor.to_dict(),
            "suite": asdict(suite),
        }
    )
    directory.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        directory / "development_data.npz",
        **{
            f"{name}_{key}": value
            for name, data in parts.items()
            for key, value in data.items()
            if key in {"X", "y", "base_ids"}
        },
    )
    metadata = {
        "dataset_hash": dataset_hash,
        "split_hash": split_hash,
        "split_ids": split_ids,
        "definitions": [asdict(d) for d in definitions],
        "reference": catalog.metadata,
        "preprocessing": renderer.processor.to_dict(),
        "repeats_per_base": config.variations,
    }
    _write_json(directory / "dataset.json", metadata)
    fitted, thresholds, fit_metrics = {}, {}, {}
    estimators = {
        "baseline-nnls": BaselineNNLS(renderer.templates(reference)),
        "logistic-ovr": LogisticOVR(seed=seed),
        "random-forest": RandomForestMultiLabel(seed=seed),
    }
    for model_id in config.models:
        model = estimators[model_id].fit(parts["train"]["X"], parts["train"]["y"])
        validation = model.scores(parts["validation"]["X"])
        threshold, selection = select_thresholds(
            validation,
            parts["validation"]["y"],
            default={"baseline-nnls": 0.15, "logistic-ovr": 0, "random-forest": 0.5}[model_id],
        )
        selection.update(base_sample_ids=split_ids["validation"], split_hash=split_hash)
        model_metrics = {
            "validation": evaluate_multilabel(
                parts["validation"]["y"], validation, threshold, suite.elements
            )
        }
        manifest = ModelManifest.create(
            model_id=model_id,
            model_type=type(model).__name__,
            class_order=suite.elements,
            preprocessor=renderer.processor,
            thresholds=dict(zip(suite.elements, threshold.tolist(), strict=True)),
            threshold_selection=selection,
            score_kind=model.score_kind,
            calibrated=False,
            dataset_hash=dataset_hash,
            split_hash=split_hash,
            seed=seed,
            metrics=model_metrics,
            reference_metadata=catalog.metadata,
            generator_parameters={
                "profile": asdict(reference),
                "snr_db": suite.snr_db,
                "intrinsic_line_variation": 0.08,
                "calibration_jitter_std_nm": suite.calibration_jitter_std_nm,
                "independent_noise_repeats": config.variations,
                "renderer": (
                    "shared production profile kernels, separate intrinsic/readout RNG streams"
                ),
            },
            split_ids=split_ids,
        )
        save_artifact(directory / "models" / model_id, model, renderer.processor, manifest)
        fitted[model_id], thresholds[model_id], fit_metrics[model_id] = (
            model,
            threshold,
            model_metrics,
        )
    test = [d for d in definitions if d.base_id in allocations["test"]]
    return fitted, thresholds, metadata, test


def _seed_grid_run(
    config: RobustnessConfig,
    suite: Suite,
    catalog: ReferenceCatalog,
    step: float,
    seed: int,
    directory: Path,
) -> dict:
    definitions = compositions(suite, seed, config.base_samples, config.variations)
    renderer = CachedRenderer(catalog, suite, step)
    models, thresholds, metadata, test = _fit_models(
        config, suite, catalog, renderer, definitions, seed, directory
    )
    trace_definitions = compositions(suite, seed, len(test), config.variations, trace=True)
    assert not {d.base_id for d in definitions} & {d.base_id for d in trace_definitions}
    _write_json(
        directory / "trace_compositions.json",
        {"definitions": [asdict(d) for d in trace_definitions]},
    )
    conditions, raw_arrays = [], {}
    for index, (name, profile, ratio) in enumerate(_all_conditions(config, suite)):
        data = renderer.render(test if ratio is None else trace_definitions, profile, ratio)
        raw_arrays[f"{index}_truth"] = data["y"]
        raw_arrays[f"{index}_base_ids"] = data["base_ids"]
        raw_arrays[f"{index}_trace_indices"] = data["trace_indices"]
        rows = []
        for model_id, model in models.items():
            scores = model.scores(data["X"])
            prediction = (scores >= thresholds[model_id]).astype(int)
            counts, ids = grouped_counts(
                data["y"], prediction, data["base_ids"], data["trace_indices"]
            )
            raw_arrays[f"{index}_{model_id}_scores"] = scores
            raw_arrays[f"{index}_{model_id}_predicted"] = prediction
            raw_arrays[f"{index}_{model_id}_group_counts"] = counts
            raw_arrays[f"{index}_{model_id}_group_ids"] = np.array(ids)
            metrics = evaluate_multilabel(data["y"], scores, thresholds[model_id], suite.elements)
            metrics.update(
                measure_inference(lambda model=model, X=data["X"]: model.scores(X), len(data["X"]))
            )
            extra = metrics_from_counts(counts.sum(axis=0), len(suite.elements))
            for field in ("trace_recall", "trace_false_positive_rate"):
                metrics[field] = float(extra[field]) if np.isfinite(extra[field]) else None
            rows.append(
                {
                    "model_id": model_id,
                    "metrics": metrics,
                    "base_support": {
                        e: int(np.sum((counts[:, i] + counts[:, 2 * len(suite.elements) + i]) > 0))
                        for i, e in enumerate(suite.elements)
                    },
                }
            )
        conditions.append(
            {
                "condition_index": index,
                "condition_id": name,
                "profile": asdict(profile),
                "cohort": "grouped_test" if ratio is None else "independent_trace_test",
                "trace_ratio": ratio,
                "realized_trace_ratio": data["realized_trace_ratio"],
                "sample_count": len(data["y"]),
                "base_group_count": len(set(data["base_ids"])),
                "models": rows,
            }
        )
    np.savez_compressed(directory / "predictions.npz", **raw_arrays)
    result = {
        "status": "completed",
        "seed": seed,
        "suite_id": suite.suite_id,
        "grid_step_nm": step,
        "class_order": suite.elements,
        "split_hash": metadata["split_hash"],
        "dataset_hash": metadata["dataset_hash"],
        "source_hash": catalog.metadata["content_sha256"],
        "conditions": conditions,
        "raw_predictions_sha256": hashlib.sha256(
            (directory / "predictions.npz").read_bytes()
        ).hexdigest(),
        "path": directory.name,
    }
    _write_json(directory / "result.json", result)
    return result


def aggregate_results(
    config: RobustnessConfig, output: Path, runs: list[dict], successful_seeds: list[int]
) -> dict:
    results, deltas, grid_deltas = [], [], []
    alpha = (1 - config.confidence_level) / 2
    for suite in config.suites:
        grouped_cache = {}
        for step in suite.grid_steps_nm:
            selected = [
                next(
                    r
                    for r in runs
                    if r["seed"] == seed
                    and r["suite_id"] == suite.suite_id
                    and r["grid_step_nm"] == step
                )
                for seed in successful_seeds
            ]
            for index, (name, profile, ratio) in enumerate(_all_conditions(config, suite)):
                bootstrap_seed = 81000 + index
                models_grouped, models_draws = {}, {}
                expected_ids = []
                for model_id in config.models:
                    grouped = []
                    actual_ids = []
                    supports = dict.fromkeys(suite.elements, 0)
                    for run in selected:
                        path = output / "runs" / run["path"] / "predictions.npz"
                        if (
                            hashlib.sha256(path.read_bytes()).hexdigest()
                            != run["raw_predictions_sha256"]
                        ):
                            raise ValueError("Raw prediction integrity mismatch")
                        with np.load(path, allow_pickle=False) as data:
                            grouped.append(data[f"{index}_{model_id}_group_counts"])
                            actual_ids.append(data[f"{index}_{model_id}_group_ids"].tolist())
                        row = next(
                            m
                            for m in run["conditions"][index]["models"]
                            if m["model_id"] == model_id
                        )
                        for e in suite.elements:
                            supports[e] += row["base_support"][e]
                    if expected_ids and actual_ids != expected_ids:
                        raise ValueError(
                            "Model comparisons require exactly matched physical groups"
                        )
                    expected_ids = actual_ids
                    models_grouped[model_id] = grouped
                    counts = hierarchical_bootstrap(
                        grouped, config.bootstrap_repeats, bootstrap_seed
                    )
                    models_draws[model_id] = metrics_from_counts(counts, len(suite.elements))[
                        "micro_f1"
                    ]
                    grouped_cache[(step, index, model_id)] = (grouped, actual_ids)
                    metrics = summarize_counts(
                        grouped,
                        suite.elements,
                        config.bootstrap_repeats,
                        config.confidence_level,
                        bootstrap_seed,
                    )
                    results.append(
                        {
                            "suite_id": suite.suite_id,
                            "grid_step_nm": step,
                            "condition_id": name,
                            "trace_ratio": ratio,
                            "profile": asdict(profile),
                            "model_id": model_id,
                            "successful_seeds": successful_seeds,
                            "metrics": metrics,
                            "total_base_groups": sum(len(g) for g in grouped),
                            "per_element_positive_base_support": supports,
                        }
                    )
                for first, second in itertools.combinations(config.models, 2):
                    draws = models_draws[first] - models_draws[second]
                    point = [
                        metrics_from_counts(
                            sum(g.sum(axis=0) for g in models_grouped[m]), len(suite.elements)
                        )["micro_f1"]
                        for m in (first, second)
                    ]
                    deltas.append(
                        {
                            "suite_id": suite.suite_id,
                            "grid_step_nm": step,
                            "condition_id": name,
                            "model_a": first,
                            "model_b": second,
                            "metric": "micro_f1",
                            "difference_a_minus_b": float(point[0] - point[1]),
                            "ci_low": float(np.quantile(draws, alpha)),
                            "ci_high": float(np.quantile(draws, 1 - alpha)),
                            "method": (
                                "paired hierarchical bootstrap with identical seed/group draws"
                            ),
                        }
                    )
        for fine, coarse in itertools.combinations(sorted(suite.grid_steps_nm), 2):
            for index, (name, _, _) in enumerate(_all_conditions(config, suite)):
                for model_id in config.models:
                    first, ids_a = grouped_cache[(fine, index, model_id)]
                    second, ids_b = grouped_cache[(coarse, index, model_id)]
                    if ids_a != ids_b:
                        raise ValueError("Grid comparisons require matched base groups")
                    draws_a = metrics_from_counts(
                        hierarchical_bootstrap(first, config.bootstrap_repeats, 81000 + index),
                        len(suite.elements),
                    )["micro_f1"]
                    draws_b = metrics_from_counts(
                        hierarchical_bootstrap(second, config.bootstrap_repeats, 81000 + index),
                        len(suite.elements),
                    )["micro_f1"]
                    difference = draws_a - draws_b
                    point_a = metrics_from_counts(
                        sum(g.sum(axis=0) for g in first), len(suite.elements)
                    )["micro_f1"]
                    point_b = metrics_from_counts(
                        sum(g.sum(axis=0) for g in second), len(suite.elements)
                    )["micro_f1"]
                    grid_deltas.append(
                        {
                            "suite_id": suite.suite_id,
                            "condition_id": name,
                            "model_id": model_id,
                            "fine_step_nm": fine,
                            "coarse_step_nm": coarse,
                            "micro_f1_difference_fine_minus_coarse": float(point_a - point_b),
                            "ci_low": float(np.quantile(difference, alpha)),
                            "ci_high": float(np.quantile(difference, 1 - alpha)),
                            "method": (
                                "paired seed/base bootstrap; each grid has its own "
                                "development-fitted model"
                            ),
                        }
                    )
    limits = []
    for suite in config.suites:
        for step in suite.grid_steps_nm:
            for model_id in config.models:
                cells = [
                    r
                    for r in results
                    if r["suite_id"] == suite.suite_id
                    and r["grid_step_nm"] == step
                    and r["model_id"] == model_id
                    and r["trace_ratio"] is not None
                ]
                control = next(r for r in cells if r["trace_ratio"] == 0)
                fpr = control["metrics"]["trace_false_positive_rate"]["pooled_estimate"]
                qualified = [
                    r["trace_ratio"]
                    for r in cells
                    if r["trace_ratio"] > 0
                    and r["metrics"]["trace_recall"]["pooled_estimate"]
                    >= config.trace_recall_target
                    and fpr <= config.trace_false_positive_ceiling
                ]
                limits.append(
                    {
                        "suite_id": suite.suite_id,
                        "grid_step_nm": step,
                        "model_id": model_id,
                        "smallest_tested_ratio_meeting_preregistered_point_criteria": min(qualified)
                        if qualified
                        else None,
                        "recall_target": config.trace_recall_target,
                        "absent_control_false_positive_ceiling": (
                            config.trace_false_positive_ceiling
                        ),
                        "actual_absent_control_fpr": fpr,
                        "scope": (
                            "finite tested contribution ratios, not concentration/instrument LoD; "
                            "thresholds were not selected here"
                        ),
                    }
                )
    return {
        "aggregates": results,
        "paired_model_differences": deltas,
        "paired_grid_differences": grid_deltas,
        "tested_trace_ratio_limits": limits,
    }


def run_benchmark(config_path: Path, output_root: Path) -> Path:
    config = RobustnessConfig.load(config_path)
    cache_path = config_path.parent / config.reference_cache
    parent = ReferenceCatalog.from_cache(cache_path)
    catalogs = {suite.suite_id: focused_catalog(parent, suite) for suite in config.suites}
    implementation_hash = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    package_root = Path(__file__).resolve().parents[1]
    implementation_files = {
        str(path.relative_to(package_root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(package_root.rglob("*.py"))
    }
    run_id = f"{config.benchmark_id}-{datetime.now(UTC):%Y%m%dT%H%M%S}-{uuid.uuid4().hex[:8]}"
    output = output_root / run_id
    output.mkdir(parents=True, exist_ok=False)
    preregistration = {
        "schema_version": 1,
        "created_at": datetime.now(UTC).isoformat(),
        "configuration": asdict(config),
        "configuration_hash": stable_hash(asdict(config)),
        "source_sha256": hashlib.sha256(cache_path.read_bytes()).hexdigest(),
        "source_content_sha256": parent.metadata["content_sha256"],
        "implementation_sha256": implementation_hash,
        "package_file_sha256": implementation_files,
        "suite_source_hashes": {name: c.metadata["content_sha256"] for name, c in catalogs.items()},
        "prediction_started": False,
        "threshold_selection": "validation base IDs only; one fit per model/suite/grid/seed",
        "interval_method": (
            "Percentile hierarchical bootstrap at configuration.confidence_level; "
            "seed resampling then base-group "
            "resampling; no resampling noise rows independently"
        ),
        "paired_comparisons": (
            "Identical seed/group draws across models and grids; no multiplicity "
            "correction; exploratory intervals"
        ),
        "trace_criterion": (
            "smallest preregistered tested contribution ratio with pooled "
            "recall>=target and absent-control FPR<=ceiling; not an "
            "instrument/concentration limit"
        ),
    }
    _write_json(output / "preregistration.json", preregistration)
    prereg_hash = hashlib.sha256((output / "preregistration.json").read_bytes()).hexdigest()
    for name, catalog in catalogs.items():
        catalog.to_cache(output / "sources" / f"{name}.json")
    (output / "config.yaml").write_text(yaml.safe_dump(asdict(config)), encoding="utf-8")
    result = {
        "run_id": run_id,
        "status": "running",
        "created_at": preregistration["created_at"],
        "preregistration_sha256": prereg_hash,
        "expected_seeds": list(config.seeds),
        "successful_seeds": [],
        "failed_runs": [],
        "runs": [],
        "limitations": [
            "Band-limited synthetic spectra, not external measured validation.",
            (
                "NIST line strengths are normalized within each element; weights are "
                "contribution ratios, not concentrations."
            ),
            (
                "Profiles and response slopes are controlled approximations, not measured "
                "detector responses."
            ),
            (
                "Only five training seeds; bootstrap coverage and discovery claims are "
                "not established."
            ),
            (
                "Grid comparisons keep RMS SNR fixed with independent readout streams; "
                "they do not hold detector exposure or photon budget fixed."
            ),
        ],
        "dependency_versions": {
            "python": platform.python_version(),
            **{
                name: version(name)
                for name in ("numpy", "scipy", "scikit-learn", "joblib", "PyYAML")
            },
        },
    }
    _write_json(output / "result.json", result)
    for seed in config.seeds:
        seed_ok = True
        for suite in config.suites:
            for step in suite.grid_steps_nm:
                cell_id = f"{suite.suite_id}-seed-{seed}-step-{step:g}"
                try:
                    cell = _seed_grid_run(
                        config,
                        suite,
                        catalogs[suite.suite_id],
                        step,
                        seed,
                        output / "runs" / cell_id,
                    )
                    result["runs"].append(cell)
                    print(f"Completed {cell_id}", flush=True)
                except Exception as exc:
                    seed_ok = False
                    result["failed_runs"].append(
                        {
                            "seed": seed,
                            "suite_id": suite.suite_id,
                            "grid_step_nm": step,
                            "error": str(exc),
                        }
                    )
                    print(f"Failed {cell_id}: {exc}", flush=True)
                _write_json(output / "result.json", result)
        if seed_ok:
            result["successful_seeds"].append(seed)
    if result["successful_seeds"]:
        result.update(aggregate_results(config, output, result["runs"], result["successful_seeds"]))
    result["status"] = (
        "completed" if len(result["successful_seeds"]) == len(config.seeds) else "incomplete"
    )
    result["completed_at"] = datetime.now(UTC).isoformat()
    if hashlib.sha256((output / "preregistration.json").read_bytes()).hexdigest() != prereg_hash:
        raise ValueError("Preregistered protocol was modified during prediction")
    _write_json(output / "result.json", result)
    return output
