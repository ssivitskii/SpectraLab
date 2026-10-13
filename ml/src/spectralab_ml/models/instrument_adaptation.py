"""Research-only supervised instrument adaptation; no fitting or tuning on test data.

Input spectra have already been interpolated onto the declared 0.1 nm grid without
bridging unobserved bands. Features use only declared bands: clip negatives, take
1 nm maxima, normalize each band to unit sum, then log1p(1000*x). The scaler is fit
on training rows with any known label. Physical samples have equal total weight,
both in the scaler and separately in each known-label logistic regression.
"""

from __future__ import annotations

import copy
import hashlib
import io
import json
import os
import re
import warnings
import zipfile
from collections import Counter
from pathlib import Path
from typing import Any

import numpy as np
from scipy.special import expit
from sklearn import __version__ as sklearn_version
from sklearn.exceptions import ConvergenceWarning
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler

SCHEMA_VERSION = 1
MAX_ARTIFACT_BYTES = 10_000_000
NEVER_POSITIVE_THRESHOLD = float(np.nextafter(1.0, np.inf))
THRESHOLD_RULE = "score >= threshold; threshold > 1 disables the class"
FEATURE_SPEC = {
    "name": "observed_band_log_max_bins",
    "version": 1,
    "grid_step_nm": 0.1,
    "bin_width_nm": 1.0,
    "negative_intensity": "clip_to_zero",
    "band_normalization": "l1_of_bin_maxima",
    "log_transform": "log1p(1000*x)",
    "zero_band_policy": "reject",
    "scaler": "training_known_rows_group_weighted_standard_scaler",
}
ARRAY_NAMES = {"grid_nm", "observed_mask", "mean", "scale", "coefficients", "intercepts"}
MANIFEST_NAMES = {
    "schema_version",
    "artifact_type",
    "artifact_format",
    "score_kind",
    "calibrated",
    "instrument_id",
    "class_order",
    "wavelength_medium",
    "units",
    "feature_spec",
    "observed_intervals",
    "feature_count",
    "grid_sha256",
    "mask_sha256",
    "C",
    "seed",
    "training",
    "provenance",
    "dependency_versions",
    "thresholds",
    "threshold_rule",
    "coefficients_sha256",
    "identity",
}


def _json_bytes(value: Any) -> bytes:
    try:
        return json.dumps(value, sort_keys=True, allow_nan=False, ensure_ascii=False).encode()
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("Artifact metadata must contain finite JSON values") from exc


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _array_sha(array: np.ndarray) -> str:
    return _sha(np.ascontiguousarray(array).tobytes())


def _validate_contract(
    grid_nm: np.ndarray, intervals: Any, class_order: Any, instrument_id: str
) -> tuple[np.ndarray, list[list[float]], tuple[str, ...], list[list[np.ndarray]]]:
    grid = np.asarray(grid_nm, dtype="<f8")
    if (
        grid.ndim != 1
        or not 2 <= grid.size <= 100_000
        or not np.isfinite(grid).all()
        or grid[0] <= 0
        or not np.allclose(np.diff(grid), 0.1, rtol=0, atol=1e-8)
    ):
        raise ValueError("Adapter requires a finite, increasing 0.1 nm grid")
    if (
        not isinstance(class_order, (tuple, list))
        or not 1 <= len(class_order) <= 32
        or any(not isinstance(c, str) or not re.fullmatch(r"[A-Z][a-z]?", c) for c in class_order)
        or len(set(class_order)) != len(class_order)
    ):
        raise ValueError("Class order must contain unique element symbols")
    if not isinstance(instrument_id, str) or not 1 <= len(instrument_id.strip()) <= 200:
        raise ValueError("An explicit instrument_id is required")
    if not isinstance(intervals, (tuple, list)) or not intervals:
        raise ValueError("Observed intervals must be nonempty")
    normalized, bins = [], []
    previous = -np.inf
    for interval in intervals:
        if (
            not isinstance(interval, (tuple, list))
            or len(interval) != 2
            or any(type(v) not in (int, float) for v in interval)
        ):
            raise ValueError("Each observed interval needs two numeric endpoints")
        start, end = (float(v) for v in interval)
        if (
            not np.isfinite([start, end]).all()
            or start <= previous
            or end <= start
            or start < grid[0] - 1e-8
            or end > grid[-1] + 1e-8
            or not np.isclose(end - start, round(end - start), rtol=0, atol=1e-8)
            or not all(np.any(np.isclose(grid, edge, atol=1e-8, rtol=0)) for edge in (start, end))
        ):
            raise ValueError("Bands must be disjoint, grid-aligned and divide into 1 nm bins")
        band_bins = []
        for i in range(round(end - start)):
            left, right = start + i, start + i + 1
            # An interior boundary belongs only to its right bin; the final endpoint is retained.
            right_mask = (
                grid <= right + 1e-8 if i == round(end - start) - 1 else grid < right - 1e-8
            )
            selected = np.flatnonzero((grid >= left - 1e-8) & right_mask)
            if not selected.size:
                raise ValueError("Empty spectral bin")
            band_bins.append(selected)
        normalized.append([start, end])
        bins.append(band_bins)
        previous = end
    return grid.copy(), normalized, tuple(class_order), bins


def _provenance(value: dict) -> dict:
    value = json.loads(_json_bytes(value))
    if (
        not isinstance(value, dict)
        or value.get("estimand") != "operational_above_threshold"
        or not isinstance(value.get("oxide_policy"), dict)
        or not value["oxide_policy"]
    ):
        raise ValueError("Provenance requires operational_above_threshold and an oxide_policy")
    return value


def _thresholds(value: Any, classes: tuple[str, ...]) -> dict | None:
    if value is None:
        return None
    if (
        not isinstance(value, dict)
        or set(value) != set(classes)
        or any(
            type(v) not in (int, float)
            or not np.isfinite(v)
            or not 0 <= v <= NEVER_POSITIVE_THRESHOLD
            for v in value.values()
        )
    ):
        raise ValueError("Thresholds require finite [0,nextafter(1,+inf)] values per class")
    return {c: float(value[c]) for c in classes}


def _weights(groups: list[str]) -> np.ndarray:
    counts = Counter(groups)
    return np.asarray([1 / counts[group] for group in groups], dtype=float)


def _check_training_metadata(manifest: dict, classes: tuple[str, ...]) -> None:
    if (
        type(manifest["C"]) not in (int, float)
        or not np.isfinite(manifest["C"])
        or manifest["C"] <= 0
        or type(manifest["seed"]) is not int
        or not 0 <= manifest["seed"] < 2**32
    ):
        raise ValueError("Invalid training hyperparameters")
    training = manifest["training"]
    required = {
        "rows",
        "physical_sample_ids",
        "physical_sample_count",
        "physical_sample_ids_sha256",
        "training_data_sha256",
        "known_support",
        "sample_weight",
    }
    if not isinstance(training, dict) or set(training) != required:
        raise ValueError("Invalid training metadata")
    groups = training["physical_sample_ids"]
    if (
        not isinstance(groups, list)
        or not groups
        or any(not isinstance(g, str) or not 1 <= len(g) <= 1024 for g in groups)
        or groups != sorted(set(groups))
        or type(training["rows"]) is not int
        or training["rows"] < len(groups)
        or type(training["physical_sample_count"]) is not int
        or training["physical_sample_count"] != len(groups)
        or training["physical_sample_ids_sha256"] != _sha(_json_bytes(groups))
        or not isinstance(training["training_data_sha256"], str)
        or not re.fullmatch(r"[a-f0-9]{64}", training["training_data_sha256"])
        or training["sample_weight"] != "one_total_weight_per_physical_sample_per_known_class"
        or not isinstance(training["known_support"], dict)
        or set(training["known_support"]) != set(classes)
    ):
        raise ValueError("Invalid physical-sample training contract")
    for support in training["known_support"].values():
        if (
            not isinstance(support, dict)
            or set(support) != {"positive_groups", "negative_groups", "known_rows", "unknown_rows"}
            or any(type(v) is not int or v < 0 for v in support.values())
            or min(support["positive_groups"], support["negative_groups"]) < 1
            or support["positive_groups"] + support["negative_groups"] > len(groups)
            or support["positive_groups"] + support["negative_groups"] > support["known_rows"]
            or support["known_rows"] + support["unknown_rows"] != training["rows"]
        ):
            raise ValueError("Invalid known-label support")
    versions = manifest["dependency_versions"]
    if (
        not isinstance(versions, dict)
        or set(versions) != {"numpy", "scikit_learn"}
        or any(not isinstance(v, str) or not v for v in versions.values())
    ):
        raise ValueError("Invalid dependency version metadata")


class InstrumentAdapter:
    score_kind = "instrument_adapted_logistic_score"
    calibrated = False

    @property
    def manifest(self) -> dict:
        self._require_fitted()
        return copy.deepcopy(self._manifest)

    @property
    def identity(self) -> str:
        return self.manifest["identity"]

    def _require_fitted(self) -> None:
        if not hasattr(self, "_manifest"):
            raise ValueError("Instrument adapter has not been fitted or loaded")

    def _features(self, X: np.ndarray) -> np.ndarray:
        values = np.asarray(X, dtype=float)
        if values.ndim != 2 or values.shape[1] != len(self.grid_nm) or not values.shape[0]:
            raise ValueError("Spectra must be a nonempty rows-by-declared-grid matrix")
        if not np.isfinite(values).all():
            raise ValueError("Spectra must be finite")
        bands = []
        for band in self._bins:
            maxima = np.maximum(
                np.column_stack([values[:, indices].max(axis=1) for indices in band]), 0
            )
            largest = maxima.max(axis=1, keepdims=True)
            if np.any(largest <= 0):
                raise ValueError("Every observed band must contain positive signal in every row")
            # Dividing by the maximum first avoids overflow for large finite instrument counts.
            normalized = maxima / largest
            normalized /= normalized.sum(axis=1, keepdims=True)
            bands.append(np.log1p(1000 * normalized))
        return np.column_stack(bands)

    def fit(
        self,
        X: np.ndarray,
        y: np.ndarray,
        label_mask: np.ndarray,
        *,
        sample_ids: list[str],
        grid_nm: np.ndarray,
        observed_intervals: list[list[float]],
        class_order: tuple[str, ...],
        instrument_id: str,
        C: float = 1.0,
        seed: int = 0,
        provenance: dict | None = None,
    ) -> InstrumentAdapter:
        if hasattr(self, "_manifest"):
            raise ValueError("Adapter is already fitted; create a new instance for another fit")
        if type(C) not in (int, float) or not np.isfinite(C) or C <= 0:
            raise ValueError("C must be finite and positive")
        if type(seed) is not int or not 0 <= seed < 2**32:
            raise ValueError("Seed must be an integer in [0,2**32)")
        grid, intervals, classes, bins = _validate_contract(
            grid_nm, observed_intervals, class_order, instrument_id
        )
        provenance = _provenance(provenance)
        labels, known = np.asarray(y), np.asarray(label_mask)
        if (
            labels.dtype != bool
            or known.dtype != bool
            or labels.ndim != 2
            or labels.shape != known.shape
            or labels.shape != (len(sample_ids), len(classes))
            or any(not isinstance(g, str) or not 1 <= len(g) <= 1024 for g in sample_ids)
        ):
            raise ValueError("Boolean labels/mask and nonempty physical sample IDs must align")
        self.grid_nm, self.observed_intervals, self.class_order, self._bins = (
            grid,
            intervals,
            classes,
            bins,
        )
        features = self._features(X)
        if len(features) != len(labels):
            raise ValueError("Spectra and labels must have the same row count")
        groups = np.asarray(sample_ids)
        for group in set(sample_ids):
            for k in range(len(classes)):
                truth = labels[(groups == group) & known[:, k], k]
                if truth.size and not np.all(truth == truth[0]):
                    raise ValueError("Known labels conflict within a physical sample")
        active = known.any(axis=1)
        if not active.any():
            raise ValueError("No known training labels")
        scaler = StandardScaler().fit(
            features[active], sample_weight=_weights(groups[active].tolist())
        )
        transformed = scaler.transform(features)
        coefficients, intercepts, support = [], [], {}
        for k, element in enumerate(classes):
            selected = known[:, k]
            if set(labels[selected, k].tolist()) != {False, True}:
                raise ValueError(f"Both known label states are required for {element}")
            estimator = LogisticRegression(
                C=float(C), solver="lbfgs", max_iter=3000, tol=1e-9, random_state=seed
            )
            with warnings.catch_warnings():
                warnings.simplefilter("error", ConvergenceWarning)
                try:
                    estimator.fit(
                        transformed[selected],
                        labels[selected, k],
                        sample_weight=_weights(groups[selected].tolist()),
                    )
                except ConvergenceWarning as exc:
                    raise ValueError(
                        f"Instrument adaptation did not converge for {element}"
                    ) from exc
            coefficients.append(estimator.coef_[0])
            intercepts.append(estimator.intercept_[0])
            support[element] = {
                "positive_groups": len(set(groups[selected & labels[:, k]].tolist())),
                "negative_groups": len(set(groups[selected & ~labels[:, k]].tolist())),
                "known_rows": int(selected.sum()),
                "unknown_rows": int((~selected).sum()),
            }
        self.observed_mask = np.zeros(grid.size, dtype=np.uint8)
        for band in bins:
            for indices in band:
                self.observed_mask[indices] = 1
        self.mean = np.asarray(scaler.mean_, dtype="<f8")
        self.scale = np.asarray(scaler.scale_, dtype="<f8")
        self.coefficients = np.asarray(coefficients, dtype="<f8")
        self.intercepts = np.asarray(intercepts, dtype="<f8")
        data_hash = hashlib.sha256()
        for array in (np.asarray(X, dtype="<f8"), labels, known):
            data_hash.update(np.ascontiguousarray(array).tobytes())
        data_hash.update(_json_bytes(sample_ids))
        self._manifest = {
            "schema_version": SCHEMA_VERSION,
            "artifact_type": "instrument_adapter",
            "artifact_format": "linear-logistic-npz-v1",
            "score_kind": self.score_kind,
            "calibrated": False,
            "instrument_id": instrument_id,
            "class_order": list(classes),
            "wavelength_medium": "vacuum",
            "units": "nm",
            "feature_spec": copy.deepcopy(FEATURE_SPEC),
            "observed_intervals": intervals,
            "feature_count": len(self.mean),
            "grid_sha256": _array_sha(grid),
            "mask_sha256": _array_sha(self.observed_mask),
            "C": float(C),
            "seed": seed,
            "training": {
                "rows": len(labels),
                "physical_sample_ids": sorted(set(sample_ids)),
                "physical_sample_count": len(set(sample_ids)),
                "physical_sample_ids_sha256": _sha(_json_bytes(sorted(set(sample_ids)))),
                "training_data_sha256": data_hash.hexdigest(),
                "known_support": support,
                "sample_weight": "one_total_weight_per_physical_sample_per_known_class",
            },
            "provenance": provenance,
            "dependency_versions": {"numpy": np.__version__, "scikit_learn": sklearn_version},
            "thresholds": None,
            "threshold_rule": THRESHOLD_RULE,
        }
        self._update_integrity()
        return self

    def _npz_bytes(self) -> bytes:
        stream = io.BytesIO()
        np.savez_compressed(stream, **{name: getattr(self, name) for name in sorted(ARRAY_NAMES)})
        return stream.getvalue()

    def _update_integrity(self) -> None:
        self._manifest["coefficients_sha256"] = _sha(self._npz_bytes())
        self._manifest["identity"] = _sha(
            _json_bytes({k: v for k, v in self._manifest.items() if k != "identity"})
        )

    def scores(self, X: np.ndarray) -> np.ndarray:
        self._require_fitted()
        features = (self._features(X) - self.mean) / self.scale
        logits = features @ self.coefficients.T + self.intercepts
        if not np.isfinite(logits).all():
            raise ValueError("Nonfinite adapted-model logits")
        return expit(logits)

    def save(self, directory: Path, *, thresholds: dict[str, float] | None = None) -> None:
        """Save numeric state and selected thresholds; nextafter(1,+inf) disables a class."""
        self._require_fitted()
        directory = Path(directory)
        if directory.is_symlink():
            raise ValueError("Symlink adapter directory is not trusted")
        directory.mkdir(parents=True, exist_ok=True)
        if any(
            (directory / name).exists() or (directory / name).is_symlink()
            for name in ("model.npz", "manifest.json")
        ):
            raise ValueError("Adapter artifact already exists; choose a new directory")
        self._manifest["thresholds"] = _thresholds(thresholds, self.class_order)
        self._update_integrity()
        for name, data in (
            ("model.npz", self._npz_bytes()),
            ("manifest.json", _json_bytes(self._manifest)),
        ):
            with (directory / name).open("xb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())

    @classmethod
    def load(
        cls,
        directory: Path,
        *,
        expected_grid_nm: np.ndarray | None = None,
        expected_observed_intervals: list[list[float]] | None = None,
        expected_class_order: tuple[str, ...] | None = None,
        expected_instrument_id: str | None = None,
    ) -> InstrumentAdapter:
        directory = Path(directory)
        if directory.is_symlink():
            raise ValueError("Symlink adapter directory is not trusted")
        for name in ("model.npz", "manifest.json"):
            path = directory / name
            if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_ARTIFACT_BYTES:
                raise ValueError("Missing, untrusted or oversized adapter artifact")
        try:
            manifest = json.loads((directory / "manifest.json").read_text())
            if not isinstance(manifest, dict) or set(manifest) != MANIFEST_NAMES:
                raise ValueError("Invalid adapter manifest fields")
            if (
                type(manifest["schema_version"]) is not int
                or manifest["schema_version"] != SCHEMA_VERSION
                or manifest["artifact_type"] != "instrument_adapter"
                or manifest["artifact_format"] != "linear-logistic-npz-v1"
                or _json_bytes(manifest["feature_spec"]) != _json_bytes(FEATURE_SPEC)
                or manifest["score_kind"] != cls.score_kind
                or manifest["calibrated"] is not False
                or manifest["wavelength_medium"] != "vacuum"
                or manifest["units"] != "nm"
                or manifest["threshold_rule"] != THRESHOLD_RULE
            ):
                raise ValueError("Incompatible adapter version or feature contract")
            expected_identity = _sha(
                _json_bytes({k: v for k, v in manifest.items() if k != "identity"})
            )
            raw = (directory / "model.npz").read_bytes()
            if manifest["identity"] != expected_identity or manifest["coefficients_sha256"] != _sha(
                raw
            ):
                raise ValueError("Adapter checksum mismatch")
            with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                if (
                    len(archive.namelist()) != len(ARRAY_NAMES)
                    or set(archive.namelist()) != {f"{name}.npy" for name in ARRAY_NAMES}
                    or sum(info.file_size for info in archive.infolist()) > MAX_ARTIFACT_BYTES
                ):
                    raise ValueError("Invalid or oversized adapter arrays")
            with np.load(io.BytesIO(raw), allow_pickle=False) as arrays:
                values = {name: arrays[name] for name in ARRAY_NAMES}
            for name, value in values.items():
                expected_dtype = np.dtype("u1") if name == "observed_mask" else np.dtype("<f8")
                if value.dtype != expected_dtype or not np.isfinite(value).all():
                    raise ValueError("Adapter arrays must have finite numeric declared dtypes")
            grid, intervals, classes, bins = _validate_contract(
                values["grid_nm"],
                manifest["observed_intervals"],
                manifest["class_order"],
                manifest["instrument_id"],
            )
            count = sum(len(band) for band in bins)
            expected_mask = np.zeros(grid.size, dtype=np.uint8)
            for band in bins:
                for indices in band:
                    expected_mask[indices] = 1
            if (
                type(manifest["feature_count"]) is not int
                or manifest["feature_count"] != count
                or values["mean"].shape != (count,)
                or values["scale"].shape != (count,)
                or np.any(values["scale"] <= 0)
                or values["coefficients"].shape != (len(classes), count)
                or values["intercepts"].shape != (len(classes),)
                or not np.array_equal(values["observed_mask"], expected_mask)
                or manifest["grid_sha256"] != _array_sha(grid)
                or manifest["mask_sha256"] != _array_sha(expected_mask)
            ):
                raise ValueError("Adapter feature shape, grid or observation mask mismatch")
            _provenance(manifest["provenance"])
            _thresholds(manifest["thresholds"], classes)
            _check_training_metadata(manifest, classes)
            if (
                (expected_grid_nm is not None and not np.array_equal(grid, expected_grid_nm))
                or (
                    expected_observed_intervals is not None
                    and intervals != expected_observed_intervals
                )
                or (expected_class_order is not None and classes != tuple(expected_class_order))
                or (
                    expected_instrument_id is not None
                    and manifest["instrument_id"] != expected_instrument_id
                )
            ):
                raise ValueError(
                    "Adapter is incompatible with the requested instrument or spectral contract"
                )
            model = cls()
            for name, value in values.items():
                setattr(model, name, value)
            model.observed_intervals, model.class_order, model._bins = intervals, classes, bins
            model._manifest = manifest
            return model
        except (OSError, KeyError, TypeError, json.JSONDecodeError, zipfile.BadZipFile) as exc:
            raise ValueError("Invalid adapter artifact") from exc
