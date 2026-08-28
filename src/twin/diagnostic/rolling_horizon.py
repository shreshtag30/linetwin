"""Rolling-horizon bottleneck prediction: fork the live line's observable
state, run the fork forward, diagnose the forecast. Genuinely underexplored
territory -- Ragazzini et al. (2024) note only ~2 prior works do DT-based
bottleneck *prediction* rather than detection (docs/CITATIONS.md).

Stated limitation, not an oversight: this is NOT a literal deterministic
replay of the live simulation's exact future. Python cannot deep-copy a
running `simpy.Process` (it wraps a generator, and generator frames are not
copyable), so the fork cannot inherit the live line's exact in-flight
generator state -- which unit is how far into its current cycle, precisely.
What DOES carry into the fork, because it is what actually drives near-term
bottleneck behavior: each station's current state, mode-decomposition
history, active-period history, live perturbation multipliers, and current
queue depth (seeded with placeholder units, not the exact real ones). The
forecast is therefore "what happens if the current configuration and
congestion level continue," run forward on a fresh, independently-seeded
RNG stream -- a genuine Monte Carlo projection under current conditions, not
a claim of exact future replay.
"""

from __future__ import annotations

import copy

import simpy

from twin.contracts import BottleneckVerdict
from twin.diagnostic.bottleneck import StationView, diagnose
from twin.sim.line import Line, _Part


def fork_and_predict(
    line: Line, horizon_s: float, *, fork_seed: int = 999_983
) -> BottleneckVerdict:
    """Forks `line`'s observable state forward `horizon_s` sim-seconds and
    returns the diagnosed bottleneck of the forecast, not the live line.
    """
    config = copy.deepcopy(line.config)
    config.seed = fork_seed  # independent stream for the forecast branch -- see module docstring

    fork_env = simpy.Environment(initial_time=line.env.now)
    fork_line = Line(fork_env, config)
    fork_line._live_multiplier = dict(line._live_multiplier)

    for sid, station in line.stations.items():
        fork_station = fork_line.stations[sid]
        fork_station.state = station.state
        fork_station.time_in_state = dict(station.time_in_state)
        fork_station.active_periods = list(station.active_periods)
        fork_station._active_period_start = station._active_period_start
        fork_station._state_since = fork_env.now
        fork_station.units_completed = station.units_completed
        fork_station.last_cycle_time_s = station.last_cycle_time_s

        # Seed queue occupancy with placeholder units so congestion carries
        # into the forecast -- the part of "now" that actually matters for
        # near-term bottleneck behavior. Not the real queued units (their
        # exact in-flight state cannot be forked, see module docstring).
        #
        # Seeding `.items` directly (rather than through `Store.put()`) is
        # safe ONLY because this runs before `fork_env.run()` starts any
        # station's process -- simpy's `Store._do_get` just checks `if self.
        # items` synchronously when a `get()` is actually processed, with no
        # requirement that items arrived via a `put()` event. Verified
        # against simpy's own source before relying on it, not assumed.
        #
        # REAL BUG, found by actually running this: a placeholder unit can
        # complete its cycle DURING the forecast and flow through
        # Line.make_on_departure like any other unit, which builds a
        # UnitEvent -- and UnitEvent.unit_id requires `ge=0` (contracts.py).
        # Negative placeholder ids (this module's first draft used -1, -2,
        # ...) raise a pydantic ValidationError the instant one finishes.
        # Fixed with a large, non-negative offset unlikely to collide with
        # any real unit_id from the live line.
        depth = len(station.in_buf.items)
        fork_station.in_buf.items.clear()
        for i in range(depth):
            fork_station.in_buf.items.append(_Part(unit_id=10_000_000 + i, variant="sedan"))

    fork_env.run(until=fork_env.now + horizon_s)

    views = [StationView.from_station(fork_line.stations[sid]) for sid in config.station_ids]
    return diagnose(views)


__all__ = ["fork_and_predict"]
