"""ROI estimation for the leadership view (Phase 10).

Every constant here is a STATED ASSUMPTION, not a measured fact -- named and
labeled that way deliberately, same discipline as every timing parameter in
`sim/line.py` (docs/CITATIONS.md). This module produces an ESTIMATE, not an
audited saving: LineTwin has no real before/after production run to compare
against, only its own simulated risk scores. Function names say `estimate_*`
for exactly this reason, never `actual_*` or `measured_*`.
"""

from __future__ import annotations

# synthetic -- uncalibrated. What would calibrate it: the real average
# unit-count between a mid-line station and final inspection at the plant's
# actual WIP levels, from its own MES data, which this project does not have
# access to. Stated up front rather than buried in a comment only a reader of
# the source would ever see -- this constant is rendered directly in the UI.
QC_LAG_UNITS = 40.0

# synthetic -- uncalibrated. The incremental rework/scrap cost avoided by
# catching a defect at its origin station rather than after QC_LAG_UNITS more
# stations' worth of value-add have been sunk into the unit. What would
# calibrate it: a real per-station value-add table joined to the plant's
# actual rework/scrap cost data, which this project does not have. Bosch's
# published ~0.58% defect prevalence (docs/CITATIONS.md) is the only
# real-data anchor anywhere in this module's design; this dollar figure is
# not derived from it and must not be presented as if it were.
REWORK_COST_DELTA_USD = 850.0


def estimate_units_at_risk(mean_defect_risk: float, qc_lag_units: float = QC_LAG_UNITS) -> float:
    """Units currently in the pipeline, between here and final inspection,
    carrying roughly the line's current mean defect-risk level.
    """
    return mean_defect_risk * qc_lag_units


def estimate_dollars_at_stake(
    units_at_risk: float, rework_cost_delta_usd: float = REWORK_COST_DELTA_USD
) -> float:
    """Estimated rework/scrap cost avoidable by catching those units' defects
    now rather than at final inspection. An ESTIMATE from stated assumptions
    -- see module docstring -- not a measured saving.
    """
    return units_at_risk * rework_cost_delta_usd


__all__ = [
    "QC_LAG_UNITS",
    "REWORK_COST_DELTA_USD",
    "estimate_dollars_at_stake",
    "estimate_units_at_risk",
]
