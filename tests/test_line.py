"""Integration test for the full 30-station configured line.

Complements test_cascade.py (which tests the Station primitive in isolation on
a minimal 3-station chain) by proving the config-driven assembly in line.py
wires 30 real stations together correctly, that the designated bottleneck
actually backs up its immediate upstream neighbour, and that the per-unit
event log conforms to the frozen contract.

LESSON CARRIED FROM BUILDING line.py MANUALLY BEFORE THIS TEST EXISTED: an
early ad-hoc run at 500 sim-seconds showed station S17 with zero completions,
which looked like a deadlock. It wasn't -- with body-zone cycle times around
45s and 16 stations upstream of S17, simple pipeline fill time is already
~700s before S17 sees its first unit. This test uses a long enough duration
that fill time cannot be mistaken for a bug, matching the same trap documented
in test_cascade.py.
"""

from __future__ import annotations

from pathlib import Path

import simpy

from twin.contracts import StationState, UnitEvent
from twin.sim.line import LineConfig, build_line

SCENARIO = Path(__file__).resolve().parents[1] / "scenarios" / "line30.yaml"
DURATION_S = 6000.0  # long enough for flow to reach and back up past S17


def test_scenario_loads_with_the_committed_topology() -> None:
    config = LineConfig.from_yaml(SCENARIO)
    assert len(config.station_ids) == 30
    assert config.station_ids[0] == "S01"
    assert config.station_ids[-1] == "S30"
    assert len(config.dark_stations) == 8
    assert len(config.instrumented_stations) == 22
    assert config.bottleneck_station_id == "S17"


def test_bottleneck_backs_up_into_its_immediate_upstream_neighbour() -> None:
    env = simpy.Environment()
    line = build_line(env, SCENARIO)
    env.run(until=DURATION_S)
    for station in line.stations.values():
        station.finalize_active_period()

    upstream = line.stations["S16"]
    bottleneck = line.stations["S17"]

    assert upstream.time_in_state.get(StationState.BLOCKED, 0.0) > 0, (
        "S16 must spend real time BLOCKED behind the configured bottleneck S17"
    )
    assert bottleneck.units_completed > 0, "the bottleneck itself must still be making progress"


def test_flow_reaches_the_last_station() -> None:
    env = simpy.Environment()
    line = build_line(env, SCENARIO)
    env.run(until=DURATION_S)

    assert line.stations["S30"].units_completed > 0, (
        "units must reach the end of the line within the test duration"
    )


def test_dark_stations_match_the_scenario_config() -> None:
    env = simpy.Environment()
    line = build_line(env, SCENARIO)

    for sid, station in line.stations.items():
        expected_instrumented = sid not in line.config.dark_stations
        assert station.instrumented == expected_instrumented, sid


def test_event_log_entries_conform_to_the_frozen_contract() -> None:
    env = simpy.Environment()
    line = build_line(env, SCENARIO)
    env.run(until=1000.0)

    assert len(line.events) > 0
    for event in line.events[:200]:
        assert isinstance(event, UnitEvent)
        assert event.exited_at >= event.entered_at
        assert event.cycle_time_s > 0
        assert event.station_id in line.stations


def test_same_seed_reproduces_identical_event_log_different_seed_diverges() -> None:
    def run_with(scenario_path: Path, seed_override: int | None) -> list[float]:
        config = LineConfig.from_yaml(scenario_path)
        if seed_override is not None:
            config.seed = seed_override
        env = simpy.Environment()
        from twin.sim.line import Line

        line = Line(env, config)
        env.run(until=800.0)
        return [e.cycle_time_s for e in line.events]

    a1 = run_with(SCENARIO, None)
    a2 = run_with(SCENARIO, None)
    b = run_with(SCENARIO, 999_999)

    assert a1 == a2, "identical scenario config must reproduce the identical event log"
    assert a1 != b, "a different seed must diverge"
