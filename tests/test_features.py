"""FeatureExtractor: the shared, non-invasive rolling-window sampler used
identically offline and online (src/twin/risk/features.py).
"""

from __future__ import annotations

from pathlib import Path

import simpy

from twin.risk.features import FEATURE_NAMES, FeatureExtractor
from twin.sim.line import Line, LineConfig

SCENARIO = Path(__file__).resolve().parents[1] / "scenarios" / "line30.yaml"


def test_features_for_returns_exactly_the_five_documented_features() -> None:
    config = LineConfig.from_yaml(SCENARIO)
    env = simpy.Environment()
    line = Line(env, config)
    env.run(until=1000.0)

    extractor = FeatureExtractor(config.station_ids, config.buffer_capacity_of, sample_dt=7.5)
    extractor.sample_tick(line.stations)

    feats = extractor.features_for("S05", line.stations["S05"], "S04")
    assert set(feats.keys()) == set(FEATURE_NAMES)


def test_first_station_has_no_upstream_and_reads_zero() -> None:
    config = LineConfig.from_yaml(SCENARIO)
    env = simpy.Environment()
    line = Line(env, config)
    env.run(until=1000.0)

    extractor = FeatureExtractor(config.station_ids, config.buffer_capacity_of, sample_dt=7.5)
    extractor.sample_tick(line.stations)

    feats = extractor.features_for("S01", line.stations["S01"], None)
    assert feats["upstream_risk_ewma"] == 0.0


def test_update_risk_ewma_is_reflected_in_the_next_stations_upstream_feature() -> None:
    config = LineConfig.from_yaml(SCENARIO)
    env = simpy.Environment()
    line = Line(env, config)
    env.run(until=1000.0)

    extractor = FeatureExtractor(config.station_ids, config.buffer_capacity_of, sample_dt=7.5)
    extractor.sample_tick(line.stations)

    extractor.update_risk_ewma("S01", 0.8)
    feats = extractor.features_for("S02", line.stations["S02"], "S01")
    # EWMA from a 0.0 baseline with alpha=0.3: 0.3*0.8 + 0.7*0.0 = 0.24
    assert feats["upstream_risk_ewma"] > 0.2


def test_blocked_and_starved_fractions_are_bounded_zero_to_one() -> None:
    config = LineConfig.from_yaml(SCENARIO)
    env = simpy.Environment()
    line = Line(env, config)

    extractor = FeatureExtractor(config.station_ids, config.buffer_capacity_of, sample_dt=7.5)
    for tick in range(1, 200):
        env.run(until=tick * 7.5)
        extractor.sample_tick(line.stations)

    for sid in config.station_ids:
        feats = extractor.features_for(sid, line.stations[sid], None)
        assert 0.0 <= feats["blocked_fraction"] <= 1.0
        assert 0.0 <= feats["starved_fraction"] <= 1.0


def test_queue_pressure_matches_queue_depth_over_capacity() -> None:
    config = LineConfig.from_yaml(SCENARIO)
    env = simpy.Environment()
    line = Line(env, config)
    env.run(until=1000.0)

    extractor = FeatureExtractor(config.station_ids, config.buffer_capacity_of, sample_dt=7.5)
    extractor.sample_tick(line.stations)

    station = line.stations["S17"]
    feats = extractor.features_for("S17", station, "S16")
    expected = len(station.in_buf.items) / config.buffer_capacity_of["S17"]
    assert feats["queue_pressure"] == expected


def test_station_never_modified_by_the_extractor() -> None:
    """The extractor is external, read-only bookkeeping -- it must never
    touch Station's own state machine (sim/station.py's own docstring calls
    that code this project's most delicate).
    """
    config = LineConfig.from_yaml(SCENARIO)
    env = simpy.Environment()
    line = Line(env, config)
    env.run(until=1000.0)

    station = line.stations["S05"]
    state_before = station.state
    time_in_state_before = dict(station.time_in_state)

    extractor = FeatureExtractor(config.station_ids, config.buffer_capacity_of, sample_dt=7.5)
    extractor.sample_tick(line.stations)
    extractor.features_for("S05", station, "S04")

    assert station.state == state_before
    assert station.time_in_state == time_in_state_before
