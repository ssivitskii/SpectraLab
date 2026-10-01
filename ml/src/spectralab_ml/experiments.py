from __future__ import annotations

import csv
import itertools
import json
import platform
import re
import uuid
from datetime import UTC, datetime
from importlib.metadata import distributions
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import yaml

from spectralab_ml.config import load_project_config, repository_root
from spectralab_ml.data.reference import load_demo_catalog
from spectralab_ml.datasets import build_demo_dataset
from spectralab_ml.evaluation import evaluate_multilabel
from spectralab_ml.models import load_artifact
from spectralab_ml.preprocessing import SpectrumPreprocessor
from spectralab_ml.simulation import GenerationConfig, generate_spectrum
from spectralab_ml.training import train_and_save_models

FACTORS = {"snr_db", "instrumental_fwhm_nm", "sampling_step_nm"}
MODELS = {"baseline-nnls", "logistic-ovr", "random-forest"}


def _test_combinations(
    class_order: tuple[str, ...], protocol: str, sizes: list[int] | None = None
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
) -> tuple[np.ndarray, np.ndarray, list[str]]:
    rows = []
    labels = []
    ids = []
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


def run_experiment(config_path: Path, output_root: Path | None = None) -> Path:
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    seed = int(config.get("seed", 42))
    prefix = str(config.get("run_id", config_path.stem))
    protocol = config.get("protocol", "controlled_sweep")
    factor = config.get("factor", "snr_db")
    models = config.get("models", sorted(MODELS))
    values = config.get("values", [20])
    if protocol not in {"controlled_sweep", "unseen_combinations"} or factor not in FACTORS:
        raise ValueError("Unknown experiment protocol or controlled factor")
    if not models or len(set(models)) != len(models) or not set(models) <= MODELS or not values:
        raise ValueError("Models and sweep values must be nonempty and valid")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,60}", prefix) or seed < 0:
        raise ValueError("Invalid run ID or seed")
    root = repository_root()
    project = load_project_config()
    classes = tuple(config.get("elements", project.elements))
    if (
        len(classes) < 2
        or len(set(classes)) != len(classes)
        or not set(classes) <= set(project.elements)
    ):
        raise ValueError("Invalid experiment classes")
    preprocessor = SpectrumPreprocessor(
        project.wavelength_start_nm, project.wavelength_end_nm, project.model_step_nm
    )
    sizes = config.get("mixture_sizes", [2, 3] if protocol == "unseen_combinations" else [1, 2, 3])
    if not sizes or not set(sizes) <= {1, 2, 3}:
        raise ValueError("Invalid mixture sizes")
    test_combinations = _test_combinations(classes, protocol, sizes)
    if not test_combinations:
        raise ValueError("No test combinations")
    # Validate each condition before creating a run directory.
    normalized_values = [None if v in (None, "clean") else float(v) for v in values]
    for value in normalized_values:
        GenerationConfig(
            elements=(classes[0],), component_weights=(1.0,), **{factor: value}
        ).validate()
    catalog = load_demo_catalog(root)
    run_id = f"{prefix}-{datetime.now(UTC):%Y%m%dT%H%M%S}-{uuid.uuid4().hex[:8]}"
    output = (output_root or root / "reports" / "experiments") / run_id
    output.mkdir(parents=True, exist_ok=False)
    result = {
        "run_id": run_id,
        "status": "running",
        "created_at": datetime.now(UTC).isoformat(),
        "demo": True,
        "reference_source": catalog.source_name,
        "reference_metadata": catalog.metadata,
        "configuration": config,
        "seed": seed,
        "class_order": classes,
        "protocol": protocol,
        "runs": [],
        "limitations": (
            "Демонстрация на синтетических данных demo_fixture; "
            "научная валидация и проверка на реальных измерениях не выполнены."
        ),
    }
    _write_json(output / "result.json", result)
    (output / "config.yaml").write_text(
        yaml.safe_dump(config, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    try:
        heldout = (
            {tuple(sorted(c)) for c in test_combinations}
            if protocol == "unseen_combinations"
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
            seed=seed,
            base_samples=int(config.get("base_samples", 36)),
            variations=int(config.get("variations", 2)),
            combinations=allowed,
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
            dataset, catalog, preprocessor, output / "models", seed=seed, model_ids=models
        )
        fitted = {
            name: load_artifact(
                output / "models" / name,
                expected_class_order=classes,
                expected_reference=catalog.metadata,
            )
            for name in models
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
        runs = []
        first_ids = None
        for raw_value, value in zip(values, normalized_values, strict=True):
            X, y, ids = _make_condition_test(
                catalog=catalog,
                preprocessor=preprocessor,
                class_order=classes,
                combinations=test_combinations,
                factor=factor,
                value=value,
                seed=seed,
            )
            if first_ids is None:
                first_ids = ids
            assert first_ids == ids and not set(ids) & set(
                itertools.chain.from_iterable(dataset.split_ids.values())
            )
            for name, (estimator, _, manifest) in fitted.items():
                scores = estimator.scores(X)
                metrics = evaluate_multilabel(
                    y,
                    scores,
                    np.asarray([manifest.thresholds[e] for e in classes]),
                    classes,
                    timed_predict=lambda est=estimator, data=X: est.scores(data),
                )
                runs.append(
                    {
                        "model_id": name,
                        "factor": factor,
                        "value": value,
                        "requested_value": raw_value,
                        "test_base_sample_ids": ids,
                        "metrics": metrics,
                    }
                )
        result.update(
            status="completed",
            runs=runs,
            completed_at=datetime.now(UTC).isoformat(),
            controlled_test_base_sample_ids=first_ids,
        )
        with (output / "metrics.csv").open("w", newline="", encoding="utf-8") as stream:
            keys = ["micro_f1", "macro_f1", "exact_match_accuracy", "hamming_loss"]
            writer = csv.DictWriter(stream, fieldnames=["model_id", "factor", "value", *keys])
            writer.writeheader()
            for run in runs:
                writer.writerow(
                    {k: run[k] for k in ("model_id", "factor", "value")}
                    | {k: run["metrics"][k] for k in keys}
                )
        fig, axis = plt.subplots(figsize=(8, 4))
        for name in models:
            axis.plot(
                range(len(values)),
                [r["metrics"]["micro_f1"] for r in runs if r["model_id"] == name],
                marker="o",
                label=name,
            )
        axis.set_xticks(range(len(values)), [str(v) for v in values])
        axis.set(
            xlabel=factor, ylabel="micro-F1", title="Synthetic demo_fixture: controlled degradation"
        )
        axis.legend()
        fig.tight_layout()
        fig.savefig(output / "robustness.png", dpi=150)
        plt.close(fig)
        _write_json(output / "result.json", result)
    except Exception as exc:
        result.update(status="failed", error=str(exc))
        _write_json(output / "result.json", result)
        raise
    return output
