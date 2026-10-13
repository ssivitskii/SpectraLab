from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np
from scipy.signal import find_peaks

from spectralab_ml.data.reference import ReferenceCatalog
from spectralab_ml.models import BaselineNNLS, load_artifact
from spectralab_ml.models.cnn import cnn_available
from spectralab_ml.preprocessing import SpectrumPreprocessor
from spectralab_ml.training import make_reference_templates

SAFE_MODEL_ID = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}$")


@dataclass
class PredictionResult:
    model_id: str
    model_version: str
    model_origin: str
    reference_source: str
    scores: list[dict[str, Any]]
    score_kind: str
    calibrated: bool
    thresholds: dict[str, float]
    detected_elements: list[str]
    warnings: list[str]
    processing_time_ms: float
    matched_reference_lines: list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ModelRegistry:
    def __init__(
        self,
        artifacts_dir: Path,
        catalog: ReferenceCatalog,
        class_order: tuple[str, ...],
        preprocessor: SpectrumPreprocessor | None = None,
        *,
        ion_stage: int = 1,
    ):
        self.artifacts_dir = artifacts_dir.resolve()
        self._cache: dict[str, tuple[tuple[int, ...], tuple[Any, Any, Any]]] = {}
        self.catalog = catalog
        self.class_order = class_order
        self.ion_stage = ion_stage
        self.preprocessor = preprocessor or SpectrumPreprocessor()
        self._builtin = BaselineNNLS(
            make_reference_templates(catalog, self.preprocessor, class_order, ion_stage=ion_stage)
        )

    def list_models(self) -> list[dict[str, Any]]:
        models = [
            {
                "model_id": "builtin-nnls",
                "version": "1",
                "model_type": "BaselineNNLS",
                "ready": True,
                "score_kind": self._builtin.score_kind,
                "calibrated": False,
                "data_source": self.catalog.source_name,
                "applicability": (
                    f"Synthetic ion stage {self.ion_stage}; fixed conservative threshold"
                ),
                "unavailable_reason": None,
                "diagnostic_code": None,
            }
        ]
        for model_id in ("baseline-nnls", "logistic-ovr", "random-forest", "cnn-1d"):
            try:
                _, _, manifest = self._load(model_id)
                models.append(
                    {
                        "model_id": model_id,
                        "version": manifest.model_version,
                        "model_type": manifest.model_type,
                        "ready": True,
                        "score_kind": manifest.score_kind,
                        "calibrated": manifest.calibrated,
                        "data_source": manifest.data_source,
                        "applicability": manifest.applicability,
                        "unavailable_reason": None,
                        "diagnostic_code": None,
                    }
                )
            except (
                ImportError,
                FileNotFoundError,
                ValueError,
                KeyError,
                TypeError,
                OSError,
                EOFError,
                AttributeError,
            ) as exc:
                if model_id == "cnn-1d" and not cnn_available():
                    code, reason = "optional_runtime_missing", "Установите optional extra cnn"
                elif isinstance(exc, FileNotFoundError):
                    code, reason = "artifact_missing", "Модель ещё не обучена"
                else:
                    code, reason = "artifact_incompatible", "Артефакт повреждён или несовместим"
                models.append(
                    {
                        "model_id": model_id,
                        "version": None,
                        "model_type": model_id,
                        "ready": False,
                        "score_kind": None,
                        "calibrated": False,
                        "data_source": self.catalog.source_name,
                        "applicability": "Run `make demo` to create this trusted local artifact",
                        "unavailable_reason": reason,
                        "diagnostic_code": code,
                    }
                )
        return models

    def _load(self, model_id: str) -> tuple[Any, Any, Any]:
        if model_id not in {"baseline-nnls", "logistic-ovr", "random-forest", "cnn-1d"}:
            raise FileNotFoundError("Неизвестная модель; выберите модель из /models")
        directory = self.artifacts_dir / model_id
        if directory.is_symlink() or directory.resolve().parent != self.artifacts_dir:
            raise ValueError("Artifact escapes trusted model directory")
        if model_id == "cnn-1d" and not cnn_available():
            raise ImportError("CNN requires optional runtime")
        manifest_path = directory / "manifest.json"
        if manifest_path.is_symlink():
            raise ValueError("Symlink artifacts are not trusted")
        raw_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        filename = (
            "model.npz"
            if raw_manifest.get("artifact_format") == "cnn-state-npz-v1"
            else "model.joblib"
        )
        paths = [manifest_path, directory / filename]
        if any(path.is_symlink() for path in paths):
            raise ValueError("Symlink artifacts are not trusted")
        fingerprint = tuple(
            value for path in paths for value in (path.stat().st_mtime_ns, path.stat().st_size)
        )
        cached = self._cache.get(model_id)
        if cached is None or cached[0] != fingerprint:
            value = load_artifact(
                directory,
                expected_class_order=self.class_order,
                expected_reference=self.catalog.metadata,
                expected_model_id=model_id,
                expected_ion_stages=(self.ion_stage,),
            )
            if value[1].to_dict() != self.preprocessor.to_dict():
                raise ValueError("Artifact preprocessing is incompatible with service grid")
            self._cache[model_id] = fingerprint, value
        return self._cache[model_id][1]

    def _resolve(self, model_id: str) -> tuple[Any, SpectrumPreprocessor, dict[str, Any]]:
        if not SAFE_MODEL_ID.fullmatch(model_id):
            raise ValueError("Invalid model_id")
        if model_id == "builtin-nnls":
            thresholds = dict.fromkeys(self.class_order, 0.2)
            return (
                self._builtin,
                self.preprocessor,
                {
                    "version": "1",
                    "origin": "built-in deterministic reference baseline",
                    "thresholds": thresholds,
                    "score_kind": self._builtin.score_kind,
                    "calibrated": False,
                },
            )
        directory = self.artifacts_dir / model_id
        if directory.parent != self.artifacts_dir or not directory.is_dir():
            raise FileNotFoundError(f"Model '{model_id}' is not ready; run make demo")
        estimator, preprocessor, manifest = self._load(model_id)
        return (
            estimator,
            preprocessor,
            {
                "version": manifest.model_version,
                "origin": f"trusted local artifact; data={manifest.data_source}",
                "thresholds": manifest.thresholds,
                "score_kind": manifest.score_kind,
                "calibrated": manifest.calibrated,
            },
        )

    def predict(
        self, model_id: str, wavelength_nm: np.ndarray, intensity: np.ndarray
    ) -> PredictionResult:
        started = time.perf_counter()
        estimator, preprocessor, metadata = self._resolve(model_id)
        vector = preprocessor.transform(wavelength_nm, intensity)
        if np.linalg.norm(vector) < 1e-12:
            values = np.zeros(len(self.class_order))
        else:
            values = estimator.scores(vector[None, :])[0]
        thresholds = metadata["thresholds"]
        detected = [
            element
            for element, value in zip(self.class_order, values, strict=True)
            if value >= thresholds[element]
        ]
        peaks, _ = find_peaks(intensity, prominence=max(float(np.ptp(intensity)) * 0.03, 1e-12))
        peak_wavelengths = wavelength_nm[peaks]
        matches = [
            {
                "element": line.element,
                "wavelength_nm": line.wavelength_nm,
                "relative_intensity": line.relative_intensity,
                "kind": line.wavelength_kind,
                "observed_peak_nm": float(
                    peak_wavelengths[np.argmin(abs(peak_wavelengths - line.wavelength_nm))]
                ),
                "delta_nm": float(np.min(abs(peak_wavelengths - line.wavelength_nm))),
            }
            for line in self.catalog.lines
            if line.element in detected
            and line.ion_stage == self.ion_stage
            and len(peak_wavelengths)
            and np.min(abs(peak_wavelengths - line.wavelength_nm)) <= 1.0
        ]
        warnings = [
            (
                "Калибровка проверена только на синтетической development-выборке."
                if metadata["calibrated"]
                else "Оценки не калиброваны и не являются вероятностями."
            ),
            (
                "Линии сопоставлены с пиками в пределах 1 nm. "
                "Это диагностика, а не доказанное объяснение решения модели."
            ),
            (
                "Область применимости: синтетические спектры справочника "
                f"{self.catalog.source_name}; "
                "реальные измерения требуют отдельной проверки."
            ),
        ]
        return PredictionResult(
            model_id=model_id,
            model_version=metadata["version"],
            model_origin=metadata["origin"],
            reference_source=self.catalog.source_name,
            scores=[
                {"element": element, "value": float(value)}
                for element, value in zip(self.class_order, values, strict=True)
            ],
            score_kind=metadata["score_kind"],
            calibrated=metadata["calibrated"],
            thresholds=thresholds,
            detected_elements=detected,
            warnings=warnings,
            processing_time_ms=(time.perf_counter() - started) * 1000,
            matched_reference_lines=matches,
        )
