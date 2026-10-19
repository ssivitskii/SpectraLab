"""Zero-inner-FPR selection tests on fictional arrays only."""

import numpy as np
import pytest
from spectralab_ml.evaluation.calcium_operating_v4 import operating_reports
from spectralab_ml.evaluation.calcium_v4 import choose_candidate


def test_zero_budget_strict_ties_unknowns_replicates_and_candidate_selection():
    specs = [
        {
            "id": str(i),
            "family": "full-v3-ca",
            "learner": "logistic",
            "C": 0.1,
            "gamma_multiplier": 1.0,
        }
        for i in range(2)
    ]
    # Candidate0 tied positive/negative .4 must exclude both; candidate1 keeps both positives.
    p = np.array([[0.4, 0.8, 0.4, 0.3], [0.6, 0.8, 0.4, 0.3]])
    y = np.array([1, 1, 0, 0])
    ids = ["a", "b", "c", "d"]
    reports = operating_reports(p, y, ids, specs, [4, 4])
    assert choose_candidate(reports, 0.0) == 1
    assert all(r["thresholds"]["0.0"]["metrics"]["false_positive"] == 0 for r in reports)
    assert reports[0]["thresholds"]["0.0"]["metrics"]["true_positive"] == 1
    repeated = operating_reports(
        np.column_stack([p, p[:, 0], [1.0, 0.0]]),
        np.r_[y, 1, -1],
        [*ids, "a", "unknown"],
        specs,
        [4, 4],
    )
    for original, replicated in zip(reports, repeated, strict=True):
        assert original["thresholds"] == replicated["thresholds"]
        assert original["tune_brier"] == pytest.approx(replicated["tune_brier"], abs=1e-15)
    with pytest.raises(ValueError, match="finite"):
        operating_reports(p * np.nan, y, ids, specs, [4, 4])
