"""Run the preregistered expanded synthetic robustness experiment."""

from __future__ import annotations

import argparse
from pathlib import Path

from spectralab_ml.evaluation.robustness import run_benchmark


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("ml/configs/expanded_benchmark.yaml"))
    parser.add_argument("--output", type=Path, default=Path("reports/verification/expanded"))
    args = parser.parse_args()
    print(run_benchmark(args.config, args.output))


if __name__ == "__main__":
    main()
