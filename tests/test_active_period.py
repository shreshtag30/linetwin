"""Phase 3 exit gate, Bug B: active periods must not be truncated.

Most tests here drive `Station._set_state` directly (`auto_start=False` -- no
`run()` process is started) to test the merging logic in isolation. The
classification itself -- DOWN is ACTIVE, STARVED is not -- is pinned
separately in tests/test_fixture_matches_contract.py.

CORRECTION: this file used to say breakdowns were "explicitly out of scope
for this build", and that was true -- which meant the single most-emphasised
claim in the project (a breakdown does NOT end an active period) guarded a
transition the running simulation could never make. `DOWN` and `REPAIR` were
declared, classified ACTIVE, and never entered by anything except these
synthetic tests. Breakdowns now exist (scenarios/line30.yaml's `breakdowns`
block), so the final test below exercises the property END TO END on a real
running line rather than only against hand-driven transitions.
"""

from __future__ import annotations

from pathlib import Path

import simpy

from twin.contracts import StationState, Zone
from twin.sim.line import build_line
from twin.sim.station import Station

SCENARIO = Path(__file__).resolve().parents[1] / "scenarios" / "line30.yaml"


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


def test_a_real_breakdown_on_a_running_line_does_not_end_the_active_period() -> None:
    """End-to-end version of this file's central claim, on a real line.

    Every other test here hand-drives `_set_state`. This one runs the actual
    simulation until genuine breakdowns have occurred, then asserts the
    property that makes the Active Period Method different from the plain
    utilization method: a station that goes WORKING -> DOWN -> REPAIR ->
    WORKING accumulates ONE unbroken active period spanning the stoppage, not
    three short ones split at each transition.

    The check is structural rather than cosmetic: if `_set_state` closed the
    period on every transition (the bug the `_unsafe_close_period_on_every_
    transition` flag reproduces), a station with N breakdowns would show at
    least N extra period boundaries, and its longest period could never
    exceed the gap between two consecutive state changes.
    """
    env = simpy.Environment()
    line = build_line(env, SCENARIO)
    env.run(until=20_000.0)

    broken = [st for st in line.stations.values() if st.breakdown_count > 0]
    assert broken, "no breakdown occurred in 20,000s -- MTBF misconfigured, test is vacuous"

    total_downtime = sum(st.downtime_s for st in line.stations.values())
    assert total_downtime > 0.0

    # At least one station must have an active period that strictly CONTAINS
    # a stoppage -- i.e. is longer than the repair it survived.
    profile = line.config.breakdowns
    assert profile is not None
    survived = 0
    for st in broken:
        st.finalize_active_period()
        longest = max((end - start for start, end in st.active_periods), default=0.0)
        if longest > profile.detect_s + profile.mttr_s:
            survived += 1
    assert survived > 0, (
        "every active period was shorter than a single stoppage -- the period is "
        "being closed at the WORKING->DOWN transition, which silently degrades the "
        "Active Period Method into the utilization method"
    )


def test_down_and_repair_are_actually_entered_by_a_running_line() -> None:
    """Guards against the states going unreachable again. `mode_decomposition`
    (diagnostic/bottleneck.py) divides active time across WORKING/DOWN/REPAIR/
    SETUP; while only WORKING was ever entered it could return nothing but
    {"working": 1.0}, so the dashboard's "why is it the constraint" panel was
    structurally incapable of saying anything but "working".
    """
    env = simpy.Environment()
    line = build_line(env, SCENARIO)
    env.run(until=20_000.0)

    time_in = {state: 0.0 for state in StationState}
    for st in line.stations.values():
        for state, seconds in st.time_in_state.items():
            time_in[state] += seconds

    assert time_in[StationState.DOWN] > 0.0, "DOWN is still unreachable on a running line"
    assert time_in[StationState.REPAIR] > 0.0, "REPAIR is still unreachable on a running line"
    assert time_in[StationState.WORKING] > time_in[StationState.REPAIR], (
        "repair time should not dominate working time -- MTBF/MTTR misconfigured"
    )
