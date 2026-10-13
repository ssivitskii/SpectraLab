from __future__ import annotations

import hashlib
import json
import math
import platform
import re
from dataclasses import asdict, dataclass, field, replace
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
    artifact_format: str = "joblib-v1"
    schema_version: int = 2
    ion_stages: tuple[int, ...] = (1,)
    calibration_metadata: dict[str, Any] = field(default_factory=dict)
    model_configuration: dict[str, Any] = field(default_factory=dict)

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
        calibration_metadata: dict[str, Any] | None = None,
        ion_stages: tuple[int, ...] = (1,),
    ) -> ModelManifest:
        if not SAFE_ID.fullmatch(model_id):
            raise ValueError("Unsafe model_id")
        if tuple(thresholds) != class_order:
            raise ValueError("Threshold order must match class_order")
        from spectralab_ml.simulation.generator import GENERATOR_UNITS

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
            generator_parameters={**generator_parameters, "units": GENERATOR_UNITS},
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
                "Синтетические спектры заданных ионных стадий; на реальных измерениях не проверено"
            ),
            reference_metadata=reference_metadata,
            split_ids=split_ids,
            ion_stages=ion_stages,
            calibration_metadata=calibration_metadata or {},
        )


def _validated_grid_count(start: Any, end: Any, step: Any) -> int:
    values = []
    for value in (start, end, step):
        if type(value) not in (int, float):
            raise ValueError("Artifact grid values must be finite numbers")
        try:
            numeric = float(value)
        except OverflowError as exc:
            raise ValueError("Artifact grid value is outside the finite range") from exc
        if not math.isfinite(numeric):
            raise ValueError("Artifact grid values must be finite numbers")
        values.append(numeric)
    start, end, step = values
    if not 0 < start < end or step <= 0:
        raise ValueError("Artifact wavelength range and step must be positive and ordered")
    intervals = (end - start) / step
    if (
        not math.isfinite(intervals)
        or not 1 <= intervals <= 99_999
        or not math.isclose(intervals, round(intervals), rel_tol=0, abs_tol=1e-8)
    ):
        raise ValueError("Artifact grid must have 2..100000 points and an exactly dividing step")
    return round(intervals) + 1


def _check_manifest(manifest: ModelManifest, preprocessor: SpectrumPreprocessor) -> None:
    if (
        manifest.model_version != "1"
        or manifest.schema_version != 2
        or manifest.wavelength_medium != "vacuum"
        or manifest.units != "nm"
    ):
        raise ValueError("Artifact version or wavelength convention is incompatible")
    if not manifest.ion_stages or any(
        type(stage) is not int or stage < 1 for stage in manifest.ion_stages
    ):
        raise ValueError("Invalid ion-stage contract")
    if manifest.artifact_format not in {"joblib-v1", "cnn-state-npz-v1"}:
        raise ValueError("Unsupported checkpoint format")
    if not manifest.class_order or len(set(manifest.class_order)) != len(manifest.class_order):
        raise ValueError("Invalid artifact class order")
    if tuple(manifest.thresholds) != manifest.class_order or not numpy.all(
        numpy.isfinite(list(manifest.thresholds.values()))
    ):
        raise ValueError("Invalid artifact thresholds")
    point_count = _validated_grid_count(
        manifest.wavelength_start_nm, manifest.wavelength_end_nm, manifest.model_step_nm
    )
    _validated_grid_count(
        preprocessor.wavelength_start_nm, preprocessor.wavelength_end_nm, preprocessor.model_step_nm
    )
    if type(manifest.grid_point_count) is not int or manifest.grid_point_count != point_count:
        raise ValueError("Artifact grid length is incompatible")
    if manifest.artifact_format == "cnn-state-npz-v1":
        from spectralab_ml.models.cnn import validate_model_configuration

        validate_model_configuration(manifest.model_configuration, expected_seed=manifest.seed)
        if point_count < 4:
            raise ValueError("CNN artifact requires at least four grid points")
    if type(preprocessor.normalization) is not str or preprocessor.normalization not in {
        "max_abs",
        "l2",
    }:
        raise ValueError("Artifact normalization is unsupported")
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
    from spectralab_ml.models.cnn import CNN1D

    is_cnn = isinstance(estimator, CNN1D)
    if is_cnn:
        manifest = replace(
            manifest,
            artifact_format="cnn-state-npz-v1",
            model_configuration=estimator.configuration(),
        )
    _check_manifest(manifest, preprocessor)
    directory.mkdir(parents=True, exist_ok=True)
    if directory.is_symlink():
        raise ValueError("Symlink model directories are not trusted")
    filename = "model.npz" if is_cnn else "model.joblib"
    model_file = directory / filename
    temporary = directory / (filename + ".tmp")
    if model_file.is_symlink() or temporary.is_symlink():
        raise ValueError("Symlink model files are not trusted")
    if is_cnn:
        estimator.save_checkpoint(temporary)
        import torch

        manifest = replace(
            manifest,
            artifact_format="cnn-state-npz-v1",
            dependency_versions={**manifest.dependency_versions, "torch": torch.__version__},
        )
    else:
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
    expected_ion_stages: tuple[int, ...] | None = None,
) -> tuple[object, SpectrumPreprocessor, ModelManifest]:
    if directory.is_symlink() or any(
        (directory / name).is_symlink() for name in ("manifest.json", "model.joblib", "model.npz")
    ):
        raise ValueError("Symlink artifacts are not trusted")
    raw_manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    if raw_manifest.get("schema_version") != 2:
        raise ValueError("Legacy model schema requires explicit retraining")
    manifest = ModelManifest(**raw_manifest)
    manifest = replace(
        manifest, class_order=tuple(manifest.class_order), ion_stages=tuple(manifest.ion_stages)
    )
    if expected_model_id is not None and manifest.model_id != expected_model_id:
        raise ValueError("Artifact identity does not match requested model")
    _check_manifest(manifest, SpectrumPreprocessor.from_dict(manifest.preprocessing))
    if expected_class_order and manifest.class_order != expected_class_order:
        raise ValueError("Artifact class order is incompatible")
    if expected_ion_stages and manifest.ion_stages != expected_ion_stages:
        raise ValueError("Artifact ion-stage contract is incompatible")
    if expected_reference and any(
        manifest.reference_metadata.get(k) != expected_reference.get(k)
        for k in ("source", "checksum_sha256", "content_sha256", "wavelength_medium")
    ):
        raise ValueError("Artifact reference source/checksum is incompatible")
    if manifest.dependency_versions["scikit-learn"] != sklearn.__version__:
        raise ValueError("Artifact scikit-learn version is incompatible; retrain the model")
    filename = "model.npz" if manifest.artifact_format == "cnn-state-npz-v1" else "model.joblib"
    model_path = directory / filename
    if model_path.stat().st_size > 500_000_000:
        raise ValueError("Artifact exceeds trusted loader size limit")
    raw = model_path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != manifest.model_sha256:
        raise ValueError("Artifact checksum mismatch")
    # This checksum detects corruption, not hostile code. Only the local administrator may
    # place files in the trusted artifact directory; no HTTP artifact uploads exist.
    if manifest.artifact_format == "cnn-state-npz-v1":
        from spectralab_ml.models.cnn import CNN1D

        estimator = CNN1D.load_checkpoint(
            model_path,
            seed=manifest.seed,
            input_points=manifest.grid_point_count,
            class_count=len(manifest.class_order),
            epochs=manifest.model_configuration["epochs"],
            learning_rate=manifest.model_configuration["learning_rate"],
            architecture=manifest.model_configuration["architecture"],
        )
        preprocessor = SpectrumPreprocessor.from_dict(manifest.preprocessing)
    else:
        payload = joblib.load(model_path)
        estimator, preprocessor = payload["estimator"], payload["preprocessor"]
    _check_manifest(manifest, preprocessor)
    if estimator.score_kind != manifest.score_kind or estimator.calibrated != manifest.calibrated:
        raise ValueError("Artifact score/calibration metadata is inconsistent")
    if type(estimator).__name__ != manifest.model_type:
        raise ValueError("Artifact model type is inconsistent")
    probe = estimator.scores(numpy.zeros((1, manifest.grid_point_count)))
    if probe.shape != (1, len(manifest.class_order)) or not numpy.all(numpy.isfinite(probe)):
        raise ValueError("Artifact estimator output does not match class_order")
    return estimator, preprocessor, manifest
