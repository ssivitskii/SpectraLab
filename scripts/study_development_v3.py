"""Run the locked development-only line, calibration and refusal comparison."""

import argparse
from pathlib import Path

from spectralab_ml.config import repository_root
from spectralab_ml.evaluation.development_v3 import run_development_study_v3


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-plan", type=Path)
    parser.add_argument("--output-root", type=Path)
    parser.add_argument("--seed", type=int, default=20261019)
    args = parser.parse_args()
    print(
        run_development_study_v3(
            repository_root(),
            source_plan_path=args.source_plan,
            output_root=args.output_root,
            seed=args.seed,
        )
    )


if __name__ == "__main__":
    main()
