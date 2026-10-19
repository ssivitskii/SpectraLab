"""Run the disclosed second-round calcium crossfit experiment with BLAS threads=1."""

from spectralab_ml.config import repository_root
from spectralab_ml.evaluation.calcium_crossfit_v4 import run_crossfit_study

if __name__ == "__main__":
    print(run_crossfit_study(repository_root()))
