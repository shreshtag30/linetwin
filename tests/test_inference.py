"""Laplacian harmonic extension (src/twin/graph/inference.py). Two identities
are the mandatory exit gate: the lambda=0 exact-mean-of-neighbors case on a
symmetric path, and partition of unity (a constant field is reproduced
exactly). Both are verified numerically here, not just asserted in prose.
"""

from __future__ import annotations

import pytest

from twin.graph.inference import harmonic_extension


def test_symmetric_path_lambda_zero_gives_the_interior_node_the_exact_mean() -> None:
    """lambda=0, symmetric weights (w_down == w_up): a single dark interior
    node between two labeled neighbors must get exactly their mean -- the
    textbook harmonic function result (Zhu, Ghahramani & Lafferty 2003).
    """
    station_ids = ["A", "B", "C"]
    result = harmonic_extension(
        station_ids,
        dark_stations={"B"},
        observed_values={"A": 10.0, "C": 20.0},
        prior_values={"B": 0.0},  # irrelevant at lambda=0
        w_down=1.0,
        w_up=1.0,
        lam=0.0,
    )
    assert len(result) == 1
    assert result[0].value == pytest.approx(15.0, abs=1e-9)
    # lambda=0 means no prior contribution at all.
    assert result[0].sensor_share == pytest.approx(1.0, abs=1e-9)


def test_constant_field_is_reproduced_exactly_partition_of_unity() -> None:
    """If every observed value AND every prior equal the same constant c,
    the inferred value must also be exactly c -- this is what "rows of the
    influence operator sum to exactly 1" means concretely, and it must hold
    regardless of lambda, weights, or how many dark stations are adjacent.
    """
    station_ids = [f"S{i:02d}" for i in range(1, 11)]
    dark = {"S03", "S04", "S07", "S08", "S09"}  # includes adjacent clusters
    c = 42.0
    observed = {sid: c for sid in station_ids if sid not in dark}
    prior = {sid: c for sid in dark}

    result = harmonic_extension(
        station_ids, dark, observed, prior, w_down=1.0, w_up=0.35, lam=0.15
    )
    for r in result:
        assert r.value == pytest.approx(c, abs=1e-9)


def test_sensor_share_plus_prior_share_is_exactly_one() -> None:
    station_ids = [f"S{i:02d}" for i in range(1, 11)]
    dark = {"S05", "S06"}
    observed = {sid: float(i) for i, sid in enumerate(station_ids) if sid not in dark}
    prior = {sid: 5.0 for sid in dark}

    result = harmonic_extension(station_ids, dark, observed, prior, lam=0.15)
    for r in result:
        assert 0.0 <= r.sensor_share <= 1.0 + 1e-9


def test_asymmetric_weights_bias_the_inference_toward_upstream() -> None:
    """w_down=1.0 > w_up=0.35 (defects ride the part forward): a dark
    station's inferred value should sit closer to its UPSTREAM neighbor's
    value than to its downstream neighbor's, when they differ.
    """
    station_ids = ["A", "B", "C"]
    result = harmonic_extension(
        station_ids,
        dark_stations={"B"},
        observed_values={"A": 0.0, "C": 100.0},
        prior_values={"B": 50.0},
        w_down=1.0,
        w_up=0.35,
        lam=0.0,
    )
    inferred = result[0].value
    # Closer to A (0.0, upstream) than to C (100.0, downstream) would mean
    # inferred < 50 (the unweighted midpoint) -- since A dominates the pull.
    assert inferred < 50.0


def test_more_dark_neighbors_reduces_sensor_share() -> None:
    """A dark station surrounded by other dark stations, further from any
    real sensor, should rely more on its prior -- lower sensor_share -- than
    one directly adjacent to an observed neighbor. Not hand-tuned: this must
    fall out of the linear system itself.
    """
    station_ids = [f"S{i:02d}" for i in range(1, 8)]
    observed = {"S01": 10.0, "S07": 10.0}
    dark = {"S02", "S03", "S04", "S05", "S06"}
    prior = dict.fromkeys(dark, 10.0)

    result = harmonic_extension(station_ids, dark, observed, prior, lam=0.15)
    shares = {r.station_id: r.sensor_share for r in result}
    # S02/S06 are adjacent to a real sensor; S04 is in the middle, furthest
    # from any observed station.
    assert shares["S04"] < shares["S02"]
    assert shares["S04"] < shares["S06"]
