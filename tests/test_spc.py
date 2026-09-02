"""Individuals/moving-range control chart (src/twin/diagnostic/spc.py).

The exit gate here is that each Western Electric rule fires on a series
constructed to trigger exactly it, and stays silent on a clean in-control
series. A rule that never fires is as useless as one that always does, so
both directions are pinned.
"""

from __future__ import annotations

import random

from twin.diagnostic.spc import D2_N2, MIN_POINTS, control_chart


def _stable(n: int = 60, mean: float = 50.0, sd: float = 2.0, seed: int = 7) -> list[float]:
    rng = random.Random(seed)
    return [rng.gauss(mean, sd) for _ in range(n)]


def test_not_established_below_the_minimum_sample() -> None:
    """Control limits from a handful of points are noise. Firing on them
    would manufacture the false alarms the brief warns about.
    """
    chart = control_chart("S01", [50.0] * (MIN_POINTS - 1))
    assert chart.established is False
    assert chart.violations == []


def test_limits_bracket_the_centre_on_a_stable_process() -> None:
    chart = control_chart("S01", _stable())
    assert chart.established is True
    assert chart.lcl < chart.center < chart.ucl


def test_false_alarm_rate_on_an_in_control_process_is_measured_not_assumed() -> None:
    """The honest version of "a stable process is in control".

    An earlier draft of this test asserted a stable series produces NO
    violations. It fails, and it deserves to: running all four Western
    Electric rules over a 60-point window signals on roughly a third of
    perfectly healthy processes. Rule 1 alone accounts for ~15%, which is
    exactly 1 - (1 - 0.0027)^60 -- textbook, not a defect.

    Asserting zero violations would have meant quietly picking a seed where
    none fired. Instead this pins the RATE, so the number the module's
    docstring publishes stays true, and a change that silently made the
    chart trigger-happy fails here.
    """
    fired = 0
    trials = 400
    for seed in range(trials):
        chart = control_chart("S01", _stable(n=60, seed=seed))
        if chart.violations:
            fired += 1
    rate = fired / trials
    assert 0.20 < rate < 0.50, (
        f"combined false-alarm rate over 60 in-control points measured {rate:.1%}; "
        "the module docstring publishes ~34%, and any consumer wiring this to an "
        "operator alert is relying on that figure being accurate"
    )


def test_sigma_is_estimated_from_the_moving_range_not_the_sample_sd() -> None:
    """The whole point of an individuals chart: sigma comes from mR-bar/d2,
    which is robust to a sustained shift in a way the raw sample SD is not.
    Pinning the identity keeps a future refactor from silently substituting
    `statistics.stdev`.
    """
    values = _stable()
    chart = control_chart("S01", values)
    ranges = [abs(values[i] - values[i - 1]) for i in range(1, len(values))]
    expected = (sum(ranges) / len(ranges)) / D2_N2
    assert abs(chart.sigma_hat - expected) < 1e-9
    assert abs(chart.ucl - (chart.center + 3 * expected)) < 1e-9


def test_lower_limit_never_goes_negative() -> None:
    """A cycle time cannot be negative, so a limit below zero would be a
    number the process could not violate even in principle.
    """
    chart = control_chart("S01", _stable(mean=4.0, sd=3.0))
    assert chart.lcl >= 0.0


def test_rule_1_fires_on_a_single_gross_outlier() -> None:
    values = _stable()
    values[40] = 500.0
    chart = control_chart("S01", values)
    rules = {v.rule for v in chart.violations}
    assert "beyond_3_sigma" in rules
    assert any(v.index == 40 for v in chart.violations)


def test_rule_2_fires_on_a_sustained_shift_that_stays_inside_3_sigma() -> None:
    """The case a 3-sigma test alone misses entirely: a small shift that
    never produces a single out-of-limit point.
    """
    values = _stable(n=40, sd=1.0)
    center = sum(values) / len(values)
    for i in range(25, 36):
        values[i] = center + 1.0  # ~1 sigma: inside the limits, but all one side
    chart = control_chart("S01", values)
    assert "run_of_nine" in {v.rule for v in chart.violations}


def test_rule_3_fires_on_a_monotone_trend() -> None:
    values = _stable(n=40, sd=1.0)
    for k in range(8):
        values[20 + k] = 50.0 + k * 0.4  # steadily increasing, small steps
    chart = control_chart("S01", values)
    assert "trend_of_six" in {v.rule for v in chart.violations}


def test_a_perturbed_station_goes_out_of_control_while_a_quiet_one_does_not() -> None:
    """End-to-end sanity in the terms the dashboard would use: the station
    that was slowed is flagged, its neighbour is not.
    """
    quiet = control_chart("S01", _stable(seed=1))
    perturbed_values = _stable(seed=2)
    for i in range(30, 60):
        perturbed_values[i] *= 3.0
    perturbed = control_chart("S17", perturbed_values)

    assert quiet.in_control
    assert not perturbed.in_control
