"""ml/benchmark_public.py's pure, non-network functions.

Deliberately does NOT call `load_data()` or `main()` -- those fetch AI4I 2020
from a live external server (ucimlrepo), which this project does not want the
automated, every-push test suite depending on (an external outage would make
CI flaky for a component that isn't even part of the shipped product). The
full run was verified by hand and is documented in
docs/phases/phase-08-predictive-layer.md, the same way Phase 4/5's analysis
scripts (tools/run_sensitivity_analysis.py, tools/run_detector_benchmark.py)
are verified by hand rather than re-run on every CI push.
"""

from __future__ import annotations

import math

import numpy as np
from ml.benchmark_public import Metrics, best_threshold_for_mcc, evaluate


def test_best_threshold_for_mcc_finds_the_perfect_separator() -> None:
    y_true = np.array([0, 0, 0, 0, 1, 1, 1, 1])
    y_prob = np.array([0.1, 0.2, 0.3, 0.4, 0.6, 0.7, 0.8, 0.9])
    threshold, mcc = best_threshold_for_mcc(y_true, y_prob)
    assert 0.4 < threshold < 0.6
    assert mcc == 1.0


def test_best_threshold_for_mcc_handles_no_separation() -> None:
    rng = np.random.default_rng(0)
    y_true = rng.integers(0, 2, size=200)
    y_prob = rng.random(200)  # uninformative
    threshold, mcc = best_threshold_for_mcc(y_true, y_prob)
    assert -1.0 <= mcc <= 1.0
    assert 0.0 < threshold < 1.0


def test_evaluate_returns_a_populated_metrics_object() -> None:
    y_true = np.array([0, 0, 1, 1, 0, 1])
    y_prob = np.array([0.1, 0.3, 0.6, 0.9, 0.2, 0.7])
    m = evaluate("test_model", y_true, y_prob)
    assert isinstance(m, Metrics)
    assert m.name == "test_model"
    assert 0.0 <= m.pr_auc <= 1.0
    assert 0.0 <= m.roc_auc <= 1.0
    assert 0.0 <= m.brier <= 1.0
    assert -1.0 <= m.mcc_at_threshold <= 1.0
    assert 0.0 < m.threshold < 1.0


def test_evaluate_perfect_predictions_score_maximally() -> None:
    y_true = np.array([0, 0, 0, 1, 1, 1])
    y_prob = np.array([0.01, 0.02, 0.03, 0.98, 0.99, 0.97])
    m = evaluate("perfect", y_true, y_prob)
    assert m.pr_auc == 1.0
    assert m.roc_auc == 1.0
    assert m.brier < 0.01
    assert m.mcc_at_threshold == 1.0


def test_metrics_is_frozen() -> None:
    m = Metrics("x", pr_auc=0.5, roc_auc=0.5, brier=0.1, mcc_at_threshold=0.2, threshold=0.5)
    try:
        m.pr_auc = 0.9  # type: ignore[misc]
        raised = False
    except AttributeError:
        raised = True
    assert raised, "Metrics should be a frozen dataclass"


def test_nan_metrics_are_representable_for_the_smote_inflated_row() -> None:
    """The deliberately-broken SMOTE-then-CV row has no meaningful ROC-AUC/
    Brier/MCC -- Metrics must be able to hold NaN for those rather than
    forcing a fake number into the table.
    """
    m = Metrics(
        "smote_then_cv_INFLATED",
        pr_auc=0.99,
        roc_auc=float("nan"),
        brier=float("nan"),
        mcc_at_threshold=float("nan"),
        threshold=float("nan"),
    )
    assert math.isnan(m.roc_auc)
    assert math.isnan(m.brier)
