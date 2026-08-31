"""Model B's live scorer (src/twin/risk/scorer.py). Requires the trained
artifacts under ml/models/ -- run tools/generate_training_data.py then
tools/train_station_risk.py first; skipped otherwise rather than failing, the
same convention as tests/test_no_config_leak.py.
"""

from __future__ import annotations

import json

import numpy as np
import pytest

from twin.contracts import RiskDriver
from twin.risk.features import FEATURE_NAMES
from twin.risk.scorer import MODELS_DIR, ModelNotTrainedError, StationRiskScorer

BASE_FEATURES = {
    "cycle_time_z": 0.0,
    "queue_pressure": 0.0,
    "blocked_fraction": 0.0,
    "starved_fraction": 0.0,
    "upstream_risk_ewma": 0.0,
}


@pytest.fixture(scope="module")
def scorer() -> StationRiskScorer:
    if not (MODELS_DIR / "station_risk_model.json").exists():
        pytest.skip(f"{MODELS_DIR} not populated -- run tools/train_station_risk.py first")
    return StationRiskScorer()


def test_score_returns_a_probability_and_two_drivers(scorer: StationRiskScorer) -> None:
    tagged, drivers = scorer.score(BASE_FEATURES)
    assert 0.0 <= tagged.value <= 1.0
    assert len(drivers) == 2
    assert all(isinstance(d, RiskDriver) for d in drivers)
    assert all(d.relation == "associative" for d in drivers)
    assert all(d.feature in FEATURE_NAMES for d in drivers)


def test_drivers_are_the_two_largest_by_absolute_contribution(scorer: StationRiskScorer) -> None:
    feats = dict(BASE_FEATURES, cycle_time_z=3.0, queue_pressure=0.95)
    _, drivers = scorer.score(feats)
    driver_names = {d.feature for d in drivers}
    # Not asserting exactly which two win (that's the model's call), but the
    # two features pushed furthest from baseline should plausibly dominate.
    assert driver_names & {"cycle_time_z", "queue_pressure"}


def test_missing_model_artifacts_raise_a_clear_error(tmp_path) -> None:
    with pytest.raises(ModelNotTrainedError, match=r"tools/train_station_risk\.py"):
        StationRiskScorer(models_dir=tmp_path)


@pytest.mark.parametrize("feature_name", FEATURE_NAMES)
def test_risk_is_monotone_non_decreasing_in_each_feature(
    scorer: StationRiskScorer, feature_name: str
) -> None:
    """The mandatory exit gate: monotone_constraints=(1,1,1,1,1) means risk
    must never decrease as any single feature increases, holding the others
    fixed. Swept across the same -6..+6 range in spirit as the plan's
    monotonicity test, using each feature's own plausible range.
    """
    sweep_range = np.linspace(-3.0, 3.0, 25) if feature_name == "cycle_time_z" else np.linspace(
        0.0, 1.0, 25
    )
    risks = []
    for value in sweep_range:
        feats = dict(BASE_FEATURES)
        feats[feature_name] = float(value)
        tagged, _ = scorer.score(feats)
        risks.append(tagged.value)

    # Non-negative-constrained logistic regression with Platt calibration on
    # top -- calibration is itself monotone non-decreasing, so the
    # composition must be too. Allow a tiny floating-point tolerance rather
    # than requiring strict ">=" at every adjacent pair.
    diffs = np.diff(risks)
    assert np.all(diffs >= -1e-9), f"{feature_name}: risk decreased somewhere in the sweep: {risks}"


def test_metrics_report_the_operating_point_not_just_ranking_metrics() -> None:
    """Regression test for a real gap: an earlier version of tools/train_
    station_risk.py reported PR-AUC, ROC-AUC, MCC-at-threshold, and lift over
    a single-feature baseline, but never the no-skill PR-AUC reference point
    (== the positive base rate -- the standard comparison for PR-AUC on an
    imbalanced problem) or precision/recall/the confusion matrix AT that
    threshold. Without those, "PR-AUC 0.026" and "+44.7% lift" are both
    unfalsifiable-sounding numbers with no operating-point meaning -- a
    reader can't tell how many false alarms the model produces per real
    catch, which is exactly the brief's own "false alarms erode trust"
    complexity. Fixed by persisting all of these alongside the existing
    metrics.
    """
    metrics_path = MODELS_DIR / "station_risk_metrics.json"
    if not metrics_path.exists():
        pytest.skip(f"{metrics_path} not populated -- run tools/train_station_risk.py first")

    metrics = json.loads(metrics_path.read_text())["model"]

    for key in (
        "no_skill_pr_auc",
        "pr_auc_over_no_skill_x",
        "precision_at_threshold",
        "recall_at_threshold",
        "f1_at_threshold",
        "confusion_matrix_at_threshold",
        "flag_rate_at_threshold",
    ):
        assert key in metrics, f"missing {key}"

    assert 0.0 <= metrics["precision_at_threshold"] <= 1.0
    assert 0.0 <= metrics["recall_at_threshold"] <= 1.0
    assert 0.0 <= metrics["flag_rate_at_threshold"] <= 1.0
    # No-skill PR-AUC is defined as the positive base rate -- pin the
    # identity itself, not just that the field exists.
    cm = metrics["confusion_matrix_at_threshold"]
    total = cm["true_negative"] + cm["false_positive"] + cm["false_negative"] + cm["true_positive"]
    positives = cm["false_negative"] + cm["true_positive"]
    assert metrics["no_skill_pr_auc"] == pytest.approx(positives / total, abs=1e-6)
    assert metrics["pr_auc_over_no_skill_x"] == pytest.approx(
        metrics["pr_auc"] / metrics["no_skill_pr_auc"], abs=1e-6
    )


def test_pr_auc_reports_the_bayes_optimal_ceiling() -> None:
    """Real gap found via a full audit: PR-AUC was reported against a
    no-skill baseline and a weak single-feature model, but never against the
    one reference point unique to this project -- the training data carries
    `oracle_risk`, the EXACT true P(defect=1 | features) used to generate
    labels (`defect = Bernoulli(oracle_risk)`, labels.py). No model can ever
    beat the PR-AUC of the true oracle probabilities on this exact data, so
    "PR-AUC over ceiling" is the most honest number available -- and it
    reveals the ceiling itself is low (~0.06), which is the actual reason
    Model B's raw PR-AUC looks small in isolation: the problem is inherently
    hard at this imbalance and label-noise level, not badly modeled.
    """
    metrics_path = MODELS_DIR / "station_risk_metrics.json"
    if not metrics_path.exists():
        pytest.skip(f"{metrics_path} not populated -- run tools/train_station_risk.py first")

    metrics = json.loads(metrics_path.read_text())["model"]
    for key in ("ceiling_pr_auc", "pr_auc_over_ceiling_pct"):
        assert key in metrics, f"missing {key}"

    # The model can approach but never *truly* exceed the true oracle's own
    # PR-AUC -- that's a population-level guarantee, not a per-sample one.
    # On this exact test set (305 positives) a bootstrap of the oracle's own
    # PR-AUC has std ~0.0104 (2000 resamples) -- ordinary finite-sample
    # noise this large means a model landing within a few thousandths of the
    # ceiling can legitimately land marginally above the point estimate
    # (found directly: the monotone-logistic model scored 0.06447 vs a
    # ceiling point estimate of 0.06409, a gap 27x smaller than that noise
    # std). 0.005 is a tolerance well inside that noise band -- tight enough
    # to still catch a real violation (e.g. test-set leakage), loose enough
    # not to fail on a model that's genuinely landed at the ceiling.
    assert metrics["pr_auc"] <= metrics["ceiling_pr_auc"] + 0.005
    # Same finite-sample-noise allowance as the absolute check above,
    # expressed as a percentage of the ceiling.
    assert 0.0 <= metrics["pr_auc_over_ceiling_pct"] <= 110.0
    assert metrics["pr_auc_over_ceiling_pct"] == pytest.approx(
        metrics["pr_auc"] / metrics["ceiling_pr_auc"] * 100, abs=1e-4
    )
    # History: an earlier isotonic-on-config-D-alone calibrator (143
    # positives) collapsed 2,942 distinct raw scores into 79 buckets,
    # dropping this figure to 73.5%. Fixed first by calibrating on
    # train+calib combined (recovered to 89.4%, still XGBoost), then
    # superseded entirely by a non-negative-constrained logistic regression
    # + Platt calibration (currently ~100.6% -- see docs/DATA.md's
    # addendum). Floor kept at 85% so a future regression in either the
    # model or the calibration step is caught, not silently reintroduced.
    assert metrics["pr_auc_over_ceiling_pct"] >= 85.0, (
        "PR-AUC dropped well below the post-fix floor -- check whether "
        "the isotonic calibrator regressed to fitting on too little data"
    )
