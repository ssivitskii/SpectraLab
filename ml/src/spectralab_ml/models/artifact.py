from __future__ import annotations

import hashlib
import json
import platform
import re
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import joblib
import numpy
import scipy
import sklearn

from spectralab_ml.preprocessing import SpectrumPreprocessor

SAFE_ID = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}$")


@dataclass(frozen=True)
class ModelManifest:
    model_id: str
    model_version: str
    model_type: str
    created_at: str
    class_order: tuple[str, ...]
    wavelength_start_nm: float
    wavelength_end_nm: float
    model_step_nm: float
    grid_point_count: int
    wavelength_medium: str
    units: str
    preprocessing: dict[str, Any]
    thresholds: dict[str, float]
    threshold_selection: dict[str, Any]
    score_kind: str
    calibrated: bool
    data_source: str
    generator_parameters: dict[str, Any]
    dataset_hash: str
    split_hash: str
    seed: int
    dependency_versions: dict[str, str]
    metrics: dict[str, Any]
    applicability: str
    reference_metadata: dict[str, Any]
    split_ids: dict[str, list[str]]
    model_sha256: str = ""

    @classmethod
    def create(
        cls,
        *,
        model_id: str,
        model_type: str,
        class_order: tuple[str, ...],
        preprocessor: SpectrumPreprocessor,
        thresholds: dict[str, float],
        threshold_selection: dict[str, Any],
        score_kind: str,
        calibrated: bool,
        dataset_hash: str,
        split_hash: str,
        seed: int,
        metrics: dict[str, Any],
        reference_metadata: dict[str, Any],
        generator_parameters: dict[str, Any],
        split_ids: dict[str, list[str]],
    ) -> ModelManifest:
        if not SAFE_ID.fullmatch(model_id):
            raise ValueError("Unsafe model_id")
        if tuple(thresholds) != class_order:
            raise ValueError("Threshold order must match class_order")
        return cls(
            model_id=model_id,
            model_version="1",
            model_type=model_type,
            created_at=datetime.now(UTC).isoformat(),
            class_order=class_order,
            wavelength_start_nm=preprocessor.wavelength_start_nm,
            wavelength_end_nm=preprocessor.wavelength_end_nm,
            model_step_nm=preprocessor.model_step_nm,
            grid_point_count=len(preprocessor.model_grid),
            wavelength_medium="vacuum",
            units="nm",
            preprocessing=preprocessor.to_dict(),
            thresholds=thresholds,
            threshold_selection=threshold_selection,
            score_kind=score_kind,
            calibrated=calibrated,
            data_source=str(reference_metadata["source"]),
            generator_parameters=generator_parameters,
            dataset_hash=dataset_hash,
            split_hash=split_hash,
            seed=seed,
            dependency_versions={
                "python": platform.python_version(),
                "numpy": numpy.__version__,
                "scipy": scipy.__version__,
                "scikit-learn": sklearn.__version__,
            },
            metrics=metrics,
            applicability=(
                "Синтетические спектры нейтральных атомов; на реальных измерениях не проверено"
            ),
            reference_metadata=reference_metadata,
            split_ids=split_ids,
        )


def _check_manifest(manifest: ModelManifest, preprocessor: SpectrumPreprocessor) -> None:
    if (
        manifest.model_version != "1"
        or manifest.wavelength_medium != "vacuum"
        or manifest.units != "nm"
    ):
        raise ValueError("Artifact version or wavelength convention is incompatible")
    if not manifest.class_order or len(set(manifest.class_order)) != len(manifest.class_order):
        raise ValueError("Invalid artifact class order")
    if tuple(manifest.thresholds) != manifest.class_order or not numpy.all(
        numpy.isfinite(list(manifest.thresholds.values()))
    ):
        raise ValueError("Invalid artifact thresholds")
    expected = SpectrumPreprocessor(
        manifest.wavelength_start_nm,
        manifest.wavelength_end_nm,
        manifest.model_step_nm,
        preprocessor.normalization,
    )
    if preprocessor.to_dict() != manifest.preprocessing or not numpy.array_equal(
        expected.model_grid, preprocessor.model_grid
    ):
        raise ValueError("Artifact grid/preprocessing is incompatible with its manifest")
    if len(preprocessor.model_grid) != manifest.grid_point_count:
        raise ValueError("Artifact grid length is incompatible")


def save_artifact(
    directory: Path, estimator: object, preprocessor: SpectrumPreprocessor, manifest: ModelManifest
) -> None:
    _check_manifest(manifest, preprocessor)
    directory.mkdir(parents=True, exist_ok=True)
    if directory.is_symlink():
        raise ValueError("Symlink model directories are not trusted")
    model_file = directory / "model.joblib"
    temporary = directory / "model.joblib.tmp"
    joblib.dump({"estimator": estimator, "preprocessor": preprocessor}, temporary)
    manifest = replace(manifest, model_sha256=hashlib.sha256(temporary.read_bytes()).hexdigest())
    temporary.replace(model_file)
    metadata = directory / "manifest.json.tmp"
    metadata.write_text(
        json.dumps(asdict(manifest), ensure_ascii=False, indent=2, allow_nan=False),
        encoding="utf-8",
    )
    metadata.replace(directory / "manifest.json")


def load_artifact(
    directory: Path,
    *,
    expected_class_order: tuple[str, ...] | None = None,
    expected_reference: dict[str, Any] | None = None,
    expected_model_id: str | None = None,
) -> tuple[object, SpectrumPreprocessor, ModelManifest]:
    if directory.is_symlink() or any(
        (directory / name).is_symlink() for name in ("manifest.json", "model.joblib")
    ):
        raise ValueError("Symlink artifacts are not trusted")
    manifest = ModelManifest(
        **json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    )
    manifest = replace(manifest, class_order=tuple(manifest.class_order))
    if expected_model_id is not None and manifest.model_id != expected_model_id:
        raise ValueError("Artifact identity does not match requested model")
    _check_manifest(manifest, SpectrumPreprocessor.from_dict(manifest.preprocessing))
    if expected_class_order and manifest.class_order != expected_class_order:
        raise ValueError("Artifact class order is incompatible")
    if expected_reference and any(
        manifest.reference_metadata.get(k) != expected_reference.get(k)
        for k in ("source", "checksum_sha256")
    ):
        raise ValueError("Artifact reference source/checksum is incompatible")
    if manifest.dependency_versions["scikit-learn"] != sklearn.__version__:
        raise ValueError("Artifact scikit-learn version is incompatible; retrain the model")
    raw = (directory / "model.joblib").read_bytes()
    if hashlib.sha256(raw).hexdigest() != manifest.model_sha256:
        raise ValueError("Artifact checksum mismatch")
    # This checksum detects corruption, not hostile code. Only the local administrator may
    # place files in the trusted artifact directory; no HTTP artifact uploads exist.
    payload = joblib.load(directory / "model.joblib")
    preprocessor = payload["preprocessor"]
    _check_manifest(manifest, preprocessor)
    probe = payload["estimator"].scores(numpy.zeros((1, manifest.grid_point_count)))
    if probe.shape != (1, len(manifest.class_order)) or not numpy.all(numpy.isfinite(probe)):
        raise ValueError("Artifact estimator output does not match class_order")
    return payload["estimator"], preprocessor, manifest
