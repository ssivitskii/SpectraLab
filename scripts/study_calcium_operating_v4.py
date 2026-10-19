"""Run the declared last adaptive operating-point round without model refitting."""

from spectralab_ml.config import repository_root
from spectralab_ml.evaluation.calcium_operating_v4 import run_operating_study

if __name__ == "__main__":
    print(run_operating_study(repository_root()))
