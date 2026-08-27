"""The live, production bottleneck diagnostic -- Active Period Method
(momentary rule) with a measured significance annotation, mode decomposition,
and Bottleneck Walk operator phrasing.

This is distinct from `detectors.py`, which implements six methods purely for
the offline benchmark against Phase 4's ground truth. This module is what a
running twin actually uses.

Momentary rule (Roser, Nakano & Tanaka 2001): among stations *currently* in an
active period, the bottleneck is whichever one's active period started
earliest. A breakdown does not end an active period (contracts.StationState),
so this rule is not confused by a station that recently transitioned within
its own active period.

Significance, measured rather than assumed (Phase 5 finding): a hard
suppression gate that only names a bottleneck when ANOVA + Tukey-Kramer finds
a significant difference would delay detection past the 2-second live-response
requirement, because at 8 ticks/s a station has completed at most 1-2 active
periods in that window -- nowhere near enough for ANOVA's asymptotics, and
consecutive active periods at the same station are autocorrelated in any case
(see docs/LIMITATIONS.md), violating the test's independence assumption
outright. So significance here is a **confidence annotation** computed from
the run's ACCUMULATED history of active-period durations, not a live gate on
the current pick: the momentary rule always answers immediately, and the
annotation says how statistically confident that answer is once enough
history exists to ask the question honestly.
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass

import numpy as np
from scipy import stats as scipy_stats
from statsmodels.stats.multicomp import pairwise_tukeyhsd

from twin.contracts import BottleneckVerdict, StationState

MIN_PERIODS_FOR_SIGNIFICANCE = 3  # per station, minimum before ANOVA is even attempted


@dataclass(frozen=True)
class StationView:
    """The minimal facts this module needs from a station -- decoupled from
    the concrete Station class so this can be driven by either a live
    simulation or a replayed fixture (source-agnosticism, sources.py).
    """

    station_id: str
    state: StationState
    active_period_start: float | None
    active_period_durations: list[float]  # completed periods only, most recent last
    time_in_state: dict[StationState, float]

    @classmethod
    def from_station(cls, station: object) -> StationView:
        """Adapter from `twin.sim.station.Station`. Takes `object` rather than
        importing Station directly, so this diagnostic module has no import
        dependency on the simulation package -- consistent with the
        source-agnosticism discipline in sources.py: the live twin's diagnostic
        layer must work identically whether fed by the simulation or a real
        OPC-UA/historian tap, and neither should import the other.
        """
        return cls(
            station_id=station.station_id,  # type: ignore[attr-defined]
            state=station.state,  # type: ignore[attr-defined]
            active_period_start=station.active_period_start,  # type: ignore[attr-defined]
            active_period_durations=[
                end - start for start, end in station.active_periods  # type: ignore[attr-defined]
            ],
            time_in_state=dict(station.time_in_state),  # type: ignore[attr-defined]
        )


def momentary_bottleneck(stations: list[StationView]) -> tuple[str | None, str | None]:
    """Returns (bottleneck_id, runner_up_id). None if no station is currently
    in an active period at all (a genuinely idle line).
    """
    candidates = [
        (s.station_id, s.active_period_start)
        for s in stations
        if s.state.is_active and s.active_period_start is not None
    ]
    if not candidates:
        return None, None
    ranked = sorted(candidates, key=lambda kv: kv[1])
    bottleneck_id = ranked[0][0]
    runner_up_id = ranked[1][0] if len(ranked) > 1 else None
    return bottleneck_id, runner_up_id


def mode_decomposition(station: StationView) -> dict[str, float]:
    """Fraction of this station's ACTIVE time spent in each active substate --
    "why" it is active, not just "that" it is. E.g. {"working": 0.41,
    "repair": 0.59} reads as "downtime-dominant" rather than a bare red dot.
    """
    active_states = (
        StationState.WORKING,
        StationState.DOWN,
        StationState.REPAIR,
        StationState.SETUP,
    )
    active_total = sum(station.time_in_state.get(s, 0.0) for s in active_states)
    if active_total <= 0:
        return {}
    return {s.value: station.time_in_state.get(s, 0.0) / active_total for s in active_states}


def bottleneck_walk_explanation(station_id: str, mode: dict[str, float]) -> str:
    """Operator-facing sentence, per the Bottleneck Walk framing (Roser,
    Lorentzen & Deuse 2015): queue building faster than downstream can drain,
    plus which mode dominates when it is not simply "working slowly."
    """
    if not mode:
        return f"{station_id} -- queue building faster than downstream can drain."
    dominant = max(mode, key=lambda k: mode[k])
    if dominant == "working" and mode["working"] > 0.9:
        return f"{station_id} -- queue building faster than downstream can drain."
    pct = round(mode[dominant] * 100)
    return f"{station_id} -- {dominant}-dominant, {pct}% {dominant}."


def significance_annotation(
    stations: list[StationView], bottleneck_id: str
) -> tuple[str, float | None]:
    """ANOVA across all stations' active-period durations, then Tukey-Kramer
    to check the bottleneck is significantly different from every other
    station. Returns (confidence, p_value). Confidence is:
      - "none" if there is no current pick to annotate
      - "provisional" if fewer than MIN_PERIODS_FOR_SIGNIFICANCE periods exist
        yet for the bottleneck, or ANOVA/Tukey-Kramer does not find it
        significantly different from every other station
      - "established" if Tukey-Kramer rejects equality against every other
        station at alpha=0.05

    Known limitation, stated rather than hidden (docs/LIMITATIONS.md):
    consecutive active periods at one station are not independent draws (a
    slow upstream station tends to stay slow), so ANOVA's independence
    assumption does not strictly hold here. This annotation is a useful signal
    that the difference is larger than run-to-run noise, not a rigorously
    calibrated p-value in the textbook sense.
    """
    groups: dict[str, list[float]] = {
        s.station_id: s.active_period_durations
        for s in stations
        if len(s.active_period_durations) >= 1
    }
    bottleneck_durations = groups.get(bottleneck_id, [])
    if len(bottleneck_durations) < MIN_PERIODS_FOR_SIGNIFICANCE:
        return "provisional", None

    eligible = {sid: d for sid, d in groups.items() if len(d) >= 1}
    if len(eligible) < 2:
        return "provisional", None

    all_values = np.concatenate([np.asarray(d) for d in eligible.values()])
    all_labels = np.concatenate(
        [np.full(len(d), sid) for sid, d in eligible.items()]
    )

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # scipy warns on tiny groups; we handle that ourselves
        _f_stat, p_value = scipy_stats.f_oneway(*eligible.values())

    if not np.isfinite(p_value) or p_value >= 0.05:
        return "provisional", float(p_value) if np.isfinite(p_value) else None

    tukey = pairwise_tukeyhsd(all_values, all_labels, alpha=0.05)

    # Walk statsmodels' public summary table rather than internal attribute
    # names, which are not part of its documented API and could change.
    significant_against_all = True
    summary_rows = tukey.summary().data[1:]  # skip header row
    involved = [row for row in summary_rows if bottleneck_id in (row[0], row[1])]
    if not involved:
        return "provisional", float(p_value)
    for row in involved:
        reject = row[-1]
        # `reject` is numpy.bool_, not Python's built-in bool -- `is True`
        # is always False for it even when the value is truthy. Found via a
        # direct unit test with an obviously-significant synthetic dataset
        # that should have reached "established" and did not.
        if not bool(reject):
            significant_against_all = False
            break

    confidence = "established" if significant_against_all else "provisional"
    return confidence, float(p_value)


def diagnose(stations: list[StationView]) -> BottleneckVerdict:
    bottleneck_id, runner_up_id = momentary_bottleneck(stations)
    if bottleneck_id is None:
        return BottleneckVerdict(station_id=None, confidence="none", explanation="Line is idle.")

    by_id = {s.station_id: s for s in stations}
    mode = mode_decomposition(by_id[bottleneck_id])
    explanation = bottleneck_walk_explanation(bottleneck_id, mode)
    confidence, p_value = significance_annotation(stations, bottleneck_id)

    return BottleneckVerdict(
        station_id=bottleneck_id,
        confidence=confidence,
        p_value=p_value,
        runner_up_id=runner_up_id,
        mode_decomposition=mode,
        explanation=explanation,
    )


__all__ = [
    "MIN_PERIODS_FOR_SIGNIFICANCE",
    "bottleneck_walk_explanation",
    "diagnose",
    "mode_decomposition",
    "momentary_bottleneck",
    "significance_annotation",
]
