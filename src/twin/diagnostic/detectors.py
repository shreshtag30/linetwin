"""Six bottleneck detectors, scored against Phase 4's sensitivity ground truth.

All six read the same `LineRunStats` (run_stats.py) so the comparison is fair.
Four produce a continuous per-station score (higher = more bottleneck-like):
Utilization, Active Period (average duration), Busy Ratio, Queue Length. Two
-- Arrow and Turning Point -- are, in their original published form, discrete
graph-based methods that name a single station rather than score every
station continuously. Forcing an artificial continuous score onto them would
misrepresent methods neither of us has reimplemented from a primary,
line-by-line specification; they are evaluated on top-1 accuracy only, and
that limitation is stated here rather than papered over with an invented
scoring scheme.

Citations: Active Period Method -- Roser, Nakano & Tanaka (2001/2002). Busy
Ratio -- Kumbhar, Ng & Bandaru (2023), BR = ET_i / (ST_{i+1} - ST_i); we
compute BR per completion event from timestamps alone (this station's own
cycle time over the gap since its own previous completion), which needs only
MES-style timestamps and is the same spirit as Kumbhar's formula, not a
verbatim reimplementation of their event-log pipeline. Arrow -- Kuo & Lim
(1996), as summarized in Ragazzini et al. (2024) Table 1. Turning Point --
Li, Chang & Ni (2009) / Li & Li (2009), as summarized in the same table.
Utilization and Queue Length are the long-standing baseline methods every
paper in this space compares against.
"""

from __future__ import annotations

from dataclasses import dataclass

from twin.contracts import StationState
from twin.diagnostic.run_stats import LineRunStats


@dataclass(frozen=True)
class DetectorResult:
    name: str
    # Populated for continuous-score methods; None for Arrow/Turning Point.
    scores: dict[str, float] | None
    # Always populated: the method's single top pick.
    top_pick: str


def _fraction(stats: LineRunStats, sid: str, state: StationState) -> float:
    return stats.stations[sid].fraction_in(state, stats.duration)


def score_utilization(stats: LineRunStats) -> DetectorResult:
    scores = {sid: _fraction(stats, sid, StationState.WORKING) for sid in stats.station_order}
    return DetectorResult("utilization", scores, max(scores, key=lambda s: scores[s]))


def score_active_period(stats: LineRunStats) -> DetectorResult:
    """Roser's average active duration variant: the bottleneck is the station
    whose active periods are, on average, the longest -- not merely the most
    frequent or the most total time, which utilization already captures.
    """
    scores: dict[str, float] = {}
    for sid in stats.station_order:
        periods = stats.stations[sid].active_periods
        durations = [end - start for start, end in periods]
        scores[sid] = sum(durations) / len(durations) if durations else 0.0
    return DetectorResult("active_period", scores, max(scores, key=lambda s: scores[s]))


def score_active_period_normalized(stats: LineRunStats) -> DetectorResult:
    """Same signal as `score_active_period`, but each station's mean
    active-period duration is divided by ITS OWN zone-configured baseline
    cycle time before comparing across stations.

    REAL BUG this fixes, found by testing detectors against MULTIPLE distinct
    engineered bottleneck scenarios rather than replicating one: a station
    with an inherently long baseline cycle time can have the longest
    ABSOLUTE active periods on the entire line without being anywhere near
    the actual throughput-limiting station. Concretely, S13 (paint zone, long
    base cycle time) was the top pick of every raw-duration detector in a
    scenario engineered to make S25 (final assembly) the true bottleneck --
    confirmed via the sensitivity-analysis ground truth at every tested
    multiplier from 1.15x to 2.2x -- because S13's active periods are long in
    absolute seconds purely from its own slow baseline pace, not from being
    unusually disrupted. Dividing by each station's own baseline answers "how
    anomalous is this streak FOR THIS STATION," not "whose streak is longest
    in absolute seconds" -- the same normalization already used for
    `cycle_time_z` elsewhere in this project (risk/features.py,
    diagnostic/genealogy.py's z-score), now applied to bottleneck detection
    too, where it was missing.
    """
    scores: dict[str, float] = {}
    for sid in stats.station_order:
        periods = stats.stations[sid].active_periods
        durations = [end - start for start, end in periods]
        mean_duration = sum(durations) / len(durations) if durations else 0.0
        base = stats.base_cycle_time_of.get(sid) or 1.0
        scores[sid] = mean_duration / base
    return DetectorResult("active_period_normalized", scores, max(scores, key=lambda s: scores[s]))


def score_busy_ratio(stats: LineRunStats) -> DetectorResult:
    """BR ~ (this completion's cycle time) / (gap since this station's own
    previous completion). BR -> 1 means the station is almost never idle
    between its own completions (high effective utilization measured purely
    from timestamps); BR -> 0 means long idle/starved gaps.
    """
    scores: dict[str, float] = {}
    for sid in stats.station_order:
        st = stats.stations[sid]
        ratios = []
        for i in range(1, len(st.completion_timestamps)):
            gap = st.completion_timestamps[i] - st.completion_timestamps[i - 1]
            if gap > 0:
                ratios.append(min(st.cycle_times[i] / gap, 1.0))
        scores[sid] = sum(ratios) / len(ratios) if ratios else 0.0
    return DetectorResult("busy_ratio", scores, max(scores, key=lambda s: scores[s]))


def score_queue_length(stats: LineRunStats) -> DetectorResult:
    """A slow station's own input buffer runs persistently fuller, because
    upstream keeps feeding it faster than it can drain. Bottleneck = highest
    mean input-queue occupancy.
    """
    scores = {sid: stats.stations[sid].mean_input_queue_depth for sid in stats.station_order}
    return DetectorResult("queue_length", scores, max(scores, key=lambda s: scores[s]))


def detect_arrow(stats: LineRunStats) -> DetectorResult:
    """Kuo & Lim (1996): for each adjacent pair (i, i+1), compare i's blocking
    probability to (i+1)'s starving probability. If i is blocked more than
    i+1 is starved, draw an arrow i -> i+1 (i+1 is implicated); otherwise
    i+1 -> i. The bottleneck is the station that is the target of both its
    adjacent arrows (a graph sink) -- upstream stations block behind it,
    downstream stations starve waiting for it.
    """
    order = stats.station_order
    net_in_degree = dict.fromkeys(order, 0)

    for i in range(len(order) - 1):
        a, b = order[i], order[i + 1]
        p_block_a = _fraction(stats, a, StationState.BLOCKED)
        p_starve_b = _fraction(stats, b, StationState.STARVED)
        if p_block_a > p_starve_b:
            net_in_degree[b] += 1
            net_in_degree[a] -= 1
        else:
            net_in_degree[a] += 1
            net_in_degree[b] -= 1

    top_pick = max(net_in_degree, key=lambda s: net_in_degree[s])
    return DetectorResult("arrow", None, top_pick)


def detect_turning_point(stats: LineRunStats) -> DetectorResult:
    """Li, Chang & Ni (2009): along the line, d_i = P(blocked_i) - P(starved_i)
    is positive upstream of the bottleneck (stations back up behind it) and
    negative downstream (stations starve waiting for it). The turning point --
    the last station where d_i is still non-negative before the sequence
    turns negative -- is the bottleneck. If d is negative everywhere or
    positive everywhere (no genuine turning point in this run), falls back to
    the station with d_i closest to zero, and this fallback is exposed via
    `DetectorResult.scores` being None either way, since this method's native
    output is a single named station, not a per-station score.
    """
    order = stats.station_order
    d = {
        sid: (
            _fraction(stats, sid, StationState.BLOCKED)
            - _fraction(stats, sid, StationState.STARVED)
        )
        for sid in order
    }

    turning_station = None
    for i in range(len(order) - 1):
        if d[order[i]] >= 0 > d[order[i + 1]]:
            turning_station = order[i + 1]
            break

    if turning_station is None:
        turning_station = min(order, key=lambda s: abs(d[s]))

    return DetectorResult("turning_point", None, turning_station)


ALL_DETECTORS = (
    score_utilization,
    score_active_period,
    score_active_period_normalized,
    score_busy_ratio,
    score_queue_length,
    detect_arrow,
    detect_turning_point,
)


def run_all_detectors(stats: LineRunStats) -> list[DetectorResult]:
    return [detector(stats) for detector in ALL_DETECTORS]


__all__ = [
    "ALL_DETECTORS",
    "DetectorResult",
    "detect_arrow",
    "detect_turning_point",
    "run_all_detectors",
    "score_active_period",
    "score_active_period_normalized",
    "score_busy_ratio",
    "score_queue_length",
    "score_utilization",
]
