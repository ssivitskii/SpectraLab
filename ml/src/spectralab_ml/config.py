from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class ProjectConfig:
    elements: tuple[str, ...]
    wavelength_start_nm: float
    wavelength_end_nm: float
    model_step_nm: float
    wavelength_medium: str
    ion_stage: int

    @classmethod
    def from_mapping(cls, value: dict[str, Any]) -> ProjectConfig:
        return cls(
            elements=tuple(value["elements"]),
            wavelength_start_nm=float(value["wavelength_range_nm"][0]),
            wavelength_end_nm=float(value["wavelength_range_nm"][1]),
            model_step_nm=float(value["model_step_nm"]),
            wavelength_medium=str(value["wavelength_medium"]),
            ion_stage=int(value["ion_stage"]),
        )


def repository_root() -> Path:
    return Path(__file__).resolve().parents[3]


def load_project_config(path: Path | None = None) -> ProjectConfig:
    config_path = path or repository_root() / "ml" / "configs" / "project.yaml"
    with config_path.open(encoding="utf-8") as stream:
        raw = yaml.safe_load(stream)
    return ProjectConfig.from_mapping(raw)
