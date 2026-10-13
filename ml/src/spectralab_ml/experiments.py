from __future__ import annotations

import csv
import itertools
import json
import platform
import uuid
from dataclasses import asdict, replace
from datetime import UTC, datetime
from importlib.metadata import distributions
from pathlib import Path
from typing import Any

import numpy as np
import yaml

from spectralab_ml.benchmark import measure_inference
from spectralab_ml.config import (
    MODEL_IDS,
    ExperimentConfig,
    load_experiment_config,
    load_project_config,
    load_reference_catalog,
    repository_root,
    stable_hash,
)
from spectralab_ml.datasets import build_demo_dataset
from spectralab_ml.evaluation import evaluate_multilabel
from spectralab_ml.models import load_artifact
from spectralab_ml.preprocessing import SpectrumPreprocessor
from spectralab_ml.simulation import GenerationConfig, generate_spectrum
from spectralab_ml.training import train_and_save_models

MODELS = MODEL_IDS


def _test_combinations(
    class_order: tuple[str, ...], protocol: str, sizes: tuple[int, ...] | list[int] | None = None
) -> list[tuple[str, ...]]:
    if protocol == "unseen_combinations":
        pairs = [tuple(class_order[i : i + 2]) for i in range(0, len(class_order) - 1, 2)]
        return [
            combo
            for combo in pairs + ([tuple(class_order[:3])] if len(class_order) >= 3 else [])
            if len(combo) in (sizes or [2, 3])
        ]
    return [
        combo
        for size in (sizes or [1, 2, 3])
        for combo in list(itertools.combinations(class_order, size))[: max(len(class_order), 3)]
    ]


def _make_condition_test(
    *,
    catalog: Any,
    preprocessor: SpectrumPreprocessor,
    class_order: tuple[str, ...],
    combinations: list[tuple[str, ...]],
    factor: str,
    value: float | None,
    seed: int,
    generation: dict[str, Any] | None = None,
    ion_stage: int = 1,
) -> tuple[np.ndarray, np.ndarray, list[str]]:
    rows, labels, ids = [], [], []
    for index, combo in enumerate(combinations):
        params = dict(
            elements=combo,
            component_weights=tuple(1.0 - 0.15 * i for i in range(len(combo))),
            wavelength_start_nm=preprocessor.wavelength_start_nm,
            wavelength_end_nm=preprocessor.wavelength_end_nm,
            snr_db=20.0,
            instrumental_fwhm_nm=0.5,
            sampling_step_nm=preprocessor.model_step_nm,
            seed=seed * 1000 + index,
        )
        params.update(generation or {})
        if "ion_stage" in GenerationConfig.__dataclass_fields__:
            params["ion_stage"] = ion_stage
        params[factor] = value
        spectrum = generate_spectrum(
            GenerationConfig(**params),
            catalog,
            class_order=class_order,
            base_sample_id=f"controlled-{seed}-{index:03d}",
        )
        rows.append(preprocessor.transform(spectrum.wavelength_nm, spectrum.intensity))
        labels.append([spectrum.true_labels[e] for e in class_order])
        ids.append(spectrum.base_sample_id)
    return np.stack(rows), np.asarray(labels), ids


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8"
    )
    temporary.replace(path)


def _prepare(
    config_path: Path,
    config: ExperimentConfig,
    reference_cache: Path | None = None,
    reference_sha256: str | None = None,
) -> tuple[Any, Any, tuple[str, ...], dict[str, str]]:
    project_path = config_path.parent / config.project_config if config.project_config else None
    project = load_project_config(project_path)
    config.validate_project(project)
    classes = config.elements or project.elements
    cache = reference_cache or (
        config_path.parent / config.reference_cache if config.reference_cache else None
    )
    catalog = load_reference_catalog(
        project, cache, reference_sha256 or config.reference_sha256, classes
    )
    if not _test_combinations(classes, config.protocol, config.mixture_sizes):
        raise ValueError("No test combinations for the selected protocol and classes")
    if "cnn-1d" in config.models:
        # Resolve optional dependency before an output directory is created.
        from spectralab_ml.models.cnn import CNN1D, cnn_available

        if not cnn_available():
            raise ValueError("cnn-1d requires the optional cnn runtime extra")
        CNN1D(seed=config.seed)
    source_hash = stable_hash(
        {
            "metadata": {k: v for k, v in catalog.metadata.items() if k != "imported_at"},
            "lines": [asdict(line) for line in catalog.lines],
        }
    )
    effective = {"experiment": config.to_dict(), "project": asdict(project)}
    protocol = {k: v for k, v in config.to_dict().items() if k not in {"seed", "seeds", "run_id"}}
    hashes = {
        "config_hash": stable_hash(effective),
        "source_hash": source_hash,
        "protocol_hash": stable_hash(
            {"experiment": protocol, "project": asdict(project), "source_hash": source_hash}
        ),
    }
    return project, catalog, classes, hashes


def run_experiment(
    config_path: Path,
    output_root: Path | None = None,
    *,
    seed: int | None = None,
    reference_cache: Path | None = None,
    reference_sha256: str | None = None,
    _config: ExperimentConfig | None = None,
) -> Path:
    config = _config or load_experiment_config(config_path)
    if seed is not None:
        # Re-validate overrides with the exact same public schema.
        config = ExperimentConfig.from_mapping({**config.to_dict(), "seed": seed})
    project, catalog, classes, hashes = _prepare(
        config_path, config, reference_cache, reference_sha256
    )
    preprocessor = SpectrumPreprocessor(
        project.wavelength_start_nm, project.wavelength_end_nm, project.model_step_nm
    )
    test_combinations = _test_combinations(classes, config.protocol, config.mixture_sizes)
    run_id = f"{config.run_id}-{datetime.now(UTC):%Y%m%dT%H%M%S}-{uuid.uuid4().hex[:8]}"
    output = (output_root or repository_root() / "reports" / "experiments") / run_id
    output.mkdir(parents=True, exist_ok=False)
    result = {
        "schema_version": 1,
        "artifact_type": "experiment",
        "run_id": run_id,
        "status": "running",
        "created_at": datetime.now(UTC).isoformat(),
        "demo": catalog.source_name == "demo_fixture",
        "reference_source": catalog.source_name,
        "reference_metadata": catalog.metadata,
        "configuration": config.to_dict(),
        "seed": config.seed,
        "class_order": classes,
        "protocol": config.protocol,
        "runs": [],
        **hashes,
        "limitations": "Synthetic controlled benchmark; not validation on real measurements.",
    }
    _write_json(output / "result.json", result)
    (output / "config.yaml").write_text(
        yaml.safe_dump(config.to_dict(), sort_keys=False), encoding="utf-8"
    )
    try:
        heldout = (
            {tuple(sorted(c)) for c in test_combinations}
            if config.protocol == "unseen_combinations"
            else set()
        )
        allowed = [
            c
            for size in (1, 2, 3)
            for c in itertools.combinations(classes, size)
            if tuple(sorted(c)) not in heldout
        ]
        dataset = build_demo_dataset(
            catalog,
            preprocessor,
            classes,
            seed=config.seed,
            base_samples=config.base_samples,
            variations=config.variations,
            combinations=allowed,
            ion_stage=project.ion_stage,
        )
        actual_train = {
            tuple(sorted(r["parameters"]["elements"]))
            for r in dataset.records
            if r["split"] == "train"
        }
        assert not (actual_train & heldout)
        assert set(itertools.chain.from_iterable(actual_train)) == set(classes)
        dataset.save(output / "dataset")
        train_and_save_models(
            dataset,
            catalog,
            preprocessor,
            output / "models",
            seed=config.seed,
            model_ids=list(config.models),
            calibrate=config.calibrate,
            timing_warmups=config.timing_warmups,
            timing_repeats=config.timing_repeats,
        )
        fitted = {
            name: load_artifact(
                output / "models" / name,
                expected_class_order=classes,
                expected_reference=catalog.metadata,
            )
            for name in config.models
        }
        result.update(
            dataset_hash=dataset.dataset_hash,
            split_hash=dataset.split_hash,
            split_ids=dataset.split_ids,
            preprocessing=preprocessor.to_dict(),
            train_combinations=sorted(actual_train),
            heldout_combinations=sorted(heldout),
            test_combinations=test_combinations,
            dependency_versions={
                "python": platform.python_version(),
                **{d.metadata["Name"]: d.version for d in distributions() if d.metadata["Name"]},
            },
            model_artifacts={
                name: {
                    "path": f"models/{name}",
                    "version": m.model_version,
                    "sha256": m.model_sha256,
                    "thresholds": m.thresholds,
                    "score_kind": m.score_kind,
                    "calibrated": m.calibrated,
                    "validation": m.metrics["validation"],
                }
                for name, (_, _, m) in fitted.items()
            },
        )
        first_ids = None
        for value in config.values:
            X, y, ids = _make_condition_test(
                catalog=catalog,
                preprocessor=preprocessor,
                class_order=classes,
                combinations=test_combinations,
                factor=config.factor,
                value=value,
                seed=config.seed,
                generation=config.generation,
                ion_stage=project.ion_stage,
            )
            if first_ids is None:
                first_ids = ids
            assert first_ids == ids and not set(ids) & set(
                itertools.chain.from_iterable(dataset.split_ids.values())
            )
            for name, (estimator, _, manifest) in fitted.items():
                scores = estimator.scores(X)
                metrics = evaluate_multilabel(
                    y, scores, np.asarray([manifest.thresholds[e] for e in classes]), classes
                )
                metrics.update(
                    measure_inference(
                        lambda est=estimator, data=X: est.scores(data),
                        len(X),
                        warmups=config.timing_warmups,
                        repeats=config.timing_repeats,
                    )
                )
                result["runs"].append(
                    {
                        "model_id": name,
                        "factor": config.factor,
                        "value": value,
                        "requested_value": value,
                        "test_base_sample_ids": ids,
                        "metrics": metrics,
                    }
                )
            _write_json(output / "result.json", result)
        result.update(
            status="completed",
            completed_at=datetime.now(UTC).isoformat(),
            controlled_test_base_sample_ids=first_ids,
        )
        keys = ["micro_f1", "macro_f1", "exact_match_accuracy", "hamming_loss"]
        with (output / "metrics.csv").open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=["model_id", "factor", "value", *keys])
            writer.writeheader()
            for run in result["runs"]:
                writer.writerow(
                    {k: run[k] for k in ("model_id", "factor", "value")}
                    | {k: run["metrics"][k] for k in keys}
                )
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        fig, axis = plt.subplots(figsize=(8, 4))
        for name in config.models:
            axis.plot(
                range(len(config.values)),
                [r["metrics"]["micro_f1"] for r in result["runs"] if r["model_id"] == name],
                marker="o",
                label=name,
            )
        axis.set_xticks(range(len(config.values)), [str(v) for v in config.values])
        axis.set(
            xlabel=config.factor,
            ylabel="micro-F1",
            title=f"Synthetic {catalog.source_name}: controlled degradation",
        )
        axis.legend()
        fig.tight_layout()
        fig.savefig(output / "robustness.png", dpi=150)
        plt.close(fig)
        _write_json(output / "result.json", result)
    except Exception as exc:
        result.update(status="failed", error=str(exc), completed_at=datetime.now(UTC).isoformat())
        _write_json(output / "result.json", result)
        raise
    return output


def _summary(values: list[float]) -> dict[str, Any]:
    return {
        "mean": float(np.mean(values)) if values else None,
        "std": float(np.std(values, ddof=1)) if len(values) > 1 else None,
        "count": len(values),
    }


def aggregate_series(
    results: list[dict[str, Any]],
    expected_seeds: list[int],
    config: ExperimentConfig,
    *,
    protocol_hash: str,
    source_hash: str,
) -> dict[str, Any]:
    """Recompute paired aggregates from original run records; never impute failures."""
    expected_cells = {
        (model, config.factor, value) for model in config.models for value in config.values
    }
    by_seed: dict[int, list[dict[str, Any]]] = {}
    for result in results:
        by_seed.setdefault(result.get("seed"), []).append(result)
    successful, incomplete, failed, matched = [], [], [], []
    for seed in expected_seeds:
        candidates = by_seed.get(seed, [])
        if len(candidates) != 1:
            incomplete.append(
                {
                    "seed": seed,
                    "run_id": None,
                    "error": "missing run" if not candidates else "duplicate seed runs",
                }
            )
            continue
        result = candidates[0]
        entry = {k: result.get(k) for k in ("seed", "run_id", "config_hash", "source_hash")}
        if result.get("status") == "failed":
            failed.append({**entry, "error": result.get("error", "run failed")})
            continue
        rows = result.get("runs", [])
        cells = [(row.get("model_id"), row.get("factor"), row.get("value")) for row in rows]
        ids = [row.get("test_base_sample_ids") for row in rows]
        reason = None
        if result.get("status") != "completed":
            reason = f"run status is {result.get('status', 'missing')}"
        elif (
            result.get("protocol_hash") != protocol_hash or result.get("source_hash") != source_hash
        ):
            reason = "incompatible protocol or reference source"
        elif len(cells) != len(expected_cells) or set(cells) != expected_cells:
            reason = "incomplete or duplicated model/condition matrix"
        elif not ids or not ids[0] or any(v != ids[0] for v in ids):
            reason = "unmatched test base sample IDs"
        elif not result.get("config_hash"):
            reason = "missing configuration hash"
        if reason:
            incomplete.append({**entry, "error": reason})
        else:
            successful.append(seed)
            matched.append(result)
    aggregates = []
    for model in config.models:
        for value in config.values:
            cells = [
                next(
                    row["metrics"]
                    for row in result["runs"]
                    if (row["model_id"], row["factor"], row["value"])
                    == (model, config.factor, value)
                )
                for result in matched
            ]
            metric_names = (
                "micro_f1",
                "macro_f1",
                "exact_match_accuracy",
                "hamming_loss",
                "inference_ms_per_spectrum",
            )
            metrics = {
                name: _summary([m[name] for m in cells if m.get(name) is not None])
                for name in metric_names
            }
            for subset in ("single", "mixture"):
                metrics[subset] = {
                    name: _summary(
                        [m[subset][name] for m in cells if m.get(subset, {}).get(name) is not None]
                    )
                    for name in ("micro_f1", "exact_match_accuracy", "count")
                }
            aggregates.append(
                {"model_id": model, "factor": config.factor, "value": value, "metrics": metrics}
            )
    return {
        "successful_seeds": successful,
        "failed_runs": failed,
        "incomplete_runs": incomplete,
        "aggregates": aggregates,
        "aggregation_policy": "complete matched seeds only; ddof=1; null std for n<2",
    }


def run_series(
    config_path: Path,
    output_root: Path | None = None,
    *,
    seeds: list[int] | None = None,
    reference_cache: Path | None = None,
    reference_sha256: str | None = None,
    run_id: str | None = None,
) -> Path:
    config = load_experiment_config(config_path)
    if seeds is not None:
        config = ExperimentConfig.from_mapping({**config.to_dict(), "seeds": seeds})
    _, catalog, classes, hashes = _prepare(config_path, config, reference_cache, reference_sha256)
    if run_id is not None:
        ExperimentConfig.from_mapping({"schema_version": 1, "run_id": run_id})
    series_id = (
        run_id or f"{config.run_id}-series-{datetime.now(UTC):%Y%m%dT%H%M%S}-{uuid.uuid4().hex[:8]}"
    )
    output = (output_root or repository_root() / "reports" / "experiments") / series_id
    output.mkdir(parents=True, exist_ok=False)
    result = {
        "schema_version": 1,
        "artifact_type": "experiment_series",
        "run_id": series_id,
        "status": "running",
        "created_at": datetime.now(UTC).isoformat(),
        "protocol": config.protocol,
        "reference_source": catalog.source_name,
        "reference_metadata": catalog.metadata,
        "demo": catalog.source_name == "demo_fixture",
        "class_order": classes,
        "expected_seeds": list(config.seeds),
        "configuration": config.to_dict(),
        "seed_runs": [],
        **hashes,
    }
    raw_results: list[dict[str, Any]] = []
    result.update(
        aggregate_series(
            raw_results,
            list(config.seeds),
            config,
            protocol_hash=hashes["protocol_hash"],
            source_hash=hashes["source_hash"],
        )
    )
    _write_json(output / "result.json", result)
    (output / "config.yaml").write_text(yaml.safe_dump(config.to_dict()), encoding="utf-8")
    for seed in config.seeds:
        before = set((output / "runs").glob("*/result.json"))
        try:
            run_path = run_experiment(
                config_path,
                output / "runs",
                seed=seed,
                reference_cache=reference_cache,
                reference_sha256=reference_sha256,
                _config=config,
            )
            raw = json.loads((run_path / "result.json").read_text())
        except Exception as exc:
            created = set((output / "runs").glob("*/result.json")) - before
            raw = (
                json.loads(next(iter(created)).read_text())
                if len(created) == 1
                else {
                    "seed": seed,
                    "run_id": None,
                    "status": "failed",
                    "error": str(exc),
                    "config_hash": _prepare(
                        config_path, replace(config, seed=seed), reference_cache, reference_sha256
                    )[3]["config_hash"],
                    "source_hash": hashes["source_hash"],
                }
            )
        raw_results.append(raw)
        result["seed_runs"].append(
            {
                k: raw.get(k)
                for k in ("seed", "run_id", "status", "config_hash", "source_hash", "error")
            }
        )
        result.update(
            aggregate_series(
                raw_results,
                list(config.seeds),
                config,
                protocol_hash=hashes["protocol_hash"],
                source_hash=hashes["source_hash"],
            )
        )
        _write_json(output / "result.json", result)
    result.update(
        status="completed"
        if len(result["successful_seeds"]) == len(config.seeds)
        else "incomplete",
        completed_at=datetime.now(UTC).isoformat(),
    )
    _write_json(output / "result.json", result)
    return output
