"""Distribution helpers.

`lognormal_params` is lint-enforced (tests/test_dists.py, plus a grep-based check
in CI) as the ONLY sanctioned caller of a Generator's `.lognormal(...)` anywhere in
this codebase. The reason: numpy's `Generator.lognormal(mean, sigma)` takes
parameters of the UNDERLYING NORMAL distribution, not the lognormal's own mean and
standard deviation. Passing a station's actual desired mean and CV directly --
e.g. `rng.lognormal(58, 20.3)` for a 58s takt time with sigma taken to be 20.3 --
silently produces a distribution with mean ~= exp(58 + 20.3**2/2), on the order of
1e88 seconds. The station then waits essentially forever on its first cycle,
freezing the line on tick one with no exception raised anywhere. Centralizing the
conversion in one audited function is cheaper than trusting every call site.
"""

from __future__ import annotations

import math

import numpy as np


def lognormal_params(mean: float, cv: float) -> tuple[float, float]:
    """Convert a desired (mean, coefficient of variation) into (mu, sigma) for the
    underlying normal, such that `rng.lognormal(mu, sigma)` samples have the
    requested mean and cv.

    Derivation: for X ~ Lognormal(mu, sigma),
        E[X]   = exp(mu + sigma**2 / 2)
        Var[X] = (exp(sigma**2) - 1) * exp(2*mu + sigma**2)
        CV[X]**2 = Var[X] / E[X]**2 = exp(sigma**2) - 1
    so sigma**2 = ln(1 + cv**2), and mu = ln(mean) - sigma**2 / 2.
    """
    if mean <= 0:
        raise ValueError(f"mean must be positive, got {mean}")
    if cv <= 0:
        raise ValueError(f"cv must be positive, got {cv}")

    sigma_sq = math.log1p(cv**2)
    mu = math.log(mean) - sigma_sq / 2.0
    sigma = math.sqrt(sigma_sq)
    return mu, sigma


def sample_cycle_time(rng: np.random.Generator, mean: float, cv: float) -> float:
    """The one sanctioned call site for `rng.lognormal`."""
    mu, sigma = lognormal_params(mean, cv)
    return float(rng.lognormal(mu, sigma))


__all__ = ["lognormal_params", "sample_cycle_time"]
