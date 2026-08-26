"""Proves lognormal_params is not the "(58, 20.3) freezes the line" bug.

numpy's Generator.lognormal(mean, sigma) takes parameters of the underlying
NORMAL, not the lognormal distribution's own mean/std. Passing a station's
actual desired (mean=58, sigma=cv*mean=20.3) directly would produce a mean on
the order of exp(58) seconds. This file tests the converted parameters actually
recover the requested mean and cv empirically -- not just that the formula runs
without raising.
"""

from __future__ import annotations

import numpy as np
import pytest

from twin.sim.dists import lognormal_params, sample_cycle_time


@pytest.mark.parametrize(
    ("mean", "cv"),
    [(58.0, 0.35), (45.0, 0.12), (65.0, 0.22), (10.0, 0.5), (1000.0, 0.05)],
)
def test_lognormal_params_recovers_requested_mean_and_cv(mean: float, cv: float) -> None:
    mu, sigma = lognormal_params(mean, cv)
    rng = np.random.default_rng(42)
    samples = rng.lognormal(mu, sigma, size=200_000)

    empirical_mean = samples.mean()
    empirical_cv = samples.std() / empirical_mean

    assert empirical_mean == pytest.approx(mean, rel=0.03), (
        f"requested mean {mean}, got empirical mean {empirical_mean}"
    )
    assert empirical_cv == pytest.approx(cv, rel=0.05), (
        f"requested cv {cv}, got empirical cv {empirical_cv}"
    )


def test_naive_misuse_would_freeze_the_line_this_helper_prevents_it() -> None:
    """Demonstrates the exact bug the helper exists to prevent: passing a
    station's real (mean, cv-as-sigma) straight into rng.lognormal without
    conversion silently produces an astronomical value. The sanctioned helper
    must not do this.
    """
    rng = np.random.default_rng(0)

    # The naive, wrong call: treats mean as mu and cv*mean as sigma directly.
    naive_mu, naive_sigma = 58.0, 58.0 * 0.35
    naive_sample = rng.lognormal(naive_mu, naive_sigma)
    assert naive_sample > 1e10, "sanity check: the naive misuse should indeed explode"

    # The correct, sanctioned helper must NOT explode for the same inputs.
    correct_sample = sample_cycle_time(np.random.default_rng(0), mean=58.0, cv=0.35)
    assert correct_sample < 10_000, (
        f"sample_cycle_time must stay in a sane range, got {correct_sample}"
    )


def test_rejects_nonpositive_mean_or_cv() -> None:
    with pytest.raises(ValueError):
        lognormal_params(0.0, 0.2)
    with pytest.raises(ValueError):
        lognormal_params(58.0, 0.0)
    with pytest.raises(ValueError):
        lognormal_params(-5.0, 0.2)
