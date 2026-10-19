"""Run the locked, explicitly adaptive calcium specificity development study."""

import argparse
from pathlib import Path

from spectralab_ml.config import repository_root
from spectralab_ml.evaluation.calcium_v4 import run_calcium_study


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--protocol", type=Path)
    args = parser.parse_args()
    print(run_calcium_study(repository_root(), protocol_path=args.protocol))


if __name__ == "__main__":
    main()
