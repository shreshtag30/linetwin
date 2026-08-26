"""Phase 3 exit gate, Bug B: active periods must not be truncated.

Tests the state-tracking logic in isolation, driving `Station._set_state`
directly (`auto_start=False` -- no `run()` process is started) rather than
waiting for a real breakdown to occur, since breakdowns/MTBF are explicitly out
of scope for this build (docs/DECISIONS.md). The classification itself --
DOWN is ACTIVE, STARVED is not -- is pinned separately in
tests/test_fixture_matches_contract.py; this file tests that the station's
bookkeeping actually USES that classification correctly when merging periods.
"""

from __future__ import annotations

import simpy

from twin.contracts import StationState, Zone
from twin.sim.station import Station


def _make_station(env: simpy.Environment, **kwargs: object) -> Station:
    return Station(
        env,
        "X",
        Zone.BODY,
        in_buf=simpy.Store(env, capacity=1),
        out_buf=None,
        cycle_time_sampler=lambda _part: 1.0,
        instrumented=True,
        auto_start=False,
        **kwargs,
    )


def test_working_down_working_is_one_active_period() -> None:
    """A breakdown does NOT end an active period -- the single most
    counterintuitive, load-bearing fact in the whole diagnostic layer.
    """
    env = simpy.Environment()
    station = _make_station(env)

    station._set_state(StationState.WORKING)
    env.run(until=10)
    station._set_state(StationState.DOWN)
    env.run(until=15)
    station._set_state(StationState.WORKING)
    env.run(until=20)
    station._set_state(StationState.IDLE)  # closes the period

    assert station.active_periods == [(0, 20)], (
        "WORKING -> DOWN -> WORKING must merge into ONE active period, not two"
    )


def test_working_starved_working_is_two_active_periods() -> None:
    env = simpy.Environment()
    station = _make_station(env)

    station._set_state(StationState.WORKING)
    env.run(until=10)
    station._set_state(StationState.STARVED)  # closes period 1: (0, 10)
    env.run(until=15)
    station._set_state(StationState.WORKING)  # opens period 2
    env.run(until=20)
    station._set_state(StationState.IDLE)  # closes period 2: (15, 20)

    assert station.active_periods == [(0, 10), (15, 20)], (
        "STARVED must end an active period, unlike DOWN"
    )


def test_bug_b_reproduction_incorrectly_splits_the_down_transition() -> None:
    """Prove the previous test's distinction is load-bearing: with the guard
    removed, WORKING -> DOWN -> WORKING is incorrectly split into two periods,
    silently degrading APM into the plain utilization method.
    """
    env = simpy.Environment()
    station = _make_station(env, _unsafe_close_period_on_every_transition=True)

    station._set_state(StationState.WORKING)
    env.run(until=10)
    station._set_state(StationState.DOWN)
    env.run(until=15)
    station._set_state(StationState.WORKING)
    env.run(until=20)
    station._set_state(StationState.IDLE)

    assert station.active_periods == [(0, 10), (10, 15), (15, 20)], (
        "with the guard removed, EVERY transition closes the period -- not just "
        "the DOWN one -- truncating what should be one 20s active period into "
        "three separate single-state fragments. If it doesn't, this reproduction "
        "isn't exercising Bug B."
    )


def test_bug_b_guard_is_load_bearing() -> None:
    """The acceptance criterion itself: the safe path merges DOWN into one
    period; the unsafe path splits it into two. If both produced the same
    result, the guard would not be proven to matter.
    """

    def run_sequence(**kwargs: object) -> list[tuple[float, float]]:
        env = simpy.Environment()
        station = _make_station(env, **kwargs)
        station._set_state(StationState.WORKING)
        env.run(until=10)
        station._set_state(StationState.DOWN)
        env.run(until=15)
        station._set_state(StationState.WORKING)
        env.run(until=20)
        station._set_state(StationState.IDLE)
        return station.active_periods

    safe = run_sequence()
    unsafe = run_sequence(_unsafe_close_period_on_every_transition=True)

    assert safe == [(0, 20)]
    assert unsafe == [(0, 10), (10, 15), (15, 20)]
    assert safe != unsafe, (
        "removing the active-period guard must change the observable result -- "
        "if it doesn't, the guard (and these tests) prove nothing"
    )


def test_time_in_state_accounts_for_every_second() -> None:
    """A cross-check independent of active-period logic: whatever states a
    station passes through, the total accounted time must equal wall time.
    """
    env = simpy.Environment()
    station = _make_station(env)

    station._set_state(StationState.WORKING)
    env.run(until=7)
    station._set_state(StationState.BLOCKED)
    env.run(until=12)
    station._set_state(StationState.WORKING)
    env.run(until=30)
    # Force the final segment's time into the ledger for the check below.
    station._set_state(StationState.IDLE)

    total = sum(station.time_in_state.values())
    assert total == 30, f"expected all 30s accounted for, got {total}"
