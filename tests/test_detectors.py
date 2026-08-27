"""The six-detector benchmark, scored against Phase 4's ground truth.

Fast, restricted-seed checks here (matching test_ground_truth.py's approach);
`tools/run_detector_benchmark.py` runs the full comparison and produces the
committed `detector_comparison.csv`.

Two real findings from building this, both asserted directly rather than
described only in prose, so a future change cannot silently break them without
a test noticing:

- Queue Length does not pick the true bottleneck S17. Before a source-model
  fix (see test_bottleneck.py and the Phase 5 record) it picked S01 100% of
  the time, for an artifact reason (an unconstrained source kept S01's queue
  permanently near-full). After the fix it consistently picks S12 instead --
  a real, well-understood weakness: queue buildup accumulates through a long
  near-balanced section approaching a bottleneck and is detected one station
  early, not at the bottleneck itself.
- Arrow is genuinely unstable on this topology: across five seeds it picks
  S17 correctly three times and S13 (incorrectly) twice, because blocking
  probability accumulates diffusely through the whole body zone before
  sharply reversing at the true bottleneck, sometimes creating a competing
  "sink" earlier in the chain. This is asserted as a *rate*, not a pass/fail
  on a single seed, since the instability itself is the honest finding.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from twin.diagnostic.detectors import run_all_detectors
from twin.diagnostic.evaluate import evaluate_all
from twin.diagnostic.ground_truth import measure_all_stations
from twin.diagnostic.run_stats import run_for_analysis
from twin.sim.line import LineConfig

SCENARIO = Path(__file__).resolve().parents[1] / "scenarios" / "line30.yaml"
DURATION_S = 20_000.0


@pytest.fixture(scope="module")
def config() -> LineConfig:
    return LineConfig.from_yaml(SCENARIO)


@pytest.mark.parametrize("seed", [1, 2, 3, 4, 5])
def test_utilization_active_period_busy_ratio_turning_point_all_pick_s17(
    config: LineConfig, seed: int
) -> None:
    """The four methods found to be robust across all five seeds during
    Phase 5 groundwork. If any of these regresses on a future change to the
    scenario or the simulation core, this must fail loudly.
    """
    stats = run_for_analysis(config, seed=seed, duration=DURATION_S)
    results = {r.name: r for r in run_all_detectors(stats)}

    assert results["utilization"].top_pick == "S17"
    assert results["active_period"].top_pick == "S17"
    assert results["busy_ratio"].top_pick == "S17"
    assert results["turning_point"].top_pick == "S17"


def test_queue_length_consistently_misses_in_the_documented_way(config: LineConfig) -> None:
    """Queue Length's failure is not random noise -- it consistently and
    specifically picks S12 (immediately upstream of the bottleneck's zone),
    which is the real, cited weakness of the method, not a bug in this
    implementation.
    """
    picks = set()
    for seed in [1, 2, 3]:
        stats = run_for_analysis(config, seed=seed, duration=DURATION_S)
        result = next(r for r in run_all_detectors(stats) if r.name == "queue_length")
        picks.add(result.top_pick)
        assert result.top_pick != "S01", (
            "S01 must not be picked -- that would indicate the fixed source-model "
            "artifact has regressed"
        )
    assert picks == {"S12"}, f"expected a consistent (if wrong) pick of S12, got {picks}"


def test_arrow_is_measurably_unstable_across_seeds(config: LineConfig) -> None:
    """Documents the instability directly: neither 'always right' nor
    'always wrong', which is itself the honest characterization.
    """
    picks = []
    for seed in [1, 2, 3, 4, 5]:
        stats = run_for_analysis(config, seed=seed, duration=DURATION_S)
        result = next(r for r in run_all_detectors(stats) if r.name == "arrow")
        picks.append(result.top_pick)

    correct = sum(1 for p in picks if p == "S17")
    assert set(picks) <= {"S17", "S13"}, f"expected only the two known contenders, got {picks}"
    assert 1 <= correct <= 4, f"expected genuine instability (neither 0/5 nor 5/5), got {correct}/5"


def test_evaluate_all_reports_mse_only_for_scored_detectors(config: LineConfig) -> None:
    """Arrow and Turning Point have no native per-station score in their
    original published form; MSE and top-3 must be None for them, not an
    invented number.
    """
    stats = run_for_analysis(config, seed=1, duration=DURATION_S)
    detector_results = run_all_detectors(stats)
    ground_truth = measure_all_stations(
        config, seeds=[1, 2, 3], station_ids=["S13", "S16", "S17", "S18"]
    )
    scores = {s.name: s for s in evaluate_all(detector_results, ground_truth)}

    for scored_name in ("utilization", "active_period", "busy_ratio", "queue_length"):
        assert scores[scored_name].mse is not None
        assert scores[scored_name].top3_hit is not None

    for discrete_name in ("arrow", "turning_point"):
        assert scores[discrete_name].mse is None
        assert scores[discrete_name].top3_hit is None
        # top1 is still meaningful for a discrete-pick method
        assert scores[discrete_name].top1_correct in (True, False)


def test_the_best_continuous_detector_beats_queue_length_on_mse(config: LineConfig) -> None:
    stats = run_for_analysis(config, seed=1, duration=DURATION_S)
    detector_results = run_all_detectors(stats)
    ground_truth = measure_all_stations(
        config, seeds=[1, 2, 3], station_ids=["S01", "S12", "S13", "S16", "S17", "S18"]
    )
    scores = {s.name: s for s in evaluate_all(detector_results, ground_truth)}

    assert scores["active_period"].mse < scores["queue_length"].mse
