"""Versioned, strict configuration and local reference selection.

YAML numbers are accepted as numbers, never as strings or booleans. Paths in YAML
are relative to the configuration file. Validation never creates output files.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import asdict, dataclass, fields, replace
from pathlib import Path
from typing import Any

import yaml

CONFIG_VERSION = 1
MODEL_IDS = {
    "baseline-nnls",
    "logistic-ovr",
    "random-forest",
    "cnn-1d",
    "line-logistic",
    "line-logistic-robust",
}
FACTORS = {
    "snr_db",
    "instrumental_fwhm_nm",
    "sampling_step_nm",
    "calibration_shift_nm",
    "lorentzian_fwhm_nm",
    "photon_scale",
    "read_noise_std",
    "response_slope",
    "saturation_level",
}


def _mapping(value: Any, allowed: set[str], required: set[str] = frozenset()) -> dict[str, Any]:
    if not isinstance(value, dict) or not all(isinstance(k, str) for k in value):
        raise ValueError("Configuration must be a YAML mapping with string keys")
    if unknown := set(value) - allowed:
        raise ValueError(f"Unknown configuration fields: {', '.join(sorted(unknown))}")
    if missing := required - set(value):
        raise ValueError(f"Missing configuration fields: {', '.join(sorted(missing))}")
    return value


def _integer(value: Any, name: str, low: int, high: int) -> int:
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f"{name} must be an integer in [{low}, {high}]")
    return value


def _number(value: Any, name: str) -> float:
    if type(value) not in (int, float):
        raise ValueError(f"{name} must be a finite number (not a string or boolean)")
    try:
        result = float(value)
    except OverflowError as exc:
        raise ValueError(f"{name} is outside the finite numeric range") from exc
    if not math.isfinite(result):
        raise ValueError(f"{name} must be finite")
    return result


def _sequence(value: Any, name: str) -> list[Any] | tuple[Any, ...]:
    if not isinstance(value, (list, tuple)) or not value:
        raise ValueError(f"{name} must be a nonempty list")
    return value


def _strings(value: Any, name: str) -> tuple[str, ...]:
    result = tuple(_sequence(value, name))
    if any(type(v) is not str or not v for v in result) or len(set(result)) != len(result):
        raise ValueError(f"{name} must contain unique nonempty strings")
    return result


def _path(value: Any, name: str) -> str | None:
    if value is not None and (type(value) is not str or not value.strip()):
        raise ValueError(f"{name} must be a nonempty path string or null")
    return value


def _checksum(value: Any) -> str | None:
    if value is not None and (type(value) is not str or not re.fullmatch(r"[a-f0-9]{64}", value)):
        raise ValueError("reference_sha256 must be a lowercase SHA-256 digest")
    return value


def _version(raw: dict[str, Any]) -> None:
    if type(raw.get("schema_version")) is not int or raw["schema_version"] != CONFIG_VERSION:
        raise ValueError(f"schema_version is required and must be {CONFIG_VERSION}")


def stable_hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


@dataclass(frozen=True)
class ProjectConfig:
    elements: tuple[str, ...]
    wavelength_start_nm: float
    wavelength_end_nm: float
    model_step_nm: float
    wavelength_medium: str
    ion_stage: int
    schema_version: int = CONFIG_VERSION
    reference_cache: str | None = None
    reference_sha256: str | None = None

    @classmethod
    def from_mapping(cls, value: dict[str, Any]) -> ProjectConfig:
        required = {
            "schema_version",
            "elements",
            "wavelength_range_nm",
            "model_step_nm",
            "wavelength_medium",
            "ion_stage",
        }
        raw = _mapping(value, required | {"reference_cache", "reference_sha256"}, required)
        _version(raw)
        elements = _strings(raw["elements"], "elements")
        if len(elements) < 2 or any(not re.fullmatch(r"[A-Z][a-z]?", e) for e in elements):
            raise ValueError("elements must contain at least two distinct chemical symbols")
        span = _sequence(raw["wavelength_range_nm"], "wavelength_range_nm")
        if len(span) != 2:
            raise ValueError("wavelength_range_nm must contain exactly start and end")
        start, end = (_number(v, "wavelength_range_nm") for v in span)
        step = _number(raw["model_step_nm"], "model_step_nm")
        if not 0 < start < end or step <= 0:
            raise ValueError("Wavelength range and model step must be positive and ordered")
        intervals = (end - start) / step
        if (
            intervals < 1
            or intervals + 1 > 100_000
            or not math.isclose(intervals, round(intervals), rel_tol=0, abs_tol=1e-8)
        ):
            raise ValueError("model_step_nm must divide the range into 2..100000 grid points")
        if raw["wavelength_medium"] != "vacuum":
            raise ValueError("Project wavelength_medium must be vacuum; convert air explicitly")
        stage = _integer(raw["ion_stage"], "ion_stage", 1, 5)
        return cls(
            elements,
            start,
            end,
            step,
            raw["wavelength_medium"],
            stage,
            reference_cache=_path(raw.get("reference_cache"), "reference_cache"),
            reference_sha256=_checksum(raw.get("reference_sha256")),
        )


@dataclass(frozen=True)
class ExperimentConfig:
    schema_version: int = CONFIG_VERSION
    run_id: str = "experiment"
    seed: int = 42
    seeds: tuple[int, ...] = (7, 17, 29, 43, 71)
    protocol: str = "controlled_sweep"
    models: tuple[str, ...] = ("baseline-nnls", "logistic-ovr", "random-forest")
    base_samples: int = 36
    variations: int = 2
    factor: str = "snr_db"
    values: tuple[float | None, ...] = (20.0,)
    elements: tuple[str, ...] | None = None
    mixture_sizes: tuple[int, ...] = (1, 2, 3)
    project_config: str | None = None
    reference_cache: str | None = None
    reference_sha256: str | None = None
    timing_warmups: int = 2
    timing_repeats: int = 7
    calibrate: bool = False
    generation: dict[str, Any] | None = None

    @classmethod
    def from_mapping(cls, value: dict[str, Any]) -> ExperimentConfig:
        raw = _mapping(value, {f.name for f in fields(cls)}, {"schema_version"})
        _version(raw)
        name = raw.get("run_id", "experiment")
        if type(name) is not str or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,60}", name):
            raise ValueError("run_id must be a safe identifier of 1..61 characters")
        protocol = raw.get("protocol", "controlled_sweep")
        if type(protocol) is not str or protocol not in {"controlled_sweep", "unseen_combinations"}:
            raise ValueError("Unknown experiment protocol")
        factor = raw.get("factor", "snr_db")
        if type(factor) is not str or factor not in FACTORS:
            raise ValueError(f"Unknown controlled factor: {factor}")
        models = _strings(raw.get("models", cls.models), "models")
        if not set(models) <= MODEL_IDS:
            raise ValueError("Unknown model ID")
        seeds = tuple(
            _integer(v, "seeds", 0, 2**32 - 1)
            for v in _sequence(raw.get("seeds", cls.seeds), "seeds")
        )
        if len(set(seeds)) != len(seeds) or len(seeds) > 100:
            raise ValueError("seeds must contain 1..100 distinct integers")
        values = tuple(
            None if v is None or v == "clean" else _number(v, "values")
            for v in _sequence(raw.get("values", [20]), "values")
        )
        if len(set(values)) != len(values) or len(values) > 100:
            raise ValueError("Sweep values must be distinct, at most 100")
        if None in values and factor not in {"snr_db", "saturation_level"}:
            raise ValueError("null/clean is only compatible with snr_db or saturation_level")
        sizes = tuple(
            _integer(v, "mixture_sizes", 1, 3)
            for v in _sequence(
                raw.get(
                    "mixture_sizes", [2, 3] if protocol == "unseen_combinations" else [1, 2, 3]
                ),
                "mixture_sizes",
            )
        )
        if len(set(sizes)) != len(sizes):
            raise ValueError("mixture_sizes must be unique")
        if protocol == "unseen_combinations" and 1 in sizes:
            raise ValueError("unseen_combinations requires mixture_sizes 2 and/or 3")
        if type(raw.get("calibrate", False)) is not bool:
            raise ValueError("calibrate must be a boolean")
        return cls(
            run_id=name,
            seed=_integer(raw.get("seed", 42), "seed", 0, 2**32 - 1),
            seeds=seeds,
            protocol=protocol,
            models=models,
            base_samples=_integer(raw.get("base_samples", 36), "base_samples", 12, 10000),
            variations=_integer(raw.get("variations", 2), "variations", 1, 100),
            factor=factor,
            values=values,
            elements=_strings(raw["elements"], "elements")
            if raw.get("elements") is not None
            else None,
            mixture_sizes=sizes,
            project_config=_path(raw.get("project_config"), "project_config"),
            reference_cache=_path(raw.get("reference_cache"), "reference_cache"),
            reference_sha256=_checksum(raw.get("reference_sha256")),
            timing_warmups=_integer(raw.get("timing_warmups", 2), "timing_warmups", 1, 100),
            timing_repeats=_integer(raw.get("timing_repeats", 7), "timing_repeats", 2, 1000),
            calibrate=raw.get("calibrate", False),
            generation=raw.get("generation"),
        )

    def validate_project(self, project: ProjectConfig) -> None:
        from spectralab_ml.simulation import GenerationConfig

        classes = self.elements or project.elements
        if len(classes) < 2 or not set(classes) <= set(project.elements):
            raise ValueError("Experiment classes must be a subset of configured project classes")
        if self.base_samples < max(12, 2 * len(classes)):
            raise ValueError("base_samples must cover the configured classes")
        if self.calibrate and int(self.base_samples * 0.8) - int(self.base_samples * 0.6) < 2:
            raise ValueError("Calibration requires two distinct validation base samples")
        if any(size > len(classes) for size in self.mixture_sizes):
            raise ValueError("mixture_sizes cannot exceed the number of classes")
        controlled = {
            "elements",
            "component_weights",
            "wavelength_start_nm",
            "wavelength_end_nm",
            "sampling_step_nm",
            "seed",
            "max_points",
            "ion_stage",
        }
        allowed = {f.name for f in fields(GenerationConfig)} - controlled
        generation = _mapping({} if self.generation is None else self.generation, allowed)
        for name, value in generation.items():
            if name in {"profile", "noise_model"}:
                if type(value) is not str:
                    raise ValueError(f"generation.{name} must be a string")
            elif value is not None or name not in {"snr_db", "saturation_level"}:
                _number(value, f"generation.{name}")
        point_count = (
            round((project.wavelength_end_nm - project.wavelength_start_nm) / project.model_step_nm)
            + 1
        )
        if self.base_samples * self.variations * point_count > 50_000_000:
            raise ValueError("Configured dataset exceeds the 50 million generated point budget")
        if "cnn-1d" in self.models and point_count < 4:
            raise ValueError("cnn-1d requires at least four wavelength grid points")
        if self.factor in generation:
            raise ValueError("Controlled factor must not also appear in generation overrides")
        for value in self.values:
            params = dict(
                elements=(classes[0],),
                component_weights=(1.0,),
                wavelength_start_nm=project.wavelength_start_nm,
                wavelength_end_nm=project.wavelength_end_nm,
                sampling_step_nm=project.model_step_nm,
                **generation,
            )
            params[self.factor] = value
            GenerationConfig(**params).validate()
        if self.factor == "lorentzian_fwhm_nm" and generation.get("profile") != "voigt":
            raise ValueError("lorentzian_fwhm_nm sweep requires generation.profile: voigt")
        if (
            self.factor in {"photon_scale", "read_noise_std"}
            and generation.get("noise_model") != "poisson_read"
        ):
            raise ValueError("Detector noise sweep requires generation.noise_model: poisson_read")

    def to_dict(self) -> dict[str, Any]:
        return json.loads(json.dumps(asdict(self)))


class _StrictYamlLoader(yaml.SafeLoader):
    pass


def _unique_mapping(loader: yaml.SafeLoader, node: yaml.MappingNode, deep: bool = False):
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if not isinstance(key, str):
            raise ValueError("YAML configuration keys must be strings")
        if key in result:
            raise ValueError(f"Duplicate YAML configuration key: {key}")
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


_StrictYamlLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _unique_mapping)


def _read_yaml(path: Path) -> dict[str, Any]:
    try:
        return yaml.load(path.read_text(encoding="utf-8"), Loader=_StrictYamlLoader)
    except yaml.YAMLError as exc:
        raise ValueError(f"Invalid YAML configuration: {exc}") from exc


def repository_root() -> Path:
    return Path(__file__).resolve().parents[3]


def load_project_config(path: Path | None = None) -> ProjectConfig:
    config_path = path or repository_root() / "ml" / "configs" / "project.yaml"
    project = ProjectConfig.from_mapping(_read_yaml(config_path))
    if project.reference_cache and not Path(project.reference_cache).is_absolute():
        project = replace(
            project, reference_cache=str((config_path.parent / project.reference_cache).resolve())
        )
    return project


def load_experiment_config(path: Path) -> ExperimentConfig:
    return ExperimentConfig.from_mapping(_read_yaml(path))


def load_reference_catalog(
    project: ProjectConfig,
    reference_cache: Path | None = None,
    expected_sha256: str | None = None,
    class_order: tuple[str, ...] | None = None,
):
    """Load a trusted local cache with mandatory internal integrity validation.

    expected_sha256 optionally pins the *file bytes*, in addition to the cache's
    internal content hash. YAML project paths are resolved by load_project_config.
    """
    from spectralab_ml.data.reference import ReferenceCatalog, load_demo_catalog

    path = reference_cache or (
        repository_root() / project.reference_cache if project.reference_cache else None
    )
    expected = expected_sha256 or project.reference_sha256
    if expected and path is None:
        raise ValueError("reference_sha256 requires reference_cache")
    if path and expected and hashlib.sha256(path.read_bytes()).hexdigest() != expected:
        raise ValueError("Reference cache file checksum mismatch")
    catalog = ReferenceCatalog.from_cache(path) if path else load_demo_catalog()
    if (
        catalog.metadata.get("units") != "nm"
        or catalog.metadata.get("wavelength_medium") != project.wavelength_medium
    ):
        raise ValueError("Reference units or wavelength medium do not match project")
    classes = class_order or project.elements
    selected = catalog.for_elements(classes)
    if any(line.wavelength_medium != project.wavelength_medium for line in selected):
        raise ValueError("Reference line wavelength medium does not match project")
    present = {
        line.element
        for line in selected
        if line.ion_stage == project.ion_stage
        and project.wavelength_start_nm <= line.wavelength_nm <= project.wavelength_end_nm
        and (line.relative_intensity is None or line.relative_intensity > 0)
    }
    if missing := set(classes) - present:
        raise ValueError(
            f"Reference has no usable in-range lines for classes: {', '.join(sorted(missing))}"
        )
    return catalog
