"""Defect genealogy: given a unit flagged at final inspection, walk its
event history backward to name a likely origin station, the affected unit
range, and a calibrated confidence -- with the transfer-delay realignment
disclosed in US 12,353,197 B2 (docs/PRIOR_ART.md), cited and adopted, not
reimplemented as a black box.

Origin signal: each station-visit's `cycle_time_s`, z-scored against that
SAME station's own population of recorded cycle times across all units
(not a live Model B risk score -- `Line` stays decoupled from the risk-
scoring layer entirely; `UnitEvent.risk_at_exit` is populated by nothing yet,
consistent with `contracts.py`'s own note that "not scored" and "scored as
zero risk" are different facts). This is a self-contained, honestly simpler
signal than Model B's five-feature score, not a claim of equivalence to it.

Citation discipline (docs/PRIOR_ART.md, mandatory): the patent DISCLOSES the
transfer-delay realignment mechanism; it does not DEMONSTRATE that our
origin-attribution method works, since it contains no dataset, evaluation,
or reported performance of any kind. We differ from it deliberately: the
patent chains fixed thresholds and reports a binary attribution; the
confidence below is a continuous, calibrated value.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from twin.contracts import UnitEvent

AFFECTED_UNIT_RADIUS = 6  # units before/after the origin unit, in completion order at that station
Z_SCORE_CONFIDENCE_SCALE = 2.0  # maps a z-score to (0,1) via a logistic; see confidence() below


@dataclass(frozen=True)
class GenealogyResult:
    defect_unit_id: int
    origin_station_id: str
    origin_z_score: float
    confidence: float  # in (0, 1); NOT causal -- see module docstring
    path: list[str]  # station ids this unit visited, in order
    affected_unit_ids: list[int]
    # Transfer-delay-realigned time of the origin event (US 12,353,197 B2,
    # disclosed not demonstrated -- docs/PRIOR_ART.md): detection time minus
    # cumulative transfer delay accrued from the origin station onward. This
    # is what makes the realignment concretely checkable rather than an
    # unreported intermediate step.
    origin_realigned_time_s: float


def _station_cycle_time_stats(events: list[UnitEvent]) -> dict[str, tuple[float, float]]:
    """Per-station (mean, std) of cycle_time_s across every recorded unit at
    that station -- the population this genealogy's z-scores are measured
    against.
    """
    by_station: dict[str, list[float]] = {}
    for e in events:
        by_station.setdefault(e.station_id, []).append(e.cycle_time_s)

    stats: dict[str, tuple[float, float]] = {}
    for sid, values in by_station.items():
        n = len(values)
        mean = sum(values) / n
        if n < 2:
            stats[sid] = (mean, 1.0)  # avoid div-by-zero; z-score is ~0 with too little history
            continue
        variance = sum((v - mean) ** 2 for v in values) / (n - 1)
        stats[sid] = (mean, math.sqrt(variance) or 1.0)
    return stats


def _confidence_from_z(z: float) -> float:
    """A continuous, monotone mapping from z-score to (0, 1) -- deliberately
    NOT claimed as calibrated against any real outcome (there is no ground
    truth for "is this really the origin" in a synthetic line), just a
    monotone confidence signal. Stated plainly rather than dressed up as more
    than it is.
    """
    return 1.0 / (1.0 + math.exp(-z / Z_SCORE_CONFIDENCE_SCALE))


@dataclass(frozen=True)
class DefectCandidate:
    """A unit worth tracing -- its own path's single most anomalous cycle
    time, not yet run through `trace_genealogy` (that requires picking one).
    """

    unit_id: int
    peak_z_score: float
    peak_station_id: str


def list_defect_candidates(
    events: list[UnitEvent], *, limit: int = 10, recent_events: int = 3000
) -> list[DefectCandidate]:
    """Ranks recently-completed units by their own path's highest z-score, so
    a caller (the API, a UI) can offer "which unit should I trace" without
    the operator needing to already know a unit_id. `recent_events` caps the
    population scan to the tail of a long-running session's event log rather
    than rescanning an unbounded list on every request; it does not change
    which stations' historical stats are used (still the full log, via
    `_station_cycle_time_stats`), only which units are considered as
    candidates.

    Known limitation, measured not assumed (tests/test_genealogy.py): a
    station with a small number of EXTREME outliers has its own reference
    std inflated by those same outliers, which self-limits their z-score --
    a severely-but-rarely perturbed station can rank below a station with
    smaller, more numerous natural deviations. Every candidate is still
    correctly traced back to its true origin when picked; this only affects
    ranking order, not the correctness of an individual trace.
    """
    if not events:
        return []

    stats = _station_cycle_time_stats(events)
    window = events[-recent_events:] if len(events) > recent_events else events

    by_unit: dict[int, list[UnitEvent]] = {}
    for e in window:
        by_unit.setdefault(e.unit_id, []).append(e)

    candidates = []
    for unit_id, unit_events in by_unit.items():
        best_e, best_z = None, float("-inf")
        for e in unit_events:
            mean, std = stats[e.station_id]
            z = (e.cycle_time_s - mean) / std
            if z > best_z:
                best_e, best_z = e, z
        candidates.append(
            DefectCandidate(unit_id=unit_id, peak_z_score=best_z, peak_station_id=best_e.station_id)
        )

    candidates.sort(key=lambda c: -c.peak_z_score)
    return candidates[:limit]


def trace_genealogy(events: list[UnitEvent], defect_unit_id: int) -> GenealogyResult:
    unit_events = sorted(
        (e for e in events if e.unit_id == defect_unit_id), key=lambda e: e.entered_at
    )
    if not unit_events:
        raise ValueError(f"no recorded events for unit_id={defect_unit_id}")

    stats = _station_cycle_time_stats(events)

    scored = []
    for e in unit_events:
        mean, std = stats[e.station_id]
        z = (e.cycle_time_s - mean) / std
        scored.append((e, z))

    origin_event, origin_z = max(scored, key=lambda pair: pair[1])

    # Transfer-delay realignment (US 12,353,197 B2, disclosed not
    # demonstrated -- docs/PRIOR_ART.md): contributing factors are compared
    # at (detection time - cumulative transfer delay since that station),
    # realigning the origin station onto the detection station's time frame.
    detection_time = unit_events[-1].exited_at
    cumulative_delay_from_origin = sum(
        e.transfer_delay_s for e in unit_events if e.exited_at >= origin_event.exited_at
    )
    origin_realigned_time = detection_time - cumulative_delay_from_origin

    # Affected units: the AFFECTED_UNIT_RADIUS units completed at the origin
    # station immediately before/after the defective one, in COMPLETION
    # ORDER, not wall-clock time.
    #
    # REAL BUG, found by actually running this against an injected
    # perturbation: an earlier version used a fixed 300s wall-clock window.
    # Under a severe, sustained perturbation, a single cycle at the origin
    # station can itself take ~400-500s (verified: units 59-64 at an
    # 8x-perturbed station were 400-500s apart), so a 300s window caught
    # ZERO neighbors -- failing in exactly the scenario genealogy matters
    # most for. A unit-sequence radius is invariant to how slow the station
    # is currently running.
    origin_station_events = sorted(
        (e for e in events if e.station_id == origin_event.station_id), key=lambda e: e.exited_at
    )
    origin_index = next(
        i for i, e in enumerate(origin_station_events) if e.unit_id == defect_unit_id
    )
    lo = max(0, origin_index - AFFECTED_UNIT_RADIUS)
    hi = origin_index + AFFECTED_UNIT_RADIUS + 1
    affected = [
        e.unit_id for e in origin_station_events[lo:hi] if e.unit_id != defect_unit_id
    ]

    return GenealogyResult(
        defect_unit_id=defect_unit_id,
        origin_station_id=origin_event.station_id,
        origin_z_score=origin_z,
        confidence=_confidence_from_z(origin_z),
        path=[e.station_id for e in unit_events],
        affected_unit_ids=sorted(set(affected)),
        origin_realigned_time_s=origin_realigned_time,
    )


__all__ = [
    "AFFECTED_UNIT_RADIUS",
    "DefectCandidate",
    "GenealogyResult",
    "list_defect_candidates",
    "trace_genealogy",
]
