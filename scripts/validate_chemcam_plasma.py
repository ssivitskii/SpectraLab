from __future__ import annotations

import argparse
import hashlib
import json
import time
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import numpy as np
from spectralab_ml.config import repository_root
from spectralab_ml.data.plasma_reference import PlasmaReference
from spectralab_ml.data.reference import ReferenceCatalog
from spectralab_ml.evaluation import evaluate_multilabel
from spectralab_ml.evaluation.instrument_selection import select_operational_thresholds
from spectralab_ml.evaluation.plasma_validation import (
    make_plasma_dataset,
    operational_labels,
    operational_report,
)
from spectralab_ml.evaluation.real_validation import (
    make_locked_synthetic_dataset,
    normalize_observed,
    observation_mask,
    positive_recall_report,
)
from spectralab_ml.models import BaselineNNLS, LogisticOVR, RandomForestMultiLabel
from spectralab_ml.models.calibration import (
    TemperatureScaledEstimator,
    probability_view,
    reliability_metrics,
)
from spectralab_ml.models.estimators import select_thresholds
from spectralab_ml.models.line_features import (
    GradientBoostingOVR,
    LineWindowFeatures,
    ScaledLogisticOVR,
    select_line_positions,
)
from spectralab_ml.preprocessing import SpectrumPreprocessor
from spectralab_ml.preprocessing.robust import RobustBandTransform
from spectralab_ml.simulation.plasma import (
    InstrumentBand,
    PlasmaModel,
    RandomizationRanges,
    element_templates,
    generate_plasma_sample,
)
from spectralab_ml.training import make_reference_templates

PROTOCOL = "data/reference/chemcam_plasma_replay_protocol.json"
OXIDES = {"Na": "Na2O", "Mg": "MgO", "Ca": "CaO", "Fe": "FeOT"}
ELEMENTS = ("Na", "Mg", "Ca", "Fe")
STATE = {"positive": 1, "negative": 0, "unknown": -1}


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n")


def jsonable(value: object) -> object:
    return json.loads(json.dumps(value))


def check_protocol(root: Path, protocol: dict) -> None:
    reference = json.loads((root / protocol["plasma_reference"]["path"]).read_text())
    if reference["metadata"]["content_sha256"] != protocol["plasma_reference"]["content_sha256"]:
        raise ValueError("Plasma reference differs from the registered protocol")
    base = root / protocol["base_protocol"]["path"]
    if hashlib.sha256(base.read_bytes()).hexdigest() != protocol["base_protocol"]["sha256"]:
        raise ValueError("Base v1 protocol changed")
    expected = {
        "ranges": jsonable(asdict(RandomizationRanges())),
        "robust_transform": RobustBandTransform().to_dict(),
    }
    for key, value in expected.items():
        if jsonable(protocol[key]) != value:
            raise ValueError(f"Implementation default differs from protocol: {key}")
    real = protocol.get("real_thresholds")
    if real is not None:
        path = root / real["adaptation_protocol"]["path"]
        if hashlib.sha256(path.read_bytes()).hexdigest() != real["adaptation_protocol"]["sha256"]:
            raise ValueError("Adaptation protocol changed")


def load_adaptation(root: Path, protocol: dict, grid, mask, dry_run: bool) -> dict:
    import sys

    sys.path.insert(0, str(root / "scripts"))
    from adapt_chemcam import read_features

    real = protocol["real_thresholds"]
    adaptation = json.loads((root / real["adaptation_protocol"]["path"]).read_text())
    config = adaptation["preprocessing"]
    if observation_mask(grid, config["observed_intervals_nm"]).tolist() != mask.tolist():
        raise ValueError("Adaptation observation windows differ from the replay mask")
    roles = {real["tuning_role"]: real["tuning_role"]}
    for role in real["evaluation_roles"]:
        roles[role] = real["tuning_role"] if dry_run else role
    loaded = {}
    for name, role in roles.items():
        records = adaptation[role]
        X, _, details = read_features(root, records, grid, config)
        labels = np.asarray([[STATE[r["labels"][e]["state"]] for e in ELEMENTS] for r in records])
        loaded[name] = {
            "X": X,
            "labels": labels,
            "ids": [r["physical_sample_id"] for r in records],
            "measurements": [d["measurement_id"] for d in details],
        }
    return loaded


def load_real(root: Path, output: Path, dry_run: bool, model: PlasmaModel, grid, mask, bands):
    import sys

    sys.path.insert(0, str(root / "scripts"))
    from validate_chemcam import acquire, prepare

    v1 = json.loads((root / "data/reference/chemcam_external_protocol.json").read_text())
    source = json.loads((root / "data/reference/chemcam_external_source.json").read_text())
    classes = tuple(v1["classes"])
    if dry_run:
        rng = np.random.default_rng(99991)
        X, records, groups = [], [], []
        for sample in range(24):
            spectrum = generate_plasma_sample(model, grid, bands, rng, targets=classes)
            oxides = {
                OXIDES[e]: float(rng.choice([0.05, 3.0])) if spectrum.labels[e] else 0.05
                for e in OXIDES
            }
            for replicate in range(2):
                X.append(np.where(mask, spectrum.intensity, 0.0))
                groups.append(f"DRY{sample:02d}")
                records.append({"oxide_wt_percent": oxides, "replicate": replicate + 1})
        X = np.asarray(X)
        positive = np.asarray(
            [
                [r["oxide_wt_percent"].get(OXIDES.get(e, ""), 0) >= 1 for e in classes]
                for r in records
            ]
        )
        return X, positive, groups, records, classes
    acquire(root / "data/raw/chemcam_external", source, download=False)
    _, real_mask, X, positive, groups, records = prepare(
        root / "data/raw/chemcam_external", output, v1, source
    )
    if not np.array_equal(real_mask, mask):
        raise ValueError("Observation mask differs from the v1 protocol")
    return X, positive, groups, records, classes


def make_estimator(model_id: str, seed: int, templates: np.ndarray | None):
    if model_id == "baseline-nnls":
        if templates is None:
            raise ValueError("NNLS needs templates for this representation")
        return BaselineNNLS(templates)
    factories = {
        "logistic-ovr": LogisticOVR,
        "random-forest": RandomForestMultiLabel,
        "scaled-logistic": ScaledLogisticOVR,
        "gradient-boosting": GradientBoostingOVR,
    }
    return factories[model_id](seed=seed)


def fit_and_score(
    estimator,
    train,
    validation,
    test,
    real,
    *,
    default_threshold: float,
    calibrate: bool,
    validation_ids,
    seed: int,
):
    X_train, y_train = train
    X_val, y_val = validation
    if calibrate:
        estimator = TemperatureScaledEstimator(estimator)
        estimator.fit(X_train, y_train)
        ids = np.asarray(validation_ids)
        unique = np.asarray(sorted(set(ids)))
        shuffled = unique[np.random.default_rng(seed).permutation(len(unique))]
        calibration = np.isin(ids, shuffled[: len(shuffled) // 2])
        estimator.fit_calibration(X_val[calibration], y_val[calibration])
        threshold_rows = ~calibration
        thresholds, selection = select_thresholds(
            estimator.scores(X_val[threshold_rows]), y_val[threshold_rows], default=0.5
        )
        selection["calibration"] = estimator.calibration_metadata
    else:
        estimator.fit(X_train, y_train)
        thresholds, selection = select_thresholds(
            estimator.scores(X_val), y_val, default=default_threshold
        )
    return estimator, thresholds, selection, estimator.scores(test[0]), estimator.scores(real)


def operational_rows(labels: np.ndarray, classes: tuple[str, ...]) -> list[list[bool | None]]:
    rows = []
    for row in labels:
        values = dict(zip(ELEMENTS, row.tolist(), strict=True))
        rows.append([None if values.get(e, -1) == -1 else values[e] == 1 for e in classes])
    return rows


def real_threshold_entry(
    estimator, representation: str, thresholds, real_scores, adaptation, context
) -> dict:
    classes = context["classes"]
    index = [classes.index(e) for e in ELEMENTS]
    tuning = adaptation[context["tuning_role"]]
    tuning_scores = estimator.scores(tuning["repr"][representation])
    tuned, selection = select_operational_thresholds(
        tuning_scores[:, index],
        tuning["labels"],
        tuning["ids"],
        maximum_fpr=context["maximum_fpr"],
    )
    full = np.asarray(thresholds, dtype=float).copy()
    full[index] = tuned
    detections = real_scores >= full
    entry = {
        "thresholds": dict(zip(classes, full.tolist(), strict=True)),
        "selection": selection,
        "preflight_positive_only": positive_recall_report(
            detections, context["real_positive"], context["groups"], classes
        ),
        "preflight_operational": operational_report(
            real_scores, detections, context["oper"], context["groups"], classes
        ),
    }
    for role in context["evaluation_roles"]:
        data = adaptation[role]
        scores = estimator.scores(data["repr"][representation])
        oper = operational_rows(data["labels"], classes)
        entry[f"{role}_synthetic_thresholds"] = operational_report(
            scores, scores >= thresholds, oper, data["ids"], classes
        )
        entry[f"{role}_real_thresholds"] = operational_report(
            scores, scores >= full, oper, data["ids"], classes
        )
    return entry


def run(root: Path, dry_run: bool, n_jobs: int, protocol_name: str = PROTOCOL) -> Path:
    protocol = json.loads((root / protocol_name).read_text())
    check_protocol(root, protocol)
    v1 = json.loads((root / protocol["base_protocol"]["path"]).read_text())
    cfg = v1["preprocessing"]
    processor = SpectrumPreprocessor(cfg["grid_start_nm"], cfg["grid_end_nm"], cfg["step_nm"])
    grid = processor.model_grid
    mask = observation_mask(grid, cfg["observed_intervals_nm"])
    bands = tuple((a, b, tuple(f)) for a, b, f in protocol["synthetic"]["bands"])
    reference = PlasmaReference.load(root / protocol["plasma_reference"]["path"])
    model = PlasmaModel(reference, float(grid[0]), float(grid[-1]))
    prefix = "plasma-replay-dry" if dry_run else "plasma-replay"
    run_id = f"{prefix}-{datetime.now(UTC):%Y%m%dT%H%M%S}-{uuid4().hex[:8]}"
    output = root / "reports/external_plasma" / run_id
    output.mkdir(parents=True)
    write_json(output / "protocol.json", protocol)
    result = {
        "schema_version": 1,
        "run_id": run_id,
        "dry_run": dry_run,
        "status": "running",
        "created_at": datetime.now(UTC).isoformat(),
        "protocol_id": protocol["protocol_id"],
        "protocol_sha256": hashlib.sha256((root / protocol_name).read_bytes()).hexdigest(),
        "test_status": protocol["test_status"],
        "implementation_sha256": {
            str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in [
                *sorted((root / "ml/src/spectralab_ml").rglob("*.py")),
                Path(__file__).resolve(),
            ]
        },
        "model_selection_on_real_test": False,
        "runs": [],
    }
    write_json(output / "result.json", result)
    try:
        real_X, real_positive, groups, records, classes = load_real(
            root, output, dry_run, model, grid, mask, bands
        )
        oper = [
            [
                operational_labels(record["oxide_wt_percent"].get(OXIDES[e]))
                if e in OXIDES
                else None
                for e in classes
            ]
            for record in records
        ]
        robust = RobustBandTransform()
        mid_bands = tuple(InstrumentBand(a, b, float(np.mean(f))) for a, b, f in bands)
        plasma_templates = element_templates(model, grid, classes, bands=mid_bands)
        line_cfg = protocol["line_features"]
        context = tuple(line_cfg["context_elements"])
        context_templates = element_templates(model, grid, context, bands=mid_bands)
        selected = select_line_positions(
            plasma_templates[:, mask], grid[mask], classes, per_element=line_cfg["per_element"]
        ) + select_line_positions(
            context_templates[:, mask],
            grid[mask],
            context,
            per_element=line_cfg["per_context_element"],
        )
        lines = LineWindowFeatures(tuple(w for _, w in selected), grid, mask)
        write_json(
            output / "line_positions.json", [{"label": label, "nm": nm} for label, nm in selected]
        )
        neutral = ReferenceCatalog.from_cache(
            root / "data/reference/nist_neutral_350_800/catalog.json"
        )
        neutral_templates = make_reference_templates(neutral, processor, classes)
        real_repr = {
            "maxabs": normalize_observed(real_X, mask),
            "robust": robust.transform(real_X, mask),
        }
        real_repr["lines"] = lines.transform(real_repr["robust"])
        adaptation = {}
        context = None
        if "real_thresholds" in protocol:
            adaptation = load_adaptation(root, protocol, grid, mask, dry_run)
            for data in adaptation.values():
                data["repr"] = {
                    "maxabs": normalize_observed(data["X"], mask),
                    "robust": robust.transform(data["X"], mask),
                }
                data["repr"]["lines"] = lines.transform(data["repr"]["robust"])
            context = {
                "classes": classes,
                "tuning_role": protocol["real_thresholds"]["tuning_role"],
                "evaluation_roles": protocol["real_thresholds"]["evaluation_roles"],
                "maximum_fpr": protocol["real_thresholds"]["maximum_fpr"],
                "real_positive": real_positive,
                "groups": groups,
                "oper": oper,
            }
        seeds = protocol["model_seeds"][:1] if dry_run else protocol["model_seeds"]
        base_samples = 60 if dry_run else protocol["synthetic"]["base_samples"]
        for seed in seeds:
            started = time.perf_counter()
            seed_dir = output / f"seed-{seed}"
            seed_dir.mkdir()
            data = {
                "neutral": make_locked_synthetic_dataset(
                    neutral_templates,
                    classes,
                    neutral.metadata,
                    seed=seed,
                    base_samples=v1["synthetic_development"]["base_samples"],
                    variations=v1["synthetic_development"]["variations"],
                    mask=mask,
                ),
                "plasma": make_plasma_dataset(
                    model,
                    grid,
                    mask,
                    bands,
                    classes,
                    reference.metadata,
                    seed=seed,
                    base_samples=base_samples,
                    variations=protocol["synthetic"]["variations"],
                    n_jobs=n_jobs,
                ),
            }
            generated_s = time.perf_counter() - started
            representations = {}
            for name, dataset in data.items():
                splits = {p: getattr(dataset, p) for p in ("train", "validation", "test")}
                representations[(name, "maxabs")] = {
                    p: normalize_observed(s.X, mask) for p, s in splits.items()
                }
                representations[(name, "robust")] = {
                    p: robust.transform(s.X, mask) for p, s in splits.items()
                }
                if name == "plasma":
                    representations[(name, "lines")] = {
                        p: lines.transform(v) for p, v in representations[(name, "robust")].items()
                    }
            templates = {
                ("neutral", "maxabs"): normalize_observed(neutral_templates, mask),
                ("neutral", "robust"): np.clip(robust.transform(neutral_templates, mask), 0, None),
                ("plasma", "maxabs"): normalize_observed(plasma_templates, mask),
                ("plasma", "robust"): np.clip(robust.transform(plasma_templates, mask), 0, None),
            }
            seed_result = {
                "seed": seed,
                "generation_seconds": generated_s,
                "datasets": {
                    name: {"dataset_hash": d.dataset_hash, "split_hash": d.split_hash}
                    for name, d in data.items()
                },
                "variants": {},
            }
            write_json(
                seed_dir / "plasma_records.json",
                [{k: v for k, v in r.items() if k != "instrument"} for r in data["plasma"].records],
            )
            for variant in protocol["variants"]:
                key = (variant["data"], variant["representation"])
                X = representations[key]
                dataset = data[variant["data"]]
                y = {p: getattr(dataset, p).y for p in ("train", "validation", "test")}
                variant_result = {}
                for model_id in variant["models"]:
                    base_id, _, suffix = model_id.partition("+")
                    estimator, thresholds, selection, test_scores, real_scores = fit_and_score(
                        make_estimator(base_id, seed, templates.get(key)),
                        (X["train"], y["train"]),
                        (X["validation"], y["validation"]),
                        (X["test"], y["test"]),
                        real_repr[variant["representation"]],
                        default_threshold=0.15 if base_id == "baseline-nnls" else 0.5,
                        calibrate=suffix == "ts",
                        validation_ids=dataset.validation.base_sample_ids,
                        seed=seed,
                    )
                    detections = real_scores >= thresholds
                    entry = {
                        "thresholds": dict(zip(classes, thresholds.tolist(), strict=True)),
                        "threshold_selection": selection,
                        "synthetic_test": evaluate_multilabel(
                            y["test"], test_scores, thresholds, classes
                        ),
                        "external_positive_only": positive_recall_report(
                            detections, real_positive, groups, classes
                        ),
                        "external_operational": operational_report(
                            real_scores, detections, oper, groups, classes
                        ),
                    }
                    if base_id != "baseline-nnls":
                        probabilities = (
                            real_scores
                            if suffix == "ts"
                            else probability_view(estimator, real_scores)
                        )
                        synthetic_probabilities = (
                            test_scores
                            if suffix == "ts"
                            else probability_view(estimator, test_scores)
                        )
                        entry["synthetic_test_reliability"] = {
                            k: v
                            for k, v in reliability_metrics(
                                y["test"], synthetic_probabilities
                            ).items()
                            if k != "bins"
                        }
                        known = np.asarray([[value is not None for value in row] for row in oper])
                        truth = np.asarray([[value is True for value in row] for row in oper])
                        entry["external_operational_brier"] = (
                            float(np.mean((probabilities[known] - truth[known]) ** 2))
                            if known.any()
                            else None
                        )
                    if context is not None:
                        entry["real_thresholds"] = real_threshold_entry(
                            estimator,
                            variant["representation"],
                            thresholds,
                            real_scores,
                            adaptation,
                            context,
                        )
                    np.savez_compressed(
                        seed_dir / f"{variant['id'].replace('/', '_')}--{model_id}.npz",
                        real_scores=real_scores,
                        thresholds=thresholds,
                        detections=detections,
                    )
                    variant_result[model_id] = entry
                seed_result["variants"][variant["id"]] = variant_result
                print(f"seed {seed} {variant['id']} done", flush=True)
            seed_result["seconds"] = time.perf_counter() - started
            write_json(seed_dir / "result.json", seed_result)
            result["runs"].append(seed_result)
            write_json(output / "result.json", result)
        result["aggregate"] = aggregate(result["runs"], protocol, classes)
        if context is not None:
            result["aggregate_real_thresholds"] = aggregate_real(result["runs"], protocol)
        result.update(status="completed", completed_at=datetime.now(UTC).isoformat())
        write_json(output / "result.json", result)
        print(markdown_summary(result["aggregate"], classes))
        if context is not None:
            print(markdown_real(result["aggregate_real_thresholds"]))
    except BaseException as exc:
        result.update(status="failed", error=type(exc).__name__ + ": " + str(exc))
        write_json(output / "result.json", result)
        raise
    return output


def _mean_sd(values: list[float | None]) -> dict:
    present = [v for v in values if v is not None]
    return {
        "mean": float(np.mean(present)) if present else None,
        "sd": float(np.std(present, ddof=1)) if len(present) > 1 else None,
        "seeds": len(present),
    }


def aggregate(runs: list[dict], protocol: dict, classes: tuple[str, ...]) -> dict:
    summary = {}
    for variant in protocol["variants"]:
        for model_id in variant["models"]:
            entries = [run["variants"][variant["id"]][model_id] for run in runs]
            item = {
                "synthetic_micro_f1": _mean_sd([e["synthetic_test"]["micro_f1"] for e in entries]),
                "external_recall_all": _mean_sd(
                    [e["external_positive_only"]["primary"]["recall"] for e in entries]
                ),
                "per_element": {},
            }
            for element in ("Na", "Mg", "Ca", "Fe"):
                oper = [e["external_operational"]["per_element"][element] for e in entries]
                item["per_element"][element] = {
                    "recall_positive_only": _mean_sd(
                        [
                            e["external_positive_only"]["per_element"][element]["recall"]
                            for e in entries
                        ]
                    ),
                    "operational_recall": _mean_sd([o["recall"]["value"] for o in oper]),
                    "operational_fpr": _mean_sd([o["false_positive_rate"]["value"] for o in oper]),
                    "operational_roc_auc": _mean_sd([o["roc_auc"] for o in oper]),
                    "operational_support": {
                        "positive": oper[0]["recall"]["support"],
                        "negative": oper[0]["false_positive_rate"]["support"],
                    },
                }
            if "synthetic_test_reliability" in entries[0]:
                item["synthetic_brier"] = _mean_sd(
                    [e["synthetic_test_reliability"]["brier"] for e in entries]
                )
                item["synthetic_ece"] = _mean_sd(
                    [e["synthetic_test_reliability"]["ece"] for e in entries]
                )
                item["external_operational_brier"] = _mean_sd(
                    [e["external_operational_brier"] for e in entries]
                )
            summary[f"{variant['id']}/{model_id}"] = item
    return summary


def aggregate_real(runs: list[dict], protocol: dict) -> dict:
    roles = protocol["real_thresholds"]["evaluation_roles"]
    summary = {}
    for variant in protocol["variants"]:
        for model_id in variant["models"]:
            entries = [run["variants"][variant["id"]][model_id]["real_thresholds"] for run in runs]
            item = {
                "preflight_recall_all": _mean_sd(
                    [e["preflight_positive_only"]["primary"]["recall"] for e in entries]
                ),
                "per_element": {},
            }
            for element in ELEMENTS:
                values = {}
                sources = {"preflight": "preflight_operational"}
                for role in roles:
                    sources[f"{role}_synthetic"] = f"{role}_synthetic_thresholds"
                    sources[f"{role}_real"] = f"{role}_real_thresholds"
                for name, key in sources.items():
                    reports = [e[key]["per_element"][element] for e in entries]
                    values[name] = {
                        "recall": _mean_sd([r["recall"]["value"] for r in reports]),
                        "fpr": _mean_sd([r["false_positive_rate"]["value"] for r in reports]),
                        "roc_auc": _mean_sd([r["roc_auc"] for r in reports]),
                        "support": {
                            "positive": reports[0]["recall"]["support"],
                            "negative": reports[0]["false_positive_rate"]["support"],
                        },
                    }
                item["per_element"][element] = values
            summary[f"{variant['id']}/{model_id}"] = item
    return summary


def markdown_real(summary: dict) -> str:
    lines = [
        "| Вариант / модель | Preflight recall все >0 | "
        + " | ".join(f"{e} test синт. → реал. порог (recall / FPR)" for e in ELEMENTS)
        + " |",
        "| --- | ---: | " + " | ".join("---:" for _ in ELEMENTS) + " |",
    ]
    for name, item in summary.items():
        cells = []
        for element in ELEMENTS:
            values = item["per_element"][element]
            synthetic = next(v for k, v in values.items() if k.endswith("_synthetic"))
            real = next(v for k, v in values.items() if k.endswith("_real"))
            cells.append(
                f"{_fmt(synthetic['recall'], 2)} / {_fmt(synthetic['fpr'], 2)} → "
                f"{_fmt(real['recall'], 2)} / {_fmt(real['fpr'], 2)}"
            )
        lines.append(
            f"| {name} | {_fmt(item['preflight_recall_all'])} | " + " | ".join(cells) + " |"
        )
    return "\n".join(lines)


def _fmt(value: dict, digits: int = 3) -> str:
    if value["mean"] is None:
        return "—"
    sd = "" if value["sd"] is None else f" ± {value['sd']:.{digits}f}"
    return f"{value['mean']:.{digits}f}{sd}"


def markdown_summary(summary: dict, classes: tuple[str, ...]) -> str:
    lines = [
        "| Вариант / модель | Synthetic micro-F1 | Recall (все >0) | "
        + " | ".join(f"{e} recall / FPR / AUC" for e in ("Na", "Mg", "Ca", "Fe"))
        + " |",
        "| --- | ---: | ---: | " + " | ".join("---:" for _ in range(4)) + " |",
    ]
    for name, item in summary.items():
        cells = []
        for element in ("Na", "Mg", "Ca", "Fe"):
            e = item["per_element"][element]
            cells.append(
                f"{_fmt(e['operational_recall'], 2)} / {_fmt(e['operational_fpr'], 2)} / "
                f"{_fmt(e['operational_roc_auc'], 2)}"
            )
        lines.append(
            f"| {name} | {_fmt(item['synthetic_micro_f1'])} | "
            f"{_fmt(item['external_recall_all'])} | " + " | ".join(cells) + " |"
        )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--n-jobs", type=int, default=-1)
    parser.add_argument("--protocol", default=PROTOCOL)
    args = parser.parse_args()
    print(run(repository_root(), args.dry_run, args.n_jobs, args.protocol))


if __name__ == "__main__":
    main()
