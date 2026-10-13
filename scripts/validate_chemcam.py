"""Reproduce the preregistered ChemCam synthetic-to-real positive-label validation."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import subprocess
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import numpy as np
from spectralab_ml.config import repository_root
from spectralab_ml.data.reference import ReferenceCatalog
from spectralab_ml.evaluation import evaluate_multilabel
from spectralab_ml.evaluation.external import inspect_external_test
from spectralab_ml.evaluation.real_validation import (
    ObservedWindowEstimator,
    interpolate_observed,
    make_locked_synthetic_dataset,
    normalize_observed,
    object_sha256,
    observation_mask,
    positive_recall_report,
)
from spectralab_ml.models import (
    BaselineNNLS,
    LogisticOVR,
    ModelManifest,
    RandomForestMultiLabel,
    save_artifact,
)
from spectralab_ml.models.estimators import select_thresholds
from spectralab_ml.preprocessing import SpectrumPreprocessor
from spectralab_ml.training import make_reference_templates


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n")


def acquire(raw_dir: Path, source: dict, download: bool) -> None:
    raw_dir.mkdir(parents=True, exist_ok=True)
    for record in source["downloads"]:
        path = raw_dir / record["name"]
        if not path.exists() and download:
            temporary = path.with_suffix(path.suffix + ".download")
            # curl uses the host trust store. TLS verification remains enabled.
            subprocess.run(
                [
                    "curl",
                    "--fail",
                    "--location",
                    "--silent",
                    "--show-error",
                    "--max-time",
                    "120",
                    record["url"],
                    "-o",
                    str(temporary),
                ],
                check=True,
            )
            if hashlib.sha256(temporary.read_bytes()).hexdigest() != record["sha256"]:
                temporary.unlink()
                raise ValueError("Remote ChemCam file changed; review provenance before replacing")
            temporary.replace(path)
        if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != record["sha256"]:
            raise ValueError(f"Pinned source checksum mismatch: {record['name']}")


def prepare(raw_dir: Path, output: Path, protocol: dict, source: dict) -> tuple:
    if (
        not protocol["test_locked_before_prediction"]
        or protocol["development_sample_ids"]
        or protocol["development_session_ids"]
    ):
        raise ValueError("This protocol requires a locked external-only campaign")
    if source["wavelength_medium"] != "vacuum" or source["units"] != "nm":
        raise ValueError("This experiment requires documented vacuum wavelengths in nm")
    for name, expected in protocol["input_sha256"].items():
        if hashlib.sha256((raw_dir / name).read_bytes()).hexdigest() != expected:
            raise ValueError("Protocol input checksum mismatch")
    matrix_path = raw_dir / "msl_ccam_libs_calib.csv"
    with matrix_path.open() as stream:
        column_names = next(csv.reader(stream))[1:]
    matrix = np.loadtxt(matrix_path, delimiter=",", skiprows=1)
    wavelengths = matrix[:, 0]
    with (raw_dir / "ccam_calibration_compositions.csv").open() as stream:
        chemistry = list(csv.DictReader(stream))
    counts = Counter(row["Spectrum Name"].upper() for row in chemistry)
    by_name = {
        row["Spectrum Name"].upper(): row
        for row in chemistry
        if counts[row["Spectrum Name"].upper()] == 1
    }
    expected = sorted(name for name in set(column_names) if name in by_name)
    if expected != protocol["test_sample_ids"]:
        raise ValueError("Physical sample selection differs from locked test")
    classes = tuple(protocol["classes"])
    cfg = protocol["preprocessing"]
    processor = SpectrumPreprocessor(cfg["grid_start_nm"], cfg["grid_end_nm"], cfg["step_nm"])
    mask = observation_mask(processor.model_grid, cfg["observed_intervals_nm"])
    prepared_dir = output / "admission"
    prepared_dir.mkdir()
    samples, records, features, labels, groups = [], [], [], [], []
    oxide_for_element = {"Na": "Na2O", "Mg": "MgO", "Ca": "CaO", "Fe": "FeOT"}
    for name in expected:
        values = {}
        truth = {element: None for element in classes}
        for element, oxide in oxide_for_element.items():
            text = by_name[name][oxide].strip()
            value = float(text) if text else None
            if value is not None and not np.isfinite(value):
                raise ValueError("Nonfinite standard composition")
            values[oxide] = value
            if value is not None and value > 0:
                truth[element] = True
        columns = [i + 1 for i, target in enumerate(column_names) if target == name]
        filename = f"{name}.csv"
        path = prepared_dir / filename
        np.savetxt(
            path,
            np.column_stack([wavelengths, matrix[:, columns].mean(axis=1)]),
            delimiter=",",
            header="wavelength_nm,intensity",
            comments="",
            fmt="%.12g",
        )
        samples.append(
            {
                "sample_id": name,
                "session_id": protocol["acquisition_session_id"],
                "filename": filename,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "labels": truth,
                "oxide_wt_percent": values,
                "original_columns": columns,
                "derived_file": (
                    "replicate average for admission only; predictions use each original column"
                ),
            }
        )
        for replicate, column in enumerate(columns):
            features.append(
                interpolate_observed(
                    wavelengths,
                    matrix[:, column],
                    processor.model_grid,
                    cfg["observed_intervals_nm"],
                    max_gap_nm=cfg["max_gap_nm"],
                )
            )
            labels.append([truth[element] is True for element in classes])
            groups.append(name)
            records.append(
                {
                    "sample_id": name,
                    "replicate": replicate + 1,
                    "source_column": column,
                    "session_id": protocol["acquisition_session_id"],
                    "labels": truth,
                    "oxide_wt_percent": values,
                }
            )
    manifest = {
        **source,
        "samples": samples,
        "test_locked_before_prediction": True,
        "development_sample_ids": [],
        "development_session_ids": [],
    }
    admission = inspect_external_test(manifest, prepared_dir)
    if admission["status"] != "admitted":
        raise ValueError(f"External source not admitted: {admission['blockers']}")
    write_json(output / "intake_manifest.json", manifest)
    write_json(output / "admission.json", admission)
    write_json(output / "test_records.json", records)
    return processor, mask, np.asarray(features), np.asarray(labels, dtype=bool), groups, records


def run(root: Path, download: bool) -> Path:
    protocol_path = root / "data/reference/chemcam_external_protocol.json"
    source_path = root / "data/reference/chemcam_external_source.json"
    protocol = json.loads(protocol_path.read_text())
    source = json.loads(source_path.read_text())
    # Fail closed when changing a locked scientific convention: edits need a new protocol.
    cfg = protocol["synthetic_development"]
    conventions = {
        "split": [0.6, 0.2, 0.2],
        "mixture_sizes": [1, 2, 3],
        "component_weights": [0.35, 1.2],
        "snr_db": [20.0, 40.0],
        "fwhm_nm": 0.5,
    }
    if any(cfg[key] != value for key, value in conventions.items()):
        raise ValueError("Locked synthetic conventions do not match this implementation")
    if protocol["preprocessing"]["wavelength_medium"] != source["wavelength_medium"]:
        raise ValueError("Protocol and source wavelength media differ")
    raw_dir = root / "data/raw/chemcam_external"
    acquire(raw_dir, source, download)
    run_id = f"chemcam-external-{datetime.now(UTC):%Y%m%dT%H%M%S}-{uuid4().hex[:8]}"
    output = root / "reports/external" / run_id
    output.mkdir(parents=True)
    write_json(output / "protocol.json", protocol)
    write_json(output / "source.json", source)
    result = {
        "schema_version": 1,
        "run_id": run_id,
        "status": "running",
        "created_at": datetime.now(UTC).isoformat(),
        "protocol_hash": object_sha256(protocol),
        "source_hash": object_sha256(source),
        "implementation_sha256": {
            str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in [
                *sorted((root / "ml/src/spectralab_ml").rglob("*.py")),
                Path(__file__).resolve(),
            ]
        },
        "expected_seeds": protocol["model_seeds"],
        "source": source,
        "model_selection_on_real_test": False,
        "runs": [],
    }
    write_json(output / "result.json", result)
    try:
        processor, mask, real_X, known_positive, groups, records = prepare(
            raw_dir, output, protocol, source
        )
        classes = tuple(protocol["classes"])
        catalog = ReferenceCatalog.from_cache(
            root / "data/reference/nist_neutral_350_800/catalog.json"
        )
        templates = make_reference_templates(catalog, processor, classes)
        normalized_templates = normalize_observed(templates, mask)
        np.savez_compressed(
            output / "external_features.npz",
            X=real_X,
            positive_mask=known_positive,
            observation_mask=mask,
            wavelength_nm=processor.model_grid,
            physical_sample_ids=np.asarray(groups),
        )
        for seed in protocol["model_seeds"]:
            seed_dir = output / f"seed-{seed}"
            seed_dir.mkdir()
            dataset = make_locked_synthetic_dataset(
                templates,
                classes,
                catalog.metadata,
                seed=seed,
                base_samples=cfg["base_samples"],
                variations=cfg["variations"],
                mask=mask,
            )
            write_json(seed_dir / "synthetic_records.json", dataset.records)
            np.savez_compressed(
                seed_dir / "synthetic_splits.npz",
                **{
                    f"{part}_{key}": getattr(getattr(dataset, part), key)
                    for part in ("train", "validation", "test")
                    for key in ("X", "y")
                },
            )
            bases = {
                "baseline-nnls": BaselineNNLS(normalized_templates),
                "logistic-ovr": LogisticOVR(seed=seed),
                "random-forest": RandomForestMultiLabel(seed=seed),
            }
            seed_result = {
                "seed": seed,
                "dataset_hash": dataset.dataset_hash,
                "split_hash": dataset.split_hash,
                "split_ids": dataset.split_ids,
                "models": {},
            }
            for model_id in protocol["models"]:
                estimator = ObservedWindowEstimator(bases[model_id], mask.copy())
                estimator.fit(dataset.train.X, dataset.train.y)
                thresholds, selection = select_thresholds(
                    estimator.scores(dataset.validation.X),
                    dataset.validation.y,
                    default=0.15 if model_id == "baseline-nnls" else 0.5,
                )
                selection["scope"] = "synthetic development only; real campaign entirely held out"
                selection["base_sample_ids"] = dataset.split_ids["validation"]
                synthetic = evaluate_multilabel(
                    dataset.test.y, estimator.scores(dataset.test.X), thresholds, classes
                )
                # Freeze a complete artifact before the first real prediction for this model.
                manifest = ModelManifest.create(
                    model_id=model_id,
                    model_type=type(estimator).__name__,
                    class_order=classes,
                    preprocessor=processor,
                    thresholds=dict(zip(classes, thresholds.tolist(), strict=True)),
                    threshold_selection=selection,
                    score_kind=estimator.score_kind,
                    calibrated=False,
                    dataset_hash=dataset.dataset_hash,
                    split_hash=dataset.split_hash,
                    seed=seed,
                    metrics={"synthetic_test": synthetic},
                    reference_metadata=catalog.metadata,
                    generator_parameters={
                        "protocol": cfg,
                        "observation_profile": protocol["preprocessing"],
                    },
                    split_ids=dataset.split_ids,
                )
                save_artifact(seed_dir / "models" / model_id, estimator, processor, manifest)
                scores = estimator.scores(real_X)
                detections = scores >= thresholds
                report = positive_recall_report(detections, known_positive, groups, classes)
                np.savez_compressed(
                    seed_dir / f"{model_id}-predictions.npz",
                    scores=scores,
                    thresholds=thresholds,
                    detections=detections,
                )
                rows = []
                for record, score, detected in zip(records, scores, detections, strict=True):
                    rows.append(
                        {
                            **record,
                            "scores": dict(zip(classes, score.tolist(), strict=True)),
                            "detected_elements": [
                                e for e, d in zip(classes, detected, strict=True) if d
                            ],
                        }
                    )
                write_json(seed_dir / f"{model_id}-predictions.json", rows)
                seed_result["models"][model_id] = {
                    "synthetic_test": synthetic,
                    "external": report,
                    "thresholds": dict(zip(classes, thresholds.tolist(), strict=True)),
                }
            write_json(seed_dir / "result.json", seed_result)
            result["runs"].append(seed_result)
            write_json(output / "result.json", result)
            print(f"External validation seed {seed} completed", flush=True)
        result["aggregate"] = {}
        for model_id in protocol["models"]:
            model_reports = [run["models"][model_id]["external"] for run in result["runs"]]
            mean_metrics = {}
            for element in ("all", *classes):
                values = [
                    r["primary"] if element == "all" else r["per_element"][element]
                    for r in model_reports
                ]
                recalls = [v["recall"] for v in values if v["recall"] is not None]
                mean_metrics[element] = {
                    "recall_mean": float(np.mean(recalls)) if recalls else None,
                    "recall_seed_std": float(np.std(recalls, ddof=1)) if len(recalls) > 1 else None,
                    "seeds": len(recalls),
                    "physical_sample_support": values[0]["physical_sample_support"],
                    "per_seed_sample_ci95": [v["ci95"] for v in values],
                }
            result["aggregate"][model_id] = mean_metrics
        result.update(
            status="completed",
            completed_at=datetime.now(UTC).isoformat(),
            class_order=classes,
            reference_metadata=catalog.metadata,
            physical_samples=len(set(groups)),
            replicate_spectra=len(groups),
            limitations=[
                "Only positive-reference recall is identifiable; no precision/F1 claim.",
                "H and He are unlabelled. Four elements evaluated where composition is positive.",
                "One external acquisition campaign; no real transfer fitting occurred.",
                "Neutral Gaussian training omits ionic/molecular plasma physics and interferents.",
                "Observation windows fixed from documentation before prediction.",
                "Research models use an observation mask; do not replace the general API models.",
            ],
        )
        write_json(output / "result.json", result)
    except BaseException as exc:
        result.update(status="failed", error=type(exc).__name__ + ": " + str(exc))
        write_json(output / "result.json", result)
        raise
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--download", action="store_true")
    args = parser.parse_args()
    print(run(repository_root(), args.download))


if __name__ == "__main__":
    main()
