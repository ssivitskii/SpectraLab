"""Read-only ML installation, configuration and reference integrity diagnostics."""

from __future__ import annotations

import hashlib
import importlib.metadata
import importlib.util
import json
import platform
from pathlib import Path

from spectralab_ml.config import (
    load_experiment_config,
    load_project_config,
    load_reference_catalog,
)
from spectralab_ml.data.reference import ReferenceCatalog


def check_ml_environment(root: Path, *, verify_sources: bool = False) -> dict:
    """Check reproducibility inputs; runtime readiness never certifies model quality.

    Source snapshots and local trained profiles are optional downloads. Their
    absence is reported explicitly and does not turn a fresh clone into a broken
    ML installation. Existing but modified source snapshots fail verification.
    No spectra, estimators, fitting or network access are involved.
    """
    root = Path(root).resolve()
    checks = []

    def add(name: str, status: str, **details) -> None:
        checks.append({"name": name, "status": status, **details})

    config_dir = root / "ml/configs"
    project_path = config_dir / "project.yaml"
    project = None
    try:
        project = load_project_config(project_path)
        selected = load_reference_catalog(project)
        add(
            "project_config",
            "passed",
            class_order=list(project.elements),
            medium=project.wavelength_medium,
            selected_reference_content_sha256=selected.metadata.get("content_sha256"),
            selected_reference_checksum_sha256=selected.metadata.get("checksum_sha256"),
        )
    except (OSError, ValueError, TypeError, KeyError) as exc:
        add("project_config", "failed", reason=str(exc))
    configs = sorted((config_dir / "experiments").glob("*.yaml"))
    if not configs:
        add("experiment_configs", "failed", reason="No experiment YAML configurations found")
    for path in configs:
        try:
            config = load_experiment_config(path)
            effective_project = (
                load_project_config(path.parent / config.project_config)
                if config.project_config
                else project
            )
            if effective_project is None:
                raise ValueError("Main project configuration is unavailable")
            config.validate_project(effective_project)
            classes = config.elements or effective_project.elements
            cache = path.parent / config.reference_cache if config.reference_cache else None
            selected = load_reference_catalog(
                effective_project, cache, config.reference_sha256, classes
            )
            optional_missing = (
                "cnn-1d" in config.models and importlib.util.find_spec("torch") is None
            )
            add(
                str(path.relative_to(root)),
                "optional_dependency_missing" if optional_missing else "passed",
                seed=config.seed,
                class_order=list(classes),
                selected_reference_content_sha256=selected.metadata.get("content_sha256"),
                selected_reference_checksum_sha256=selected.metadata.get("checksum_sha256"),
                optional_extra="cnn" if optional_missing else None,
            )
        except (OSError, ValueError, TypeError, KeyError) as exc:
            add(str(path.relative_to(root)), "failed", reason=str(exc))
    references = sorted((root / "data/reference").glob("*/catalog.json"))
    if not references:
        add("reference_caches", "failed", reason="No saved reference catalogs found")
    for path in references:
        name = str(path.relative_to(root))
        try:
            catalog = ReferenceCatalog.from_cache(path)
            add(
                name,
                "passed",
                line_count=len(catalog.lines),
                content_sha256=catalog.metadata["content_sha256"],
                file_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
            )
        except (OSError, ValueError, TypeError, KeyError) as exc:
            add(name, "failed", reason=str(exc))

    if verify_sources:
        observed = {}
        for name in (
            "docs/final_validation_search_v3_summary.json",
            "docs/final_validation_v5_summary.json",
            "docs/final_validation_v6_summary.json",
        ):
            search_summary = root / name
            if not search_summary.is_file():
                add(name, "not_downloaded")
                continue
            try:
                saved = json.loads(search_summary.read_text())
                for item in saved["evidence_snapshots"]:
                    key, expected = item["path"], item["sha256"]
                    if key in observed:
                        if observed[key] != expected:
                            add(key, "failed", reason="Source manifests disagree about checksum")
                        continue
                    observed[key] = expected
                    path = root / key
                    if not path.resolve().is_relative_to(root):
                        add(key, "failed", reason="Evidence path escapes project root")
                    elif not path.is_file():
                        add(key, "not_downloaded", expected_sha256=expected)
                    else:
                        actual = hashlib.sha256(path.read_bytes()).hexdigest()
                        add(
                            key,
                            "passed" if actual == expected else "failed",
                            actual_sha256=actual,
                            expected_sha256=expected,
                        )
            except (OSError, ValueError, TypeError, KeyError) as exc:
                add(name, "failed", reason=str(exc))

    dependencies = {}
    for package in ("spectralab-ml", "numpy", "scipy", "scikit-learn", "pandas", "torch"):
        try:
            dependencies[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            dependencies[package] = None
    counts = {
        status: sum(c["status"] == status for c in checks)
        for status in ("passed", "failed", "not_downloaded", "optional_dependency_missing")
    }
    return {
        "schema_version": 1,
        "status": "blocked" if counts["failed"] else "ready",
        "scope": "local_ml_runtime_and_input_integrity_not_model_validation",
        "runtime": {
            "python": platform.python_version(),
            "platform": platform.system(),
            "dependencies": dependencies,
            "optional_cnn_available": importlib.util.find_spec("torch") is not None,
        },
        "check_counts": counts,
        "checks": checks,
        "scientific_qualification": "independent_final_validation_not_completed",
        "final_metrics": None,
        "network_access_performed": False,
        "numerical_spectra_opened": False,
    }
