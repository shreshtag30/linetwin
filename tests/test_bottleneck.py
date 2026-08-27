"""Tests for the live, production bottleneck diagnostic (diagnostic/bottleneck.py).

Three things are tested at three different levels of realism, deliberately:

1. The momentary rule and mode decomposition against direct, hand-constructed
   `StationView` fixtures -- fast, exact, and independent of simulation noise.
2. The significance layer's STATISTICAL WIRING against synthetic data with a
   clearly-separated distribution -- proving ANOVA + Tukey-Kramer correctly
   reaches "established" when the evidence genuinely supports it, which the
   live simulation (see #3) essentially never provides naturally.
3. The full 30-station line, live, parametrised across all six body-zone
   candidate stations that are clearly NOT the bottleneck -- proving the
   momentary rule never names a station that could not plausibly compete.

Two real findings from building this, both documented rather than hidden:

- An earlier version of `Line._source()` fed the first station instantly and
  unconstrained, which made it look artificially bottleneck-like (queue always
  near-full, long active periods). Fixed in `line.py`; before the fix the
  momentary rule split roughly 54%/45% between S01 and the true bottleneck
  S17 across 80 samples of a normal run. After the fix, S01 drops to ~5%.

- Even after that fix, the momentary rule genuinely competes close to 50/50
  between S17 (the true bottleneck) and S13 (the paint zone's higher-CV entry
  station) at arbitrary sampling instants -- not a bug, but the reason the
  significance annotation and the batch "average active duration" method
  (detectors.py, always 5/5 correct across seeds) both exist. The live,
  instantaneous signal is genuinely noisier than a retrospective statistical
  one, and pretending otherwise would be the dishonest choice.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import simpy

from twin.contracts import StationState
from twin.diagnostic.bottleneck import (
    StationView,
    bottleneck_walk_explanation,
    diagnose,
    mode_decomposition,
    momentary_bottleneck,
    significance_annotation,
)
from twin.sim.line import build_line

SCENARIO = Path(__file__).resolve().parents[1] / "scenarios" / "line30.yaml"


def _view(
    sid: str,
    *,
    state: StationState = StationState.WORKING,
    active_period_start: float | None = 0.0,
    durations: list[float] | None = None,
    time_in_state: dict[StationState, float] | None = None,
) -> StationView:
    return StationView(
        station_id=sid,
        state=state,
        active_period_start=active_period_start,
        active_period_durations=durations or [],
        time_in_state=time_in_state or {StationState.WORKING: 100.0},
    )


# ---------------------------------------------------------------------------
# 1. Momentary rule and mode decomposition -- hand-constructed fixtures
# ---------------------------------------------------------------------------


def test_momentary_rule_picks_the_earliest_started_active_station() -> None:
    views = [
        _view("A", active_period_start=50.0),
        _view("B", active_period_start=10.0),  # earliest -> the bottleneck
        _view("C", active_period_start=80.0),
    ]
    bottleneck_id, runner_up_id = momentary_bottleneck(views)
    assert bottleneck_id == "B"
    assert runner_up_id == "A"  # second-earliest


def test_momentary_rule_ignores_inactive_stations_even_with_an_old_start_time() -> None:
    views = [
        _view("A", state=StationState.STARVED, active_period_start=None),
        _view("B", state=StationState.WORKING, active_period_start=40.0),
    ]
    bottleneck_id, _ = momentary_bottleneck(views)
    assert bottleneck_id == "B"


def test_momentary_rule_returns_none_when_the_line_is_genuinely_idle() -> None:
    views = [_view("A", state=StationState.IDLE, active_period_start=None)]
    bottleneck_id, runner_up_id = momentary_bottleneck(views)
    assert bottleneck_id is None
    assert runner_up_id is None


def test_mode_decomposition_reports_downtime_dominant_correctly() -> None:
    view = _view(
        "X",
        time_in_state={
            StationState.WORKING: 59.0,
            StationState.REPAIR: 41.0,
            StationState.STARVED: 500.0,  # inactive time must not dilute the ACTIVE-only fractions
        },
    )
    mode = mode_decomposition(view)
    assert mode["working"] == pytest.approx(0.59, abs=0.01)
    assert mode["repair"] == pytest.approx(0.41, abs=0.01)


def test_bottleneck_walk_phrasing_names_the_dominant_mode() -> None:
    # repair (0.59) genuinely dominates working (0.41) here -- an earlier
    # version of this test used {0.59 working, 0.41 repair}, where working is
    # actually dominant, and asserted the wrong thing would appear. The
    # bottleneck.py docstring's own example had the same inconsistency; both
    # are fixed together.
    explanation = bottleneck_walk_explanation("S17", {"working": 0.41, "repair": 0.59})
    assert "S17" in explanation
    assert "repair" in explanation
    assert "59" in explanation


def test_bottleneck_walk_phrasing_uses_queue_framing_when_purely_working() -> None:
    explanation = bottleneck_walk_explanation("S17", {"working": 1.0})
    assert "queue building faster than downstream can drain" in explanation


# ---------------------------------------------------------------------------
# 2. Significance layer -- statistical wiring proven on synthetic data
# ---------------------------------------------------------------------------


def test_significance_reaches_established_when_evidence_clearly_supports_it() -> None:
    """Synthetic, not simulated: ten short, tightly-clustered periods for the
    bottleneck, all clearly longer than ten equally tight periods for every
    other station. This is the case the live simulation essentially never
    produces naturally (a real bottleneck has few, very long periods, not many
    short separable ones) -- so it must be tested directly to prove the
    ANOVA + Tukey-Kramer wiring itself is correct.
    """
    rng = np.random.default_rng(0)
    bottleneck_durations = list(rng.normal(100.0, 3.0, size=10))
    other_durations = list(rng.normal(20.0, 3.0, size=10))

    views = [
        _view("BOTTLENECK", active_period_start=1000.0, durations=bottleneck_durations),
        _view("OTHER_A", active_period_start=5.0, durations=other_durations),
        _view("OTHER_B", active_period_start=8.0, durations=list(rng.normal(22.0, 3.0, size=10))),
    ]
    confidence, p_value = significance_annotation(views, "BOTTLENECK")
    assert confidence == "established"
    assert p_value is not None
    assert p_value < 0.05


def test_significance_stays_provisional_with_too_few_periods() -> None:
    views = [
        _view("A", durations=[100.0, 105.0]),  # only 2, below MIN_PERIODS_FOR_SIGNIFICANCE
        _view("B", durations=[10.0, 12.0, 11.0]),
    ]
    confidence, p_value = significance_annotation(views, "A")
    assert confidence == "provisional"
    assert p_value is None


def test_significance_stays_provisional_when_groups_are_not_actually_different() -> None:
    rng = np.random.default_rng(1)
    similar_a = list(rng.normal(50.0, 5.0, size=10))
    similar_b = list(rng.normal(51.0, 5.0, size=10))
    views = [
        _view("A", durations=similar_a),
        _view("B", durations=similar_b),
    ]
    confidence, _ = significance_annotation(views, "A")
    assert confidence == "provisional"


# ---------------------------------------------------------------------------
# 3. The full 30-station line, live -- momentary rule never names a clear non-contender
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "clear_non_contender",
    ["S03", "S05", "S08", "S20", "S25", "S29"],
)
def test_momentary_rule_never_names_a_clear_non_contender(clear_non_contender: str) -> None:
    """Parametrised across six stations spread through the body and final
    zones that are neither the configured bottleneck (S17) nor its
    higher-variance paint-zone neighbour (S13, the one genuine live
    competitor found while building this). None of these six is ever
    plausible, so the momentary rule sampled repeatedly across a full run
    must never once pick them.
    """
    env = simpy.Environment()
    line = build_line(env, SCENARIO)

    picks: set[str] = set()
    for _ in range(40):
        env.run(until=env.now + 500.0)
        views = [StationView.from_station(st) for st in line.stations.values()]
        bid, _ = momentary_bottleneck(views)
        if bid is not None:
            picks.add(bid)

    assert clear_non_contender not in picks


def test_momentary_rule_is_dominated_by_the_two_genuine_contenders() -> None:
    """S17 (true bottleneck) and S13 (paint zone's higher-CV gateway) must
    together account for the large majority of momentary picks across a full
    run -- documenting, rather than hiding, that the live signal is genuinely
    contested between exactly these two, not uniformly spread across all 30.
    """
    env = simpy.Environment()
    line = build_line(env, SCENARIO)

    from collections import Counter

    picks: Counter[str] = Counter()
    for _ in range(60):
        env.run(until=env.now + 300.0)
        views = [StationView.from_station(st) for st in line.stations.values()]
        bid, _ = momentary_bottleneck(views)
        if bid is not None:
            picks[bid] += 1

    total = sum(picks.values())
    contender_share = (picks.get("S17", 0) + picks.get("S13", 0)) / total
    assert contender_share > 0.85, (
        f"expected S17+S13 to dominate momentary picks, got shares {dict(picks)}"
    )


def test_diagnose_end_to_end_on_the_live_line_names_a_plausible_station() -> None:
    env = simpy.Environment()
    line = build_line(env, SCENARIO)
    env.run(until=20_000.0)

    views = [StationView.from_station(st) for st in line.stations.values()]
    verdict = diagnose(views)

    assert verdict.station_id in {"S13", "S17"}
    assert verdict.explanation
    assert verdict.confidence in {"provisional", "established"}
