"""Phase 4 exit gate: ground truth by sensitivity analysis.

Scoped to a small station subset per test (not all 30) purely for test speed;
`tools/run_sensitivity_analysis.py` runs the full 30-station analysis to
produce the committed `ground_truth.csv`. A ranking is only meaningful once
every real candidate has been tried, so the subset restriction is a test-speed
convenience, never used to shortcut the actual committed artifact.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from twin.diagnostic.ground_truth import (
    _MIN_SAFE_DURATION_S,
    ground_truth_station,
    measure_all_stations,
    measure_sensitivity,
    shifting_trace,
)
from twin.sim.line import LineConfig

SCENARIO = Path(__file__).resolve().parents[1] / "scenarios" / "line30.yaml"
FAST_SEEDS = [1, 2, 3, 4, 5]


@pytest.fixture(scope="module")
def config() -> LineConfig:
    return LineConfig.from_yaml(SCENARIO)


def test_bottleneck_shows_large_negative_sensitivity(config: LineConfig) -> None:
    """Slowing the bottleneck down must clearly reduce throughput."""
    result = measure_sensitivity(config, "S17", seeds=FAST_SEEDS)
    assert result.mean_sensitivity < 0, "slowing S17 must reduce line throughput"
    assert result.ci_high < 0, "the whole CI must exclude zero -- this is not noise"


def test_non_bottleneck_station_shows_much_smaller_sensitivity_than_the_bottleneck(
    config: LineConfig,
) -> None:
    """Not literally near-zero: found, while recalibrating the arrival source
    (docs/phases/phase-05-detector-benchmark.md's addendum), that a realistic
    line run at genuine (not infinite) utilization shows SOME throughput
    sensitivity almost everywhere -- "near zero" was an artifact of an
    earlier, unrealistically slack-heavy source model. Checked directly:
    pushing arrival slack higher to chase a smaller absolute S01 number
    backfires -- above ~1.10x, the arrival process itself becomes the
    dominant bottleneck (S01's sensitivity actually EXCEEDS S17's past
    1.15x). The honest, stable property is relative, not absolute: S01 is
    clearly subordinate to the true bottleneck, not literally unaffected by
    its own cycle time.

    Correction, after the simulation gained correlated condition drift
    (scenarios/line30.yaml's `condition` block): the PROPERTY is unchanged,
    but the 5-seed estimator that used to measure it is no longer precise
    enough. Measured directly at the shipped config -- drift off: ratio
    0.340; drift on at 5 seeds: 0.516 (a noise-driven false failure); drift
    on at 20 seeds: 0.469. Correlated drift adds genuine run-to-run variance,
    so the honest fix is more replications, not a weaker claim. Seeds are
    raised here only; FAST_SEEDS still serves the tests whose assertions are
    robust at five.
    """
    seeds = list(range(1, 21))
    s01 = measure_sensitivity(config, "S01", seeds=seeds)
    s17 = measure_sensitivity(config, "S17", seeds=seeds)
    assert abs(s01.mean_sensitivity) < abs(s17.mean_sensitivity) * 0.55, (
        f"S01 should be clearly subordinate to the true bottleneck S17, "
        f"got S01={s01.mean_sensitivity} vs S17={s17.mean_sensitivity}"
    )


def test_ground_truth_identifies_the_configured_bottleneck(config: LineConfig) -> None:
    """A spot-check subset including the real bottleneck and two plausible
    runners-up (immediately upstream, and a candidate final-assembly station).
    """
    candidates = ["S01", "S13", "S16", "S17", "S19", "S23"]
    results = measure_all_stations(config, seeds=FAST_SEEDS, station_ids=candidates)
    assert ground_truth_station(results) == "S17"


def test_ci_discriminates_the_bottleneck_from_the_runner_up(config: LineConfig) -> None:
    """docs/DECISIONS.md's contingency ladder and the Phase 4 exit gate both
    require CIs narrow enough to discriminate the top-2 stations, not just a
    point-estimate ranking that could be noise.
    """
    results = measure_all_stations(config, seeds=FAST_SEEDS, station_ids=["S13", "S16", "S17"])
    by_id = {r.station_id: r for r in results}
    bottleneck = by_id["S17"]
    runner_up = max(
        (r for r in results if r.station_id != "S17"), key=lambda r: abs(r.mean_sensitivity)
    )

    # Non-overlapping CIs on |sensitivity|: the bottleneck's weakest plausible
    # magnitude must still exceed the runner-up's strongest plausible magnitude.
    assert abs(bottleneck.ci_high) > abs(runner_up.ci_low) or abs(bottleneck.ci_low) > abs(
        runner_up.ci_high
    ), (
        f"expected non-overlapping CIs; bottleneck={bottleneck.ci_low, bottleneck.ci_high}, "
        f"runner_up={runner_up.ci_low, runner_up.ci_high}"
    )


def test_ground_truth_stable_under_a_different_seed_set(config: LineConfig) -> None:
    """Re-seeding the replication set must not change which station wins."""
    candidates = ["S01", "S16", "S17", "S19"]
    results_a = measure_all_stations(config, seeds=[1, 2, 3, 4, 5], station_ids=candidates)
    results_b = measure_all_stations(
        config, seeds=[101, 102, 103, 104, 105], station_ids=candidates
    )

    assert ground_truth_station(results_a) == ground_truth_station(results_b) == "S17"


def test_duration_below_the_verified_safe_floor_is_rejected(config: LineConfig) -> None:
    """The 15,000s floor exists because shorter runs bias cross-station
    comparisons via pipeline-fill transients (see the Phase 3 addendum). This
    must be an explicit, loud error, not a silently wrong ranking.
    """
    with pytest.raises(ValueError, match="pipeline-fill"):
        measure_sensitivity(config, "S17", seeds=[1], duration=_MIN_SAFE_DURATION_S - 1)


def test_shifting_trace_matches_the_two_known_mixes(config: LineConfig) -> None:
    """Reproduces the finding from the Phase 3 addendum with the actual
    sensitivity-based ground-truth definition (not the active-time proxy used
    there for a quick sanity check).
    """
    sweep = [
        {"sedan": 0.5, "suv": 0.3, "hatchback": 0.2},  # the configured normal mix
        {"sedan": 0.05, "suv": 0.90, "hatchback": 0.05},  # heavy SUV mix
    ]
    trace = shifting_trace(
        config, sweep, seeds=FAST_SEEDS, station_ids=["S17", "S19", "S21", "S23"]
    )

    normal_weights, normal_winner = trace[0]
    heavy_suv_weights, heavy_suv_winner = trace[1]

    assert normal_winner == "S17"
    assert heavy_suv_winner != "S17", (
        "a heavy-SUV mix must shift the sensitivity-based ground truth away from S17"
    )
    assert heavy_suv_weights["suv"] == 0.90  # sanity: the sweep point is what we think it is
    assert normal_weights["suv"] == 0.3
