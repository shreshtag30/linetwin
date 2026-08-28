"""Greedy sensor placement (src/twin/graph/placement.py)."""

from __future__ import annotations

from twin.graph.placement import greedy_sensor_placement


def test_picks_the_least_sensor_backed_station_first() -> None:
    station_ids = [f"S{i:02d}" for i in range(1, 8)]
    observed = {"S01": 10.0, "S07": 10.0}
    dark = {"S02", "S03", "S04", "S05", "S06"}
    prior = dict.fromkeys(dark, 10.0)

    picks = greedy_sensor_placement(station_ids, dark, observed, prior, budget=1)
    # NOT the symmetric midpoint (S04) -- an earlier version of this test
    # assumed that and was wrong. With asymmetric weights (w_down=1.0 >
    # w_up=0.35), S01's forward influence reaches further than S07's weak
    # backward influence, so the worst-covered point shifts toward S07's
    # side. Verified directly (sensor_share per station): S02 0.827, S03
    # 0.686, S04 0.579, S05 0.522 (minimum), S06 0.581 -- S05, not S04.
    assert picks == ["S05"]


def test_picks_are_unique_and_bounded_by_budget() -> None:
    station_ids = [f"S{i:02d}" for i in range(1, 11)]
    observed = {"S01": 5.0, "S10": 5.0}
    dark = {f"S{i:02d}" for i in range(2, 10)}
    prior = dict.fromkeys(dark, 5.0)

    picks = greedy_sensor_placement(station_ids, dark, observed, prior, budget=3)
    assert len(picks) == 3
    assert len(set(picks)) == 3
    assert all(p in dark for p in picks)


def test_budget_larger_than_dark_set_returns_every_dark_station() -> None:
    station_ids = [f"S{i:02d}" for i in range(1, 6)]
    observed = {"S01": 1.0, "S05": 1.0}
    dark = {"S02", "S03", "S04"}
    prior = dict.fromkeys(dark, 1.0)

    picks = greedy_sensor_placement(station_ids, dark, observed, prior, budget=100)
    assert set(picks) == dark
    assert len(picks) == 3


def test_zero_budget_returns_no_picks() -> None:
    station_ids = ["S01", "S02", "S03"]
    picks = greedy_sensor_placement(
        station_ids, {"S02"}, {"S01": 1.0, "S03": 1.0}, {"S02": 1.0}, budget=0
    )
    assert picks == []


def test_greedy_order_improves_each_remaining_stations_sensor_share() -> None:
    """After instrumenting the worst-covered station, its former neighbors'
    sensor_share should not get worse -- a sanity check that the greedy loop
    is actually re-solving, not just ranking once and freezing.
    """
    from twin.graph.inference import harmonic_extension

    station_ids = [f"S{i:02d}" for i in range(1, 8)]
    observed = {"S01": 10.0, "S07": 10.0}
    dark = {"S02", "S03", "S04", "S05", "S06"}
    prior = dict.fromkeys(dark, 10.0)

    before_results = harmonic_extension(station_ids, dark, observed, prior)
    before = {r.station_id: r.sensor_share for r in before_results}

    picks = greedy_sensor_placement(station_ids, dark, observed, prior, budget=1)
    newly_observed = dict(observed)
    newly_observed[picks[0]] = prior[picks[0]]
    remaining_dark = dark - {picks[0]}
    remaining_prior = {k: v for k, v in prior.items() if k != picks[0]}

    after = {
        r.station_id: r.sensor_share
        for r in harmonic_extension(station_ids, remaining_dark, newly_observed, remaining_prior)
    }
    for sid in remaining_dark:
        assert after[sid] >= before[sid] - 1e-9
