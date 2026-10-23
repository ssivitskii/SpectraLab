"""CLI workflows for chemistry intake and measured-spectrum research."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

from spectralab_ml.config import repository_root

MAX_JSON_BYTES = 16_000_000


def read_json(path: Path) -> dict:
    path = Path(path)
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_JSON_BYTES:
        raise ValueError("Input must be a regular JSON file of at most 16 MB")

    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"Duplicate JSON field: {key}")
            result[key] = value
        return result

    def finite(token):
        raise ValueError(f"Nonfinite JSON number: {token}")

    def finite_float(token):
        number = float(token)
        if not math.isfinite(number):
            raise ValueError("JSON numeric exponent exceeds finite range")
        return number

    raw = path.read_bytes()
    if len(raw) > MAX_JSON_BYTES:
        raise ValueError("Input exceeds 16 MB")
    try:
        result = json.loads(
            raw, object_pairs_hook=unique, parse_constant=finite, parse_float=finite_float
        )
    except RecursionError as exc:
        raise ValueError("JSON nesting exceeds the supported limit") from exc
    if not isinstance(result, dict):
        raise ValueError("Top-level JSON object required")
    return result


def validate_output(
    output: str | None,
    *,
    inputs: tuple[Path, ...] = (),
    protected_directories: tuple[Path, ...] = (),
) -> None:
    if output:
        path = Path(output)
        if path.is_dir() or path.is_symlink():
            raise ValueError("Report output must be a regular file path")
        if path.resolve() in {p.resolve() for p in inputs} or (
            path.exists() and any(p.exists() and path.samefile(p) for p in inputs)
        ):
            raise ValueError("Output must not overwrite an input")
        if any(path.resolve().is_relative_to(p.resolve()) for p in protected_directories):
            raise ValueError("Report output cannot replace or live inside permanent receipts")


def write_report(result: dict, output: str | None, *, inputs: tuple[Path, ...] = ()) -> None:
    text = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    validate_output(output, inputs=inputs)
    if output:
        path = Path(output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    print(text, end="")


def command_ml_check(args: argparse.Namespace) -> None:
    from spectralab_ml.diagnostics import check_ml_environment

    result = check_ml_environment(Path(args.root), verify_sources=args.verify_sources)
    write_report(result, args.output)
    if result["status"] == "blocked":
        raise SystemExit(2)


def command_normalize_assays(args: argparse.Namespace) -> None:
    from spectralab_ml.data.assays import normalize_assay_manifest

    path = Path(args.input)
    result = normalize_assay_manifest(read_json(path))
    write_report(result, args.output, inputs=(path,))
    if result["status"] == "blocked":
        raise SystemExit(2)


def command_development_study(args: argparse.Namespace) -> None:
    from spectralab_ml.evaluation.development_v3 import run_development_study_v3

    path = run_development_study_v3(
        Path(args.root),
        source_plan_path=Path(args.source_plan) if args.source_plan else None,
        output_root=Path(args.output_root) if args.output_root else None,
        seed=args.seed,
    )
    print(path)


def _seen_registry(path: Path) -> dict:
    value = read_json(path)
    # Historic admission files embed the exact registry snapshot they used.
    return value.get("previously_seen_ids", value)


def command_final_template(args: argparse.Namespace) -> None:
    from spectralab_ml.evaluation._final_campaign_io import exclusive_json
    from spectralab_ml.evaluation.final_campaign_v3 import empty_final_manifest

    value = empty_final_manifest(args.campaign_id)
    path = Path(args.output)
    path.parent.mkdir(parents=True, exist_ok=True)
    exclusive_json(path, value)
    print(json.dumps(value, ensure_ascii=False, indent=2))


def command_final_preflight(args: argparse.Namespace) -> None:
    from spectralab_ml.evaluation.final_campaign_v3 import preflight_final_campaign

    manifest, registry = Path(args.manifest), Path(args.registry)
    plan = read_json(manifest)
    protected = (manifest, registry, *_campaign_paths(plan, Path(args.data_root)))
    validate_output(args.output, inputs=protected)
    result = preflight_final_campaign(
        plan,
        _seen_registry(registry),
        data_root=Path(args.data_root),
        verify_files=args.verify_files,
    )
    write_report(result, args.output, inputs=protected)
    if result["status"] == "blocked":
        raise SystemExit(2)


def command_final_freeze(args: argparse.Namespace) -> None:
    from spectralab_ml.evaluation.final_campaign_v3 import freeze_final_campaign

    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    result = freeze_final_campaign(
        Path(args.output),
        read_json(Path(args.manifest)),
        _seen_registry(Path(args.registry)),
        data_root=Path(args.data_root),
    )
    print(
        json.dumps(
            {
                "freeze_sha256": result["freeze_sha256"],
                "path": args.output,
                "numerical_inputs_read": False,
                "final_metrics": None,
            },
            indent=2,
        )
    )


def command_instrument_predict(args: argparse.Namespace) -> None:
    from spectralab_ml.inference.measured import predict_measured_profile

    paths = tuple(Path(p) for p in (args.profile, args.input, args.metadata))
    result = predict_measured_profile(*paths, input_format=args.input_format)
    write_report(result, args.output, inputs=paths)


def _campaign_paths(manifest: dict, root: Path) -> tuple[Path, ...]:
    """Protect declared inputs using metadata alone, including invalid plans."""
    from spectralab_ml.evaluation._final_campaign_io import local_path

    names = []

    def add(descriptor, key="path"):
        if isinstance(descriptor, dict) and isinstance(descriptor.get(key), str):
            names.append(descriptor[key])

    comparisons = manifest.get("comparisons", {})
    profiles = [manifest.get("profile", {})]
    if isinstance(comparisons, dict):
        profiles.extend(comparisons.values())
    for profile in profiles:
        if isinstance(profile, dict) and isinstance(profile.get("artifact_files"), list):
            for artifact in profile["artifact_files"]:
                add(artifact)
    if isinstance(manifest.get("implementation_files"), list):
        for item in manifest["implementation_files"]:
            add(item)
    if isinstance(manifest.get("samples"), list):
        for sample in manifest["samples"]:
            if isinstance(sample, dict):
                add(sample.get("local_spectrum"))
                add(sample.get("raw_spectrum"), "local_path")
    return tuple(local_path(root, name) for name in names)


def _validate_line_campaign(manifest: dict) -> None:
    for sample in manifest["samples"]:
        if sample["local_spectrum"]["rows"] > 100_000:
            raise ValueError("Measured line inference supports at most 100000 points per spectrum")


def command_final_evaluate(args: argparse.Namespace) -> None:
    import hashlib

    from spectralab_ml.data.measured_spectra import read_bounded
    from spectralab_ml.evaluation._final_campaign_io import local_path
    from spectralab_ml.evaluation.final_campaign_v3 import (
        evaluate_final_campaign,
        load_final_freeze,
    )
    from spectralab_ml.inference.measured import bind_final_line_profile

    root = Path(args.data_root)
    validate_output(
        args.output, inputs=(Path(args.freeze),), protected_directories=(Path(args.receipt_dir),)
    )
    freeze = load_final_freeze(Path(args.freeze), data_root=root)
    manifest = freeze["manifest"]
    _validate_line_campaign(manifest)
    profiles = {"primary": manifest["profile"], **manifest.get("comparisons", {})}
    files = manifest["implementation_files"]
    protected = (Path(args.freeze), *_campaign_paths(manifest, root))
    validate_output(args.output, inputs=protected, protected_directories=(Path(args.receipt_dir),))
    predictors, identities = {}, {}
    for name, contract in profiles.items():
        predictors[name], identities[name] = bind_final_line_profile(contract, root)
    required = {
        f"ml/src/spectralab_ml/{name}"
        for name in (
            "workflow_cli.py",
            "inference/measured.py",
            "data/measured_spectra.py",
            "models/line_adapter_v3.py",
            "models/line_adapter_v2.py",
            "evaluation/real_validation.py",
            "preprocessing/pipeline.py",
        )
    }
    if any(getattr(p, "requires_calcium_implementation", False) for p in predictors.values()):
        required.add("ml/src/spectralab_ml/models/calcium_adapter_v4.py")
    if any(getattr(p, "requires_calcium_ensemble", False) for p in predictors.values()):
        required.add("ml/src/spectralab_ml/models/calcium_ensemble_v4.py")
    if not required <= {item["path"] for item in files}:
        raise ValueError(
            "Final CLI must pin all actual line inference and adapter implementation files"
        )
    package_root = Path(__file__).resolve().parent
    for name in required:
        actual = package_root / name.removeprefix("ml/src/spectralab_ml/")
        if local_path(root, name).resolve() != actual.resolve():
            raise ValueError(
                "Pinned implementation must be the actual installed inference code, "
                "not a detached copy"
            )
    runtime = {
        **identities["primary"],
        "implementation_sha256": {
            item["path"]: hashlib.sha256(read_bounded(local_path(root, item["path"]))).hexdigest()
            for item in files
        },
    }
    if len(profiles) > 1:
        runtime["comparisons"] = {
            name: identity for name, identity in identities.items() if name != "primary"
        }
    result = evaluate_final_campaign(
        Path(args.freeze),
        Path(args.receipt_dir),
        data_root=root,
        runtime_identity=runtime,
        predictors=predictors,
        replay=args.replay,
    )
    write_report(result, args.output, inputs=protected)
    if result["status"] != "completed":
        raise SystemExit(2)


def register_workflows(subparsers: argparse._SubParsersAction) -> None:
    from spectralab_ml.series_cli import register_series_workflow

    register_series_workflow(subparsers)
    doctor = subparsers.add_parser(
        "ml-check", help="Check ML configs and reference integrity offline"
    )
    doctor.add_argument("--root", default=str(repository_root()))
    doctor.add_argument(
        "--verify-sources", action="store_true", help="Verify local evidence snapshots"
    )
    doctor.add_argument("--output")
    doctor.set_defaults(handler=command_ml_check)
    assays = subparsers.add_parser("normalize-assays", help="Normalize explicit Fe/Mg mass assays")
    assays.add_argument("--input", required=True)
    assays.add_argument("--output")
    assays.set_defaults(handler=command_normalize_assays)
    study = subparsers.add_parser(
        "study-development", help="Run grouped measured-development v3 study"
    )
    study.add_argument("--root", default=str(repository_root()))
    study.add_argument("--source-plan")
    study.add_argument("--output-root")
    study.add_argument("--seed", type=int, default=20261019)
    study.set_defaults(handler=command_development_study)
    template = subparsers.add_parser(
        "final-template", help="Create a deliberately blocked final planning template"
    )
    template.add_argument("--campaign-id", default="pending-independent-final-campaign")
    template.add_argument("--output", required=True)
    template.set_defaults(handler=command_final_template)
    preflight = subparsers.add_parser(
        "final-preflight", help="Check independent campaign evidence, geometry and support"
    )
    freeze = subparsers.add_parser(
        "final-freeze", help="Immutably freeze a ready final campaign before spectra are read"
    )
    for command in (preflight, freeze):
        command.add_argument("--manifest", required=True)
        command.add_argument("--registry", required=True)
        command.add_argument("--data-root", default=str(repository_root()))
        command.add_argument("--output", required=command is freeze)
    preflight.add_argument(
        "--verify-files",
        action="store_true",
        help="Explicit numerical admission check after metadata selection",
    )
    preflight.set_defaults(handler=command_final_preflight)
    freeze.set_defaults(handler=command_final_freeze)
    evaluation = subparsers.add_parser(
        "final-evaluate", help="Evaluate frozen measured models once with durable receipts"
    )
    evaluation.add_argument("--freeze", required=True)
    evaluation.add_argument(
        "--receipt-dir", required=True, help="One permanent directory for every campaign"
    )
    evaluation.add_argument("--data-root", default=str(repository_root()))
    evaluation.add_argument("--output")
    evaluation.add_argument("--replay", action="store_true")
    evaluation.set_defaults(handler=command_final_evaluate)
    prediction = subparsers.add_parser(
        "instrument-predict", help="Apply numeric measured profiles with explicit spectrum metadata"
    )
    prediction.add_argument("--profile", required=True)
    prediction.add_argument("--input", required=True)
    prediction.add_argument("--metadata", required=True)
    prediction.add_argument("--input-format", choices=("csv", "chemcam-ccs"), default="csv")
    prediction.add_argument("--output")
    prediction.set_defaults(handler=command_instrument_predict)
