"""The merged station pattern -- the highest-value code in this project.

Two independent bugs, neither raising an error, each silently invalidating a
core claim of the whole system. Both are guarded here, and both guards have a
dedicated regression test (tests/test_cascade.py, tests/test_active_period.py)
that is REQUIRED to fail red if its guard is removed -- that requirement is
enforced by the `_unsafe_*` constructor flags below, which exist ONLY so the
test suite can prove a negative, and must never be set outside a test.

Bug A -- put-while-occupied.
    The downstream `put` must be issued immediately after the cycle-time
    timeout completes, while the station is still conceptually holding the
    unit -- not after some other state transition "frees" the station first.
    Get this order wrong (e.g. by refactoring the put into a fire-and-forget
    background task) and a slowdown downstream never floods the upstream
    station, because the station has already gone looking for its next unit.
    The entire cascade thesis -- that a bottleneck visibly backs up the line --
    fails silently: everything still runs, throughput numbers still come out,
    they are just wrong.

Bug B -- the `.triggered` guard.
    STARVED/BLOCKED must be set only when the corresponding buffer operation
    did NOT resolve immediately. SimPy can satisfy a `Store.get()` or
    `Store.put()` in the same timestep it was issued, if the item/capacity was
    already available. Setting the state unconditionally on every get/put --
    even ones that resolved instantly -- truncates every active period to a
    single cycle time, which silently degrades the Active Period Method into
    the plain utilization method. No crash. All statistical rigor gone.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
import simpy

from twin.contracts import StationState, Zone

# How many recent completions `mean_recent_cycle_time_s` averages over.
# A single `last_cycle_time_s` is one lognormal draw and is mostly
# irreducible per-unit noise; a real OT tap or historian read would
# aggregate over a short window before anything downstream consumed it.
# The graph layer (graph/inference.py) reads the smoothed value, because
# what it can legitimately recover is a station's cycle-time TREND, not
# the duration of one specific unit -- which is exactly what this project
# already claims for it (docs/LIMITATIONS.md).
# 30 was chosen by measurement, not taste: the recoverable signal is the shared
# drift (sigma 0.08) and the noise floor is this station's own per-unit cv
# (0.12-0.28) divided by sqrt(window). At window=10 the graph layer beat the
# zone-base estimator by 18%; at 30, by 27%.
RECENT_CYCLE_WINDOW = 30


@dataclass(frozen=True)
class BreakdownProfile:
    """Unplanned-stoppage parameters for one station.

    `synthetic -- uncalibrated`, same discipline as every other timing
    parameter here (docs/CITATIONS.md). What would calibrate it: the
    plant's own MTBF/MTTR per station class from its CMMS work-order
    history, which this project does not have.

    Time-to-failure is measured in PRODUCTIVE seconds (a machine does not
    wear while it is starved), and is exponential -- so by the memoryless
    property, resampling the residual at the start of each work chunk is
    exactly equivalent to carrying one continuous failure clock. That
    equivalence is why `_work` below can sample per chunk without
    introducing a bias.
    """

    mtbf_productive_s: float
    detect_s: float
    mttr_s: float


class Station:
    """One station in the line. Owns an input Store, an output Store, and the
    per-station state-time bookkeeping the Active Period Method reads.
    """

    def __init__(
        self,
        env: simpy.Environment,
        station_id: str,
        zone: Zone,
        in_buf: simpy.Store,
        out_buf: simpy.Store | None,
        cycle_time_sampler: Callable[[object], float],
        instrumented: bool,
        *,
        auto_start: bool = True,
        on_departure: Callable[[object, float, float, float], None] | None = None,
        breakdowns: BreakdownProfile | None = None,
        failure_rng: np.random.Generator | None = None,
        _unsafe_fire_and_forget_put: bool = False,
        _unsafe_unconditional_state_set: bool = False,
        _unsafe_close_period_on_every_transition: bool = False,
    ) -> None:
        self.env = env
        self.station_id = station_id
        self.zone = zone
        self.in_buf = in_buf
        self.out_buf = out_buf  # None for the last station: units simply vanish (sink)
        self.cycle_time_sampler = cycle_time_sampler
        self.instrumented = instrumented
        # Both None (the default) disables breakdowns entirely, which is what
        # every pre-existing test that constructs a bare Station relies on.
        self.breakdowns = breakdowns
        self._failure_rng = failure_rng
        # Called (part, entered_at, exited_at, cycle_time_s) once per completed
        # cycle, before the downstream put is attempted. line.py uses this to
        # build the per-unit UnitEvent log that Phase 9's genealogy walks.
        self.on_departure = on_departure

        # Regression-test-only escape hatches. Both default to False (safe).
        # Flipping either to True reproduces exactly the bug its guard prevents,
        # which is how tests/test_cascade.py and tests/test_active_period.py
        # prove they actually test something.
        self._unsafe_fire_and_forget_put = _unsafe_fire_and_forget_put
        self._unsafe_unconditional_state_set = _unsafe_unconditional_state_set
        self._unsafe_close_period_on_every_transition = _unsafe_close_period_on_every_transition

        self.state: StationState = StationState.IDLE
        self._state_since: float = env.now
        self.time_in_state: dict[StationState, float] = dict.fromkeys(StationState, 0.0)

        # Active-period bookkeeping, read by Phase 5's Active Period Method.
        self._active_period_start: float | None = None
        self.active_periods: list[tuple[float, float]] = []

        self.units_completed: int = 0
        self.last_cycle_time_s: float | None = None
        # Recent completed cycle times, newest last. See RECENT_CYCLE_WINDOW.
        self.recent_cycle_times: deque[float] = deque(maxlen=RECENT_CYCLE_WINDOW)
        # Unplanned stoppages that actually occurred, for reporting.
        self.breakdown_count: int = 0
        self.downtime_s: float = 0.0

        self.process: simpy.Process | None = None
        if auto_start:
            self.process = env.process(self.run())

    # ------------------------------------------------------------------
    # State machine
    # ------------------------------------------------------------------

    def _set_state(self, new_state: StationState) -> None:
        """Record time spent in the outgoing state and manage active-period
        boundaries. A transition between two ACTIVE states (e.g. WORKING ->
        DOWN) must NOT close the active period -- this is the counterintuitive,
        load-bearing fact behind the whole Active Period Method.
        """
        now = self.env.now
        elapsed = now - self._state_since
        self.time_in_state[self.state] = self.time_in_state.get(self.state, 0.0) + elapsed

        was_active = self.state.is_active
        is_active = new_state.is_active

        if self._unsafe_close_period_on_every_transition:
            # Reproduces the active-period bug on purpose: closes the period on
            # EVERY transition rather than only on ACTIVE -> INACTIVE, which
            # truncates every active period to a single state's duration and
            # silently degrades APM into the plain utilization method. Only
            # ever set True by tests/test_active_period.py, to prove it goes
            # red without this guard.
            if self._active_period_start is not None:
                self.active_periods.append((self._active_period_start, now))
            self._active_period_start = now if is_active else None
        elif is_active and not was_active:
            self._active_period_start = now
        elif was_active and not is_active:
            if self._active_period_start is not None:
                self.active_periods.append((self._active_period_start, now))
            self._active_period_start = None
        # was_active and is_active (e.g. WORKING -> DOWN): period continues,
        # nothing to do here -- this is the counterintuitive, load-bearing case
        # the guard above protects.

        self.state = new_state
        self._state_since = now

    @property
    def active_period_start(self) -> float | None:
        """Sim time the current in-progress active period began, or None if
        the station is not currently active. Public so the diagnostic layer's
        momentary Active Period Method (diagnostic/bottleneck.py) can read it
        without reaching into a private attribute.
        """
        return self._active_period_start

    def finalize_active_period(self) -> None:
        """Close any in-progress active period at the current sim time, so a
        test or metric computed mid-active-period doesn't undercount it.
        Idempotent-ish: safe to call once at the point you want to measure.
        """
        if self._active_period_start is not None:
            self.active_periods.append((self._active_period_start, self.env.now))
            self._active_period_start = None

    # ------------------------------------------------------------------
    # The process
    # ------------------------------------------------------------------

    def run(self):
        while True:
            req = self.in_buf.get()

            # --- Bug B guard, get-side ---
            # Only STARVED if the request genuinely had to wait.
            if self._unsafe_unconditional_state_set or not req.triggered:
                self._set_state(StationState.STARVED)
            part = yield req
            entered_at = self.env.now

            self._set_state(StationState.WORKING)
            cycle_time = self.cycle_time_sampler(part)
            self.last_cycle_time_s = cycle_time
            self.recent_cycle_times.append(cycle_time)
            # Consumes `cycle_time` of PRODUCTIVE time, interleaved with any
            # unplanned stoppage that fires during it. Unchanged when
            # breakdowns are disabled: a single timeout, exactly as before.
            yield from self._work(cycle_time)
            self.units_completed += 1
            exited_at = self.env.now

            if self.on_departure is not None:
                self.on_departure(part, entered_at, exited_at, cycle_time)

            if self.out_buf is None:
                # Sink station: nothing downstream to block on.
                continue

            if self._unsafe_fire_and_forget_put:
                # --- Bug A, reproduced on purpose ---
                # Fire the put in the background and immediately go looking for
                # the next unit. The station now behaves as if it had infinite
                # capacity: a slowdown downstream never backs up into it.
                self.env.process(self._deferred_put(part))
                continue

            # --- Bug A guard ---
            # Issue the put HERE, while this unit is still occupying the
            # station, and wait for it before looping back for the next get().
            put = self.out_buf.put(part)

            # --- Bug B guard, put-side ---
            if self._unsafe_unconditional_state_set or not put.triggered:
                self._set_state(StationState.BLOCKED)
            yield put

    def _work(self, cycle_time: float):
        """Spend `cycle_time` of PRODUCTIVE time on the current unit, pausing
        for any unplanned stoppage that fires along the way.

        The state walk is WORKING -> DOWN (fault present, not yet being
        worked) -> REPAIR -> WORKING. All three are ACTIVE
        (contracts.StationState), so `_set_state` deliberately does NOT close
        the active period across them -- which is the counterintuitive,
        load-bearing property the Active Period Method depends on, and which
        before this existed was reachable only from a synthetic unit test.
        `mode_decomposition` (diagnostic/bottleneck.py) is what finally makes
        that visible: a station can now be constraint-by-downtime rather than
        constraint-by-slow-work, and the verdict says which.

        Note `last_cycle_time_s` remains PRODUCTIVE time, not wall-clock
        occupancy -- it is what a per-unit cycle-time sensor would report.
        Downtime is carried by the active-period and time_in_state
        bookkeeping instead, so the two are never conflated.
        """
        if self.breakdowns is None or self._failure_rng is None:
            yield self.env.timeout(cycle_time)
            return

        profile = self.breakdowns
        remaining = cycle_time
        while remaining > 1e-9:
            # Exponential residual time-to-failure. Memorylessness makes
            # resampling per chunk identical to one continuous failure clock
            # -- see BreakdownProfile's docstring.
            time_to_failure = float(self._failure_rng.exponential(profile.mtbf_productive_s))
            if time_to_failure >= remaining:
                yield self.env.timeout(remaining)
                return

            yield self.env.timeout(time_to_failure)
            remaining -= time_to_failure

            down_at = self.env.now
            self._set_state(StationState.DOWN)
            yield self.env.timeout(profile.detect_s)
            self._set_state(StationState.REPAIR)
            yield self.env.timeout(float(self._failure_rng.exponential(profile.mttr_s)))
            self._set_state(StationState.WORKING)

            self.breakdown_count += 1
            self.downtime_s += self.env.now - down_at

    @property
    def mean_recent_cycle_time_s(self) -> float | None:
        """Mean of the last `RECENT_CYCLE_WINDOW` completions, or None before
        this station has completed anything. This -- not the single most
        recent draw -- is what the sensor-gap inference layer reads; see
        RECENT_CYCLE_WINDOW for why.
        """
        if not self.recent_cycle_times:
            return None
        return sum(self.recent_cycle_times) / len(self.recent_cycle_times)

    def _deferred_put(self, part: object):
        """Only reachable via _unsafe_fire_and_forget_put; exists so the
        regression test can construct Bug A without duplicating the put logic.
        """
        yield self.out_buf.put(part)


__all__ = ["RECENT_CYCLE_WINDOW", "BreakdownProfile", "Station"]
