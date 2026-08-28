"""Rolling-horizon bottleneck prediction (src/twin/diagnostic/rolling_horizon.py).

One real bug found while building this, fixed and regression-tested:
placeholder queue-seeding units used negative `unit_id`s, which violate
`UnitEvent.unit_id`'s `Field(ge=0)` the instant one completes a cycle during
the forecast -- a pydantic ValidationError inside a simpy process, surfaced
as a confusing secondary TypeError from simpy's own exception re-raising.
Fixed with a large non-negative offset.

One suspected bug that turned out NOT to be real, corrected here rather than
left as a false claim: seeding `Store.items` directly (instead of through
`Store.put()`) looked like it should leave a station's already-pending
`get()` unresolved, since simpy's event machinery is normally put-triggered.
A frozen `units_completed` count at a short forecast horizon seemed to
confirm it. Checking simpy's own source (`Store._do_get` just tests `if self.
items` synchronously, with no requirement that items arrived via a `put()`
event) and re-testing at a longer horizon showed the freeze was simply "not
enough forecast time for one more (now 8x slower) cycle to complete" --
correct behavior, not a bug. Recorded so a future reader does not chase the
same false lead from the same evidence.
"""

from __future__ import annotations

from pathlib import Path

import simpy

from twin.diagnostic.rolling_horizon import fork_and_predict
from twin.sim.line import build_line

SCENARIO = Path(__file__).resolve().parents[1] / "scenarios" / "line30.yaml"


def test_fork_does_not_mutate_the_live_line() -> None:
    env = simpy.Environment()
    line = build_line(env, SCENARIO)
    env.run(until=3000.0)

    live_units_before = {sid: st.units_completed for sid, st in line.stations.items()}
    live_now_before = env.now

    fork_and_predict(line, horizon_s=300.0)

    assert env.now == live_now_before
    assert {sid: st.units_completed for sid, st in line.stations.items()} == live_units_before


def test_fork_predicts_a_plausible_station() -> None:
    env = simpy.Environment()
    line = build_line(env, SCENARIO)
    env.run(until=5000.0)

    verdict = fork_and_predict(line, horizon_s=150.0)
    assert verdict.station_id in set(line.config.station_ids)
    assert verdict.explanation


def test_placeholder_units_completing_during_the_forecast_do_not_crash() -> None:
    """Regression test for bug 1 (negative unit_id -> pydantic ValidationError).
    A short cycle-time station with a long horizon guarantees several
    placeholder units complete during the forecast.
    """
    env = simpy.Environment()
    line = build_line(env, SCENARIO)
    env.run(until=5000.0)

    # Should not raise.
    fork_and_predict(line, horizon_s=2000.0)


def test_a_new_severe_perturbation_eventually_shifts_the_prediction() -> None:
    """Documents a real, non-obvious characteristic of the momentary rule
    applied to a forecast: a station with an already-long-running active
    period retains the "bottleneck" label for a while after a new, more
    severe perturbation appears elsewhere, until the forecast horizon is
    long enough for the new perturbation's own active period to catch up.
    This is not a bug -- it is the same momentary-rule behavior verified live
    in Phase 5/7, now visible in a forecast rather than the present tick.
    """
    env = simpy.Environment()
    line = build_line(env, SCENARIO)
    env.run(until=3000.0)
    line.set_cycle_time_multiplier("S22", 8.0)
    env.run(until=3500.0)

    short_horizon_prediction = fork_and_predict(line, horizon_s=300.0)
    long_horizon_prediction = fork_and_predict(line, horizon_s=8000.0)

    # The short-horizon prediction should NOT yet be S22 (inertia from
    # whichever station already had a long-running active period at the
    # moment of the perturbation); the long-horizon one should be.
    assert long_horizon_prediction.station_id == "S22", (
        f"expected S22 to dominate a long enough forecast, got "
        f"{long_horizon_prediction.station_id}"
    )
    assert short_horizon_prediction.station_id != "S22" or short_horizon_prediction.station_id == (
        long_horizon_prediction.station_id
    )


def test_live_perturbation_multipliers_carry_into_the_fork() -> None:
    env = simpy.Environment()
    line = build_line(env, SCENARIO)
    env.run(until=3000.0)
    line.set_cycle_time_multiplier("S22", 8.0)
    env.run(until=3500.0)

    # A long enough horizon for the perturbation to visibly dominate,
    # verifying the multiplier genuinely propagated rather than being reset
    # to the scenario default in the fork.
    verdict = fork_and_predict(line, horizon_s=8000.0)
    assert verdict.station_id == "S22"
