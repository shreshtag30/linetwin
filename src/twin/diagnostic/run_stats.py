"""Shared statistics substrate for all six bottleneck detectors.

Every detector reads from the same `LineRunStats`, so comparing them is a fair
comparison of *methods*, not of who got fed richer data. Queue-depth sampling
is the one thing not already produced by Station/Line -- added here via an
external monitor process rather than by modifying Station, since it is needed
by exactly one detector (Queue Length) and has no business living in the
simulation core.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field

import simpy

from twin.contracts import StationState
from twin.sim.line import Line, LineConfig

DEFAULT_QUEUE_SAMPLE_DT = 10.0


@dataclass(frozen=True)
class StationRunStats:
    station_id: str
    time_in_state: dict[StationState, float]
    active_periods: list[tuple[float, float]]
    mean_input_queue_depth: float
    # Parallel arrays: this station's own completions, sorted by time.
    completion_timestamps: list[float] = field(repr=False)
    cycle_times: list[float] = field(repr=False)

    def fraction_in(self, state: StationState, duration: float) -> float:
        return self.time_in_state.get(state, 0.0) / duration if duration else 0.0


@dataclass(frozen=True)
class LineRunStats:
    duration: float
    station_order: list[str]
    stations: dict[str, StationRunStats]
    # Each station's zone-configured, UNPERTURBED base cycle time -- needed to
    # normalize a station's active-period duration by its own baseline pace
    # (score_active_period_normalized, detectors.py). Deliberately the STATIC
    # config value, not this run's own observed mean cycle time: a station's
    # observed cycle time already reflects any live perturbation applied to
    # IT, so normalizing by that would divide out the exact slowdown signal
    # detection is supposed to find.
    base_cycle_time_of: dict[str, float]


def run_for_analysis(
    config: LineConfig,
    seed: int,
    duration: float,
    *,
    queue_sample_dt: float = DEFAULT_QUEUE_SAMPLE_DT,
) -> LineRunStats:
    """Runs one simulation and collects everything the six detectors need."""
    run_config = copy.deepcopy(config)
    run_config.seed = seed

    env = simpy.Environment()
    line = Line(env, run_config)

    queue_sum = dict.fromkeys(run_config.station_ids, 0.0)
    queue_n = dict.fromkeys(run_config.station_ids, 0)

    def _queue_monitor():
        while True:
            for sid in run_config.station_ids:
                queue_sum[sid] += len(line.stations[sid].in_buf.items)
                queue_n[sid] += 1
            yield env.timeout(queue_sample_dt)

    env.process(_queue_monitor())
    env.run(until=duration)

    for station in line.stations.values():
        station.finalize_active_period()

    completions: dict[str, list[tuple[float, float]]] = {sid: [] for sid in run_config.station_ids}
    for event in line.events:
        completions[event.station_id].append((event.exited_at, event.cycle_time_s))

    stations: dict[str, StationRunStats] = {}
    for sid in run_config.station_ids:
        comp = sorted(completions[sid])
        mean_q = (queue_sum[sid] / queue_n[sid]) if queue_n[sid] else 0.0
        stations[sid] = StationRunStats(
            station_id=sid,
            time_in_state=dict(line.stations[sid].time_in_state),
            active_periods=list(line.stations[sid].active_periods),
            mean_input_queue_depth=mean_q,
            completion_timestamps=[t for t, _ in comp],
            cycle_times=[c for _, c in comp],
        )

    return LineRunStats(
        duration=duration,
        station_order=run_config.station_ids,
        stations=stations,
        base_cycle_time_of=dict(run_config.base_cycle_time_of),
    )


__all__ = ["DEFAULT_QUEUE_SAMPLE_DT", "LineRunStats", "StationRunStats", "run_for_analysis"]
