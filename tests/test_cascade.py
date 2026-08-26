"""Phase 3 exit gate, Bug A: the cascade must actually cascade.

A three-station chain, tight capacity-1 buffers, the last station slowed 10x.
If the merged station pattern is correct, the slowdown backs up through the
whole chain and the middle station spends real time BLOCKED. If Bug A is
present (the downstream put becomes fire-and-forget), the middle station never
notices the slowdown at all -- which is exactly what this test must catch.

LESSON FROM BUILDING THIS: an early manual run of the full 30-station line over
500 sim-seconds showed station 17 with zero completions and concluded a bug —
it wasn't one. With ~45-65s cycle times across up to 30 stations in series, the
pipeline fill time alone can exceed 500s before steady state is even reached.
This test runs long enough (2000s) for a 3-station chain that fill time is not
a confound, but anyone extending this to more stations must scale the duration
accordingly rather than assume a short window proves anything.
"""

from __future__ import annotations

import numpy as np
import pytest
import simpy

from twin.contracts import StationState, Zone
from twin.sim.station import Station

SIM_SECONDS = 2000.0
CAPACITY = 1
NORMAL_CYCLE = 10.0
SLOW_MULTIPLIER = 10.0


def _build_chain(
    env: simpy.Environment,
    *,
    unsafe_fire_and_forget_middle: bool = False,
) -> tuple[simpy.Store, Station, Station, Station]:
    """A -> B -> C, each buffer capacity 1. C runs 10x slower than A and B."""
    buf_ab = simpy.Store(env, capacity=CAPACITY)
    buf_bc = simpy.Store(env, capacity=CAPACITY)
    sink = simpy.Store(env, capacity=10_000)  # effectively unbounded sink

    station_a = Station(
        env, "A", Zone.BODY, in_buf=simpy.Store(env, capacity=10_000), out_buf=buf_ab,
        cycle_time_sampler=lambda: NORMAL_CYCLE, instrumented=True,
    )
    station_b = Station(
        env, "B", Zone.BODY, in_buf=buf_ab, out_buf=buf_bc,
        cycle_time_sampler=lambda: NORMAL_CYCLE, instrumented=True,
        _unsafe_fire_and_forget_put=unsafe_fire_and_forget_middle,
    )
    station_c = Station(
        env, "C", Zone.BODY, in_buf=buf_bc, out_buf=sink,
        cycle_time_sampler=lambda: NORMAL_CYCLE * SLOW_MULTIPLIER, instrumented=True,
    )

    # Keep A permanently fed so it is never the constraint.
    def feeder():
        while True:
            yield station_a.in_buf.put(object())

    env.process(feeder())
    return buf_ab, station_a, station_b, station_c


def test_slowdown_cascades_upstream_and_saturates_the_buffer() -> None:
    env = simpy.Environment()
    buf_ab, _station_a, station_b, _station_c = _build_chain(env)
    env.run(until=SIM_SECONDS)
    station_b.finalize_active_period()

    assert station_b.time_in_state.get(StationState.BLOCKED, 0.0) > 0, (
        "B must spend real time BLOCKED once C is 10x slower"
    )
    assert len(buf_ab.items) == CAPACITY, "the A->B buffer must saturate at capacity"


def test_bug_a_reproduction_breaks_the_cascade() -> None:
    """Prove the test above actually tests something: with Bug A reproduced
    (fire-and-forget put at B), B never blocks, because it goes looking for its
    next unit before confirming the previous one was actually accepted by C.
    """
    env = simpy.Environment()
    _buf_ab, _station_a, station_b, _station_c = _build_chain(
        env, unsafe_fire_and_forget_middle=True
    )
    env.run(until=SIM_SECONDS)
    station_b.finalize_active_period()

    assert station_b.time_in_state.get(StationState.BLOCKED, 0.0) == 0.0, (
        "with Bug A reproduced, B must NOT block -- if it does, this test's "
        "fire-and-forget reproduction is not actually exercising the bug"
    )


def test_bug_a_guard_is_load_bearing() -> None:
    """The acceptance criterion itself: the safe path blocks, the unsafe path
    does not. If both blocked (or neither did), the guard would not be proven
    to matter.
    """
    env_safe = simpy.Environment()
    _, _, b_safe, _ = _build_chain(env_safe)
    env_safe.run(until=SIM_SECONDS)
    b_safe.finalize_active_period()

    env_unsafe = simpy.Environment()
    _, _, b_unsafe, _ = _build_chain(env_unsafe, unsafe_fire_and_forget_middle=True)
    env_unsafe.run(until=SIM_SECONDS)
    b_unsafe.finalize_active_period()

    safe_blocked = b_safe.time_in_state.get(StationState.BLOCKED, 0.0)
    unsafe_blocked = b_unsafe.time_in_state.get(StationState.BLOCKED, 0.0)

    assert safe_blocked > 0.0
    assert unsafe_blocked == 0.0
    assert safe_blocked != unsafe_blocked, (
        "removing Bug A's guard must change the observable outcome -- "
        "if it doesn't, the guard (and this test) proves nothing"
    )


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_determinism_same_seed_identical_different_seed_differs(seed: int) -> None:
    """Doubles as the liveness proof: a script cannot vary its own output on a
    changed seed the way a genuine stochastic simulation does.

    An earlier version of this test recorded only `units_completed` (a single
    small integer) as the comparison signature. Over a short window that count
    is coarse enough that two genuinely different random streams can coincide
    on the same integer by chance -- which is exactly what happened (seed=2
    and seed=1002 both produced 9 completions), failing the "must diverge"
    assertion for a reason that had nothing to do with determinism being
    broken. Comparing the actual sampled cycle-time sequence instead gives the
    comparison enough resolution that a real difference cannot hide.
    """

    def run_once(s: int) -> list[float]:
        rng = np.random.default_rng(s)
        env = simpy.Environment()
        sampled: list[float] = []

        def sampler() -> float:
            value = float(rng.lognormal(3.0, 0.2))
            sampled.append(value)
            return value

        station = Station(
            env, "X", Zone.BODY,
            in_buf=simpy.Store(env, capacity=10_000),
            out_buf=None,
            cycle_time_sampler=sampler,
            instrumented=True,
        )

        def feeder():
            while True:
                yield station.in_buf.put(object())

        env.process(feeder())
        env.run(until=200.0)
        return sampled

    a1 = run_once(seed)
    a2 = run_once(seed)
    b = run_once(seed + 1000)

    assert a1 == a2, "same seed must reproduce the identical sampled sequence"
    assert a1 != b, "a different seed must diverge -- otherwise nothing is actually random"
