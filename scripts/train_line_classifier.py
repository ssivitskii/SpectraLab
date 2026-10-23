"""Train and install the line-logistic candidate for the local Analysis workspace."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from spectralab_ml.config import load_project_config, load_reference_catalog, repository_root
from spectralab_ml.datasets import build_demo_dataset
from spectralab_ml.preprocessing import SpectrumPreprocessor
from spectralab_ml.training import train_and_save_models


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--base-samples", type=int, default=144)
    parser.add_argument("--profile", choices=("demo", "line_robust_v1"), default="demo")
    parser.add_argument("--output", type=Path, default=repository_root() / "artifacts/models")
    args = parser.parse_args()
    project = load_project_config()
    catalog = load_reference_catalog(project)
    processor = SpectrumPreprocessor(
        project.wavelength_start_nm, project.wavelength_end_nm, project.model_step_nm
    )
    dataset = build_demo_dataset(
        catalog,
        processor,
        project.elements,
        seed=args.seed,
        base_samples=args.base_samples,
        variations=2,
        ion_stage=project.ion_stage,
        generation_profile=args.profile,
    )
    model_id = "line-logistic" if args.profile == "demo" else "line-logistic-robust"
    results = train_and_save_models(
        dataset, catalog, processor, args.output, seed=args.seed, model_ids=[model_id]
    )
    print(
        json.dumps(
            {
                "model_id": model_id,
                "generation_profile": args.profile,
                "artifact": str(args.output / model_id),
                "source": catalog.source_name,
                "scope": "synthetic grouped holdout; real spectra unvalidated",
                "dataset_hash": dataset.dataset_hash,
                "split_hash": dataset.split_hash,
                "metrics": results[model_id],
            },
            indent=2,
            ensure_ascii=False,
            allow_nan=False,
        )
    )


if __name__ == "__main__":
    main()
