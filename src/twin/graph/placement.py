"""Greedy sensor placement: given a budget of new sensors, which currently-
dark stations should get them first?

Cites, rather than reimplements, Krause, Singh & Guestrin (2008): their
(1 - 1/e) approximation guarantee is proven for greedy maximization of a
submodular set function measured by mutual-information reduction over a
Gaussian process. This module uses a much simpler proxy -- `1 - sensor_share`
from the harmonic extension's own exact partition of unity (graph/inference.
py) -- as the "how much would a sensor help here" signal, greedily
instrumenting the station currently most reliant on its prior. Naming the
theoretical result is most of the credit here (docs/DECISIONS.md's own
framing); this is our own heuristic run alongside that citation, not a
literal reimplementation of their algorithm.
"""

from __future__ import annotations

from twin.graph.inference import harmonic_extension


def greedy_sensor_placement(
    station_ids: list[str],
    dark_stations: set[str],
    observed_values: dict[str, float],
    prior_values: dict[str, float],
    budget: int,
) -> list[str]:
    """Returns up to `budget` currently-dark station ids, in the order they
    would be most valuable to instrument -- greedily, recomputing the full
    harmonic solve after each pick (a dark station's neighbors' sensor_share
    changes once it is "instrumented", so this is not a one-shot ranking).
    """
    remaining_dark = set(dark_stations)
    remaining_prior = dict(prior_values)
    working_observed = dict(observed_values)
    picks: list[str] = []

    for _ in range(min(budget, len(dark_stations))):
        if not remaining_dark:
            break
        results = harmonic_extension(station_ids, remaining_dark, working_observed, remaining_prior)
        # Greedily pick the station currently LEAST explained by real sensor
        # evidence -- the one relying most on its prior.
        worst = min(results, key=lambda r: r.sensor_share)
        picks.append(worst.station_id)

        remaining_dark.discard(worst.station_id)
        working_observed[worst.station_id] = remaining_prior.pop(worst.station_id)

    return picks


__all__ = ["greedy_sensor_placement"]
