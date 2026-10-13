"""Leave-one-element-out synthetic abstention experiment; no real-world claims."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

import numpy as np
from spectralab_ml.config import (
    load_project_config,
    load_reference_catalog,
    repository_root,
    stable_hash,
)
from spectralab_ml.datasets import build_demo_dataset
from spectralab_ml.evaluation.openset import ResidualRejector, rejection_metrics
from spectralab_ml.preprocessing import SpectrumPreprocessor
from spectralab_ml.simulation import GenerationConfig, generate_spectrum
from spectralab_ml.training import make_reference_templates


def main() -> None:
    root = repository_root()
    project = load_project_config(root / "ml/configs/benchmark_project.yaml")
    # A real line reference is used; every observed spectrum in this experiment is synthetic.
    cache = root / "data/reference/nist_neutral_350_800/catalog.json"
    if not cache.exists():
        raise ValueError("Run make reference before open-set benchmark")
    catalog = load_reference_catalog(project, cache)
    processor = SpectrumPreprocessor(
        project.wavelength_start_nm, project.wavelength_end_nm, project.model_step_nm
    )
    known = project.elements[:-1]
    heldout = project.elements[-1]
    seeds = [7, 17, 29, 43, 71]
    rows = []
    for seed in seeds:
        dataset = build_demo_dataset(
            catalog, processor, known, seed=seed, base_samples=48, variations=1
        )
        rejection = ResidualRejector(make_reference_templates(catalog, processor, known))
        rejection.fit_known_development(dataset.validation.X)
        conditions = {"known": dataset.test.X}
        for condition in ("unknown_only", "known_unknown_mixture"):
            vectors = []
            for index in range(12):
                elements = (
                    (heldout,)
                    if condition == "unknown_only"
                    else (known[index % len(known)], heldout)
                )
                config = GenerationConfig(
                    elements=elements,
                    component_weights=(1.0,) * len(elements),
                    wavelength_start_nm=project.wavelength_start_nm,
                    wavelength_end_nm=project.wavelength_end_nm,
                    sampling_step_nm=project.model_step_nm,
                    snr_db=20,
                    seed=seed * 10000 + 5000 + index,
                )
                spectrum = generate_spectrum(config, catalog, class_order=project.elements)
                vectors.append(processor.transform(spectrum.wavelength_nm, spectrum.intensity))
            conditions[condition] = np.stack(vectors)
        rng = np.random.default_rng(seed + 50000)
        bad = rng.normal(size=(12, len(processor.model_grid)))
        bad /= np.linalg.norm(bad, axis=1, keepdims=True)
        conditions["unsuitable_noise"] = bad
        rows.append(
            {
                "seed": seed,
                "threshold": rejection.threshold,
                "calibration_base_ids": dataset.split_ids["validation"],
                "test_base_ids": dataset.split_ids["test"],
                "conditions": {
                    name: {
                        **rejection_metrics(rejection.abstain(X), known=name == "known"),
                        "novelty_scores": rejection.novelty_scores(X).tolist(),
                    }
                    for name, X in conditions.items()
                },
            }
        )
    result = {
        "schema_version": 1,
        "protocol": "leave-one-element-out residual abstention",
        "known_elements": known,
        "heldout_unknown_elements": [heldout],
        "threshold_selection": "95th percentile of known development residuals; test untouched",
        "seeds": seeds,
        "source": catalog.metadata,
        "source_hash": stable_hash({"lines": [asdict(line) for line in catalog.lines]}),
        "project": asdict(project),
        "runs": rows,
        "limitations": "Synthetic only. Residual rejection does not identify unknown substances."
        " Empty detections are a separate event; mixed unknowns can be falsely accepted.",
    }
    path = Path(root / "reports/verification/open_set.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8"
    )
    print(path)


if __name__ == "__main__":
    main()
