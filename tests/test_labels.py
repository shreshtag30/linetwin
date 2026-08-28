"""The synthetic label-generating process (docs/DATA.md)."""

from __future__ import annotations

from twin.risk.labels import BIAS, WEIGHTS, oracle_risk

BASE_FEATURES = {
    "cycle_time_z": 0.0,
    "queue_pressure": 0.0,
    "blocked_fraction": 0.0,
    "starved_fraction": 0.0,
    "upstream_risk_ewma": 0.0,
}


def test_oracle_risk_is_a_probability() -> None:
    assert 0.0 <= oracle_risk(BASE_FEATURES) <= 1.0


def test_oracle_risk_is_monotone_increasing_in_every_feature() -> None:
    """The whole point of the hand-chosen weights (docs/DATA.md): every
    feature's assumed direction is positive. If a future edit to WEIGHTS
    introduces a negative weight, this must fail.
    """
    baseline = oracle_risk(BASE_FEATURES)
    for name in WEIGHTS:
        bumped = dict(BASE_FEATURES)
        bumped[name] = 2.0
        assert oracle_risk(bumped) > baseline, f"{name} is not monotone increasing"


def test_oracle_risk_at_baseline_features_is_near_calibrated_prevalence() -> None:
    """Not a strict equality -- BIAS was calibrated against the full
    recursive feature distribution (tools/calibrate_label_bias.py), not
    against this single all-zero feature vector -- but it should be in the
    right neighborhood, not off by orders of magnitude.
    """
    risk = oracle_risk(BASE_FEATURES)
    assert 0.0001 < risk < 0.01


def test_weights_dict_and_bias_are_consistent_with_the_calibration_record() -> None:
    # A change to WEIGHTS without re-running calibrate_label_bias.py would
    # silently invalidate the calibration -- this doesn't re-derive BIAS (that
    # requires a full simulation run), but it does pin the values this
    # project committed to, so a silent edit to either is visible in a diff.
    assert WEIGHTS == {
        "cycle_time_z": 0.9,
        "queue_pressure": 2.6,
        "blocked_fraction": 2.2,
        "starved_fraction": 1.1,
        "upstream_risk_ewma": 1.8,
    }
    assert BIAS == -8.3743
