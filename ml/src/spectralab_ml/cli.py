from __future__ import annotations

import argparse
import json
from pathlib import Path

from spectralab_ml.config import load_experiment_config, load_project_config, repository_root
from spectralab_ml.data.reference import ReferenceCatalog, load_demo_catalog
from spectralab_ml.datasets import build_demo_dataset
from spectralab_ml.preprocessing import SpectrumPreprocessor
from spectralab_ml.simulation import GenerationConfig, generate_spectrum
from spectralab_ml.training import train_and_save_models


def command_demo(args: argparse.Namespace) -> None:
    from spectralab_ml.experiments import run_experiment

    root = repository_root()
    project = load_project_config()
    catalog = load_demo_catalog(root)
    preprocessor = SpectrumPreprocessor(
        project.wavelength_start_nm, project.wavelength_end_nm, project.model_step_nm
    )
    dataset = build_demo_dataset(
        catalog,
        preprocessor,
        project.elements,
        seed=args.seed,
        base_samples=args.base_samples,
        variations=args.variations,
    )
    dataset.save(root / "data" / "processed" / dataset.dataset_hash[:16])
    metrics = train_and_save_models(
        dataset, catalog, preprocessor, root / "artifacts" / "models", seed=args.seed
    )
    experiment_path = run_experiment(root / "ml" / "configs" / "experiments" / "smoke.yaml")
    print(json.dumps({"models": metrics, "experiment": str(experiment_path)}, indent=2))


def command_experiment(args: argparse.Namespace) -> None:
    from spectralab_ml.experiments import run_experiment

    print(
        run_experiment(
            Path(args.config),
            Path(args.output) if args.output else None,
            reference_cache=Path(args.reference_cache) if args.reference_cache else None,
            reference_sha256=args.reference_sha256,
        )
    )


def command_series(args: argparse.Namespace) -> None:
    from spectralab_ml.experiments import run_series

    print(
        run_series(
            Path(args.config),
            Path(args.output) if args.output else None,
            seeds=args.seeds,
            reference_cache=Path(args.reference_cache) if args.reference_cache else None,
            reference_sha256=args.reference_sha256,
        )
    )


def command_train(args: argparse.Namespace) -> None:
    from spectralab_ml.experiments import _prepare

    path = Path(args.config)
    config = load_experiment_config(path)
    project, catalog, classes, _ = _prepare(
        path,
        config,
        Path(args.reference_cache) if args.reference_cache else None,
        args.reference_sha256,
    )
    processor = SpectrumPreprocessor(
        project.wavelength_start_nm, project.wavelength_end_nm, project.model_step_nm
    )
    dataset = build_demo_dataset(
        catalog,
        processor,
        classes,
        seed=config.seed,
        base_samples=config.base_samples,
        variations=config.variations,
        ion_stage=project.ion_stage,
    )
    output = Path(args.output or "artifacts/training")
    dataset.save(output / "dataset")
    metrics = train_and_save_models(
        dataset,
        catalog,
        processor,
        output / "models",
        seed=config.seed,
        model_ids=list(config.models),
        calibrate=config.calibrate,
        timing_warmups=config.timing_warmups,
        timing_repeats=config.timing_repeats,
    )
    print(json.dumps(metrics, indent=2))


def command_import(args: argparse.Namespace) -> None:
    metadata = json.loads(Path(args.metadata).read_text(encoding="utf-8"))
    _, report = ReferenceCatalog.from_delimited(
        Path(args.input),
        metadata=metadata,
        cache_path=Path(args.cache),
        report_path=Path(args.report),
    )
    print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))


def command_generate(args: argparse.Namespace) -> None:
    project = load_project_config()
    catalog = (
        ReferenceCatalog.from_cache(Path(args.reference_cache))
        if args.reference_cache
        else load_demo_catalog()
    )
    params = json.loads(Path(args.config).read_text(encoding="utf-8"))
    params.setdefault("wavelength_start_nm", project.wavelength_start_nm)
    params.setdefault("wavelength_end_nm", project.wavelength_end_nm)
    spectrum = generate_spectrum(GenerationConfig(**params), catalog, class_order=project.elements)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(spectrum.to_dict(), ensure_ascii=False, allow_nan=False), encoding="utf-8"
    )
    print(output)


def main() -> None:
    parser = argparse.ArgumentParser(prog="spectralab")
    subparsers = parser.add_subparsers(required=True)
    demo = subparsers.add_parser("demo", help="Train small trusted local demo artifacts")
    demo.add_argument("--seed", type=int, default=42)
    demo.add_argument("--base-samples", type=int, default=36)
    demo.add_argument("--variations", type=int, default=2)
    demo.set_defaults(handler=command_demo)
    experiment = subparsers.add_parser("experiment", help="Run a YAML experiment")
    experiment.set_defaults(handler=command_experiment)
    series = subparsers.add_parser("series", help="Run a matched multi-seed benchmark")
    series.add_argument("--seeds", type=int, nargs="+", help="Override fixed seed list")
    series.set_defaults(handler=command_series)
    trainer = subparsers.add_parser("train", help="Train using a versioned YAML configuration")
    trainer.set_defaults(handler=command_train)
    for command in (experiment, series, trainer):
        command.add_argument("--config", required=True)
        command.add_argument("--output")
        command.add_argument("--reference-cache")
        command.add_argument("--reference-sha256")
    importer = subparsers.add_parser("import-reference", help="Import a saved CSV/TSV export")
    importer.add_argument("--input", required=True)
    importer.add_argument("--metadata", required=True)
    importer.add_argument("--cache", default="data/cache/reference.json")
    importer.add_argument("--report", default="reports/reference_import.json")
    importer.set_defaults(handler=command_import)
    generator = subparsers.add_parser("generate", help="Generate a spectrum using shared ML logic")
    generator.add_argument("--config", required=True, help="JSON with GenerationConfig parameters")
    generator.add_argument("--output", default="artifacts/generated-spectrum.json")
    generator.add_argument(
        "--reference-cache", help="Optional imported catalog cache; default demo_fixture"
    )
    generator.set_defaults(handler=command_generate)
    args = parser.parse_args()
    args.handler(args)


if __name__ == "__main__":
    main()
