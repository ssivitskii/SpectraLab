from __future__ import annotations

import hashlib
import itertools
import json
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any

import numpy as np

from spectralab_ml.data.reference import ReferenceCatalog
from spectralab_ml.models.validation import training_arrays
from spectralab_ml.preprocessing import SpectrumPreprocessor
from spectralab_ml.simulation import GenerationConfig, generate_spectrum


@dataclass
class DatasetSplit:
    X: np.ndarray
    y: np.ndarray
    base_sample_ids: tuple[str, ...]


@dataclass
class DatasetBundle:
    train: DatasetSplit
    validation: DatasetSplit
    test: DatasetSplit
    class_order: tuple[str, ...]
    dataset_hash: str
    split_hash: str
    records: list[dict[str, Any]]
    reference_metadata: dict[str, Any]

    def validate(self, *, expected_features: int | None = None) -> None:
        """Reject leaked or malformed caller-supplied splits before any fitting."""
        if (
            not self.class_order
            or any(not isinstance(name, str) or not name.strip() for name in self.class_order)
            or len(set(self.class_order)) != len(self.class_order)
        ):
            raise ValueError("Dataset class_order must contain unique nonempty names")
        seen: set[str] = set()
        feature_count = expected_features
        for name in ("train", "validation", "test"):
            split = getattr(self, name)
            X, y = training_arrays(split.X, split.y)
            if feature_count is None:
                feature_count = X.shape[1]
            if X.shape[1] != feature_count or y.shape[1] != len(self.class_order):
                raise ValueError(f"Dataset {name} feature/class dimensions are incompatible")
            ids = split.base_sample_ids
            if len(ids) != len(X) or any(
                not isinstance(value, str) or not value.strip() for value in ids
            ):
                raise ValueError(f"Dataset {name} requires one nonempty base ID per row")
            groups = set(ids)
            if seen & groups:
                raise ValueError("Dataset base sample groups overlap across splits")
            seen.update(groups)
            group_labels: dict[str, np.ndarray] = {}
            for base_id, label in zip(ids, y, strict=True):
                if base_id in group_labels and not np.array_equal(group_labels[base_id], label):
                    raise ValueError("Variations of a base sample have inconsistent labels")
                group_labels[base_id] = label

    @property
    def split_ids(self) -> dict[str, list[str]]:
        return {
            name: sorted(set(getattr(self, name).base_sample_ids))
            for name in ("train", "validation", "test")
        }

    def save(self, directory: Path) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(
            directory / "spectra.npz",
            wavelength_nm=np.asarray(self.records[0]["wavelength_nm"]),
            intensity=np.asarray([r["intensity"] for r in self.records]),
            clean_signal=np.asarray([r["clean_signal"] for r in self.records]),
            labels=np.asarray(
                [[r["true_labels"][e] for e in self.class_order] for r in self.records]
            ),
            base_sample_ids=np.asarray([r["base_sample_id"] for r in self.records]),
        )
        metadata = {
            "schema_version": 1,
            "dataset_hash": self.dataset_hash,
            "split_hash": self.split_hash,
            "class_order": self.class_order,
            "reference": self.reference_metadata,
            "split_ids": self.split_ids,
            "samples": [
                {
                    k: v
                    for k, v in r.items()
                    if k not in ("wavelength_nm", "intensity", "clean_signal")
                }
                for r in self.records
            ],
        }
        (directory / "metadata.json").write_text(
            json.dumps(metadata, indent=2, allow_nan=False), encoding="utf-8"
        )


def split_base_sample_ids(
    base_ids: list[str], seed: int, train_fraction: float = 0.6, validation_fraction: float = 0.2
) -> dict[str, set[str]]:
    if len(base_ids) < 5 or len(set(base_ids)) != len(base_ids):
        raise ValueError(
            "At least five unique base_sample_id values are required before variations"
        )
    if not (
        0 < train_fraction < 1
        and 0 < validation_fraction < 1
        and train_fraction + validation_fraction < 1
    ):
        raise ValueError("Invalid split fractions")
    shuffled = np.asarray(base_ids)[np.random.default_rng(seed).permutation(len(base_ids))]
    first = int(len(shuffled) * train_fraction)
    second = int(len(shuffled) * (train_fraction + validation_fraction))
    splits = {
        "train": set(shuffled[:first]),
        "validation": set(shuffled[first:second]),
        "test": set(shuffled[second:]),
    }
    if any(not ids for ids in splits.values()):
        raise ValueError("Empty split")
    assert not (
        splits["train"] & splits["validation"]
        or splits["train"] & splits["test"]
        or splits["validation"] & splits["test"]
    )
    return splits


def build_demo_dataset(
    catalog: ReferenceCatalog,
    preprocessor: SpectrumPreprocessor,
    class_order: tuple[str, ...],
    *,
    seed: int = 42,
    base_samples: int = 36,
    variations: int = 2,
    combinations: list[tuple[str, ...]] | None = None,
    ion_stage: int = 1,
    generation_profile: str = "demo",
) -> DatasetBundle:
    if generation_profile not in {"demo", "line_robust_v1"}:
        raise ValueError("Unknown synthetic generation profile")
    if (
        not class_order
        or len(set(class_order)) != len(class_order)
        or any(not isinstance(element, str) or not element.strip() for element in class_order)
    ):
        raise ValueError("class_order must contain unique nonempty element names")
    if base_samples < max(12, 2 * len(class_order)) or not 1 <= variations <= 100:
        raise ValueError("Insufficient base samples or invalid variation count")
    all_combinations = (
        combinations
        if combinations is not None
        else [combo for size in (1, 2, 3) for combo in itertools.combinations(class_order, size)]
    )
    if not all_combinations or any(
        not 1 <= len(c) <= 3 or len(set(c)) != len(c) or not set(c) <= set(class_order)
        for c in all_combinations
    ):
        raise ValueError("Invalid configured combinations")
    rng = np.random.default_rng(seed)
    # Cycle mixture sizes so even the smoke dataset includes triples.
    ordered = []
    buckets = [[c for c in all_combinations if len(c) == size] for size in (1, 2, 3)]
    for group in itertools.zip_longest(*buckets):
        ordered.extend(c for c in group if c is not None)
    definitions = {
        f"base-{i:04d}": (
            ordered[i % len(ordered)],
            tuple(float(v) for v in rng.uniform(0.35, 1.2, len(ordered[i % len(ordered)]))),
        )
        for i in range(base_samples)
    }
    if generation_profile == "line_robust_v1":
        # Composition and relative contributions belong to the base group, not a shot.
        # Weak components are explicit positives; absent components remain controls.
        for base_id, (combo, _) in definitions.items():
            weights = np.exp(rng.uniform(np.log(0.02), 0, len(combo)))
            definitions[base_id] = (combo, tuple((weights / weights.max()).tolist()))
    for offset in range(1000):
        allocations = split_base_sample_ids(list(definitions), seed + offset)
        covered = set(
            itertools.chain.from_iterable(definitions[i][0] for i in allocations["train"])
        )
        if covered == set(class_order):
            break
    else:
        raise ValueError("Cannot construct training split covering every configured element")
    rows = {s: [] for s in allocations}
    labels = {s: [] for s in allocations}
    groups = {s: [] for s in allocations}
    records = []
    for base_id, (combo, weights) in definitions.items():
        split = next(s for s, ids in allocations.items() if base_id in ids)
        for variant in range(variations):
            params = GenerationConfig(
                elements=combo,
                component_weights=weights,
                wavelength_start_nm=preprocessor.wavelength_start_nm,
                wavelength_end_nm=preprocessor.wavelength_end_nm,
                sampling_step_nm=preprocessor.model_step_nm,
                snr_db=float(rng.choice([10, 20, 30, 40])),
                instrumental_fwhm_nm=float(rng.choice([0.2, 0.5, 1.0])),
                calibration_shift_nm=float(rng.normal(0, 0.015)),
                seed=seed * 10000 + int(base_id[5:]) * 100 + variant,
                **(
                    {"ion_stage": ion_stage}
                    if "ion_stage" in GenerationConfig.__dataclass_fields__
                    else {}
                ),
            )
            if generation_profile == "line_robust_v1":
                params = replace(params, calibration_shift_nm=float(rng.uniform(-0.5, 0.5)))
            spectrum = generate_spectrum(
                params, catalog, class_order=class_order, base_sample_id=base_id
            )
            if generation_profile == "line_robust_v1":
                peak = float(np.max(spectrum.clean_signal))
                span = preprocessor.wavelength_end_nm - preprocessor.wavelength_start_nm
                params = replace(
                    params,
                    background_level=float(rng.choice([0, 0.5, 2])) * peak,
                    background_slope=float(rng.uniform(-0.5, 0.5)) * peak / span,
                )
                spectrum = generate_spectrum(
                    params, catalog, class_order=class_order, base_sample_id=base_id
                )
            records.append({**spectrum.to_dict(), "split": split})
            rows[split].append(preprocessor.transform(spectrum.wavelength_nm, spectrum.intensity))
            labels[split].append([spectrum.true_labels[e] for e in class_order])
            groups[split].append(base_id)
    # Preprocessing is stateless. Its shared fit validates train only, and learns nothing.
    preprocessor.fit(
        [
            (np.asarray(r["wavelength_nm"]), np.asarray(r["intensity"]))
            for r in records
            if r["split"] == "train"
        ]
    )
    identity = {
        "records": records,
        "reference_lines": [asdict(line) for line in catalog.lines],
        "source": catalog.source_name,
        "reference_metadata": {k: v for k, v in catalog.metadata.items() if k != "imported_at"},
        "classes": class_order,
        "preprocessing": preprocessor.to_dict(),
    }
    if generation_profile != "demo":
        identity["generation_profile"] = generation_profile
    digest = hashlib.sha256(
        json.dumps(identity, sort_keys=True, allow_nan=False, separators=(",", ":")).encode()
    ).hexdigest()
    split_hash = hashlib.sha256(
        json.dumps({s: sorted(ids) for s, ids in allocations.items()}, sort_keys=True).encode()
    ).hexdigest()
    parts = {
        s: DatasetSplit(np.stack(rows[s]), np.asarray(labels[s]), tuple(groups[s]))
        for s in allocations
    }
    return DatasetBundle(
        **parts,
        class_order=class_order,
        dataset_hash=digest,
        split_hash=split_hash,
        records=records,
        reference_metadata=catalog.metadata,
    )
