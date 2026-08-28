"""ROI estimation (src/twin/economics.py) -- pure arithmetic over stated,
labeled assumptions. Nothing here claims to be a measured saving.
"""

from __future__ import annotations

from twin.economics import (
    QC_LAG_UNITS,
    REWORK_COST_DELTA_USD,
    estimate_dollars_at_stake,
    estimate_units_at_risk,
)


def test_units_at_risk_scales_linearly_with_mean_risk() -> None:
    assert estimate_units_at_risk(0.0) == 0.0
    assert estimate_units_at_risk(1.0) == QC_LAG_UNITS
    assert estimate_units_at_risk(0.5) == estimate_units_at_risk(1.0) / 2


def test_units_at_risk_honors_an_explicit_qc_lag_override() -> None:
    assert estimate_units_at_risk(0.5, qc_lag_units=10.0) == 5.0


def test_dollars_at_stake_scales_linearly_with_units_at_risk() -> None:
    assert estimate_dollars_at_stake(0.0) == 0.0
    assert estimate_dollars_at_stake(1.0) == REWORK_COST_DELTA_USD
    assert estimate_dollars_at_stake(2.0) == 2 * REWORK_COST_DELTA_USD


def test_dollars_at_stake_honors_an_explicit_cost_override() -> None:
    assert estimate_dollars_at_stake(4.0, rework_cost_delta_usd=100.0) == 400.0


def test_end_to_end_estimate_is_non_negative_for_a_realistic_risk_range() -> None:
    for mean_risk in (0.0, 0.001, 0.01, 0.1, 1.0):
        units = estimate_units_at_risk(mean_risk)
        dollars = estimate_dollars_at_stake(units)
        assert units >= 0.0
        assert dollars >= 0.0
