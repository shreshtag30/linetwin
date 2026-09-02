#!/usr/bin/env python3
"""Detector benchmark across MULTIPLE distinct bottleneck scenarios, not one.

Real gap found in the original tools/run_detector_benchmark.py: its "10
seeds" were 10 replications of the SAME single engineered scenario (S17 as
the dominant bottleneck) -- a fair test of noise-robustness on one scenario,
but not of generalization across different bottleneck identities or line
positions. A detector that happens to be tuned (even unintentionally) to
find S17 specifically would score identically to one that genuinely detects
"whichever station is the real constraint."

This script tests multiple ENGINEERED bottleneck stations, one per zone, at
the same multiplier as the original scenario (1.15x, scenarios/line30.yaml)
so the comparison across zones is apples-to-apples. For each scenario, ground
truth is recomputed from scratch via the same sensitivity analysis (Phase 4)
and VERIFIED to actually match the intended station before being trusted --
an engineered perturbation is not guaranteed to dominate in every zone's own
cycle-time/variability regime, so this is checked, not assumed.

HISTORY: the first run of this script (docs/phases/phase-05-detector-
benchmark.md's addendum) found that 1.15x worked for S17 and S25 but not
S05, and that S25 needed 2.2x for a robust LIVE signal even though 1.15x
already gave a technically-correct ground truth. That turned out to be
downstream of two real simulation-core confounds -- uneven zone-to-zone base
cycle times, and an unpaced arrival source -- both since found and fixed.
With those fixed, a single uniform 1.15x multiplier works for all three
zones, verified directly; the per-zone multiplier table below is no longer
needed and has been removed rather than left stale.
"""

from __future__ import annotations

import copy
import csv
import time
from pathlib import Path

from twin.diagnostic.detectors import run_all_detectors
from twin.diagnostic.evaluate import evaluate_all
from twin.diagnostic.ground_truth import ground_truth_station, measure_all_stations
from twin.diagnostic.run_stats import run_for_analysis
from twin.sim.line import LineConfig

SCENARIO = Path(__file__).resolve().parents[1] / "scenarios" / "line30.yaml"

# One station per zone (body/paint/final). Now that zones are rebalanced,
# the same 1.15x multiplier (the original Phase 5 scenario's own value)
# produces the correct sensitivity-based ground truth for all three,
# verified directly -- no per-zone tuning needed any more.
SCENARIO_MULTIPLIER = {
    "S05": 1.15,  # body
    "S17": 1.15,  # paint -- the original Phase 5 scenario
    "S25": 1.15,  # final
}

SEEDS = list(range(1, 11))  # 10 replications per scenario, same as the original benchmark
DURATION_S = 20_000.0
# 60, raised from 20 -- see tools/run_sensitivity_analysis.py's SEEDS comment
# for the measurement. With correlated condition drift in the line, 20
# replications no longer separate the engineered bottleneck from its nearest
# competitor, so the ground truth this benchmark scores against has to be
# measured more precisely or the verification gate below passes or fails on
# noise.
GROUND_TRUTH_SEEDS = list(range(1, 61))


def build_scenario_config(base: LineConfig, station_id: str) -> LineConfig:
    cfg = copy.deepcopy(base)
    cfg.bottleneck_station_id = station_id
    cfg.bottleneck_multiplier = SCENARIO_MULTIPLIER[station_id]
    return cfg


def main() -> None:
    base_config = LineConfig.from_yaml(SCENARIO)
    out_dir = Path(__file__).resolve().parents[1] / "docs" / "phases"
    out_dir.mkdir(parents=True, exist_ok=True)

    rows = []
    t_start = time.time()

    for station_id, multiplier in SCENARIO_MULTIPLIER.items():
        print(f"\n=== Scenario: {station_id} as engineered bottleneck ({multiplier}x) ===")
        cfg = build_scenario_config(base_config, station_id)

        print(f"  Computing ground truth ({len(GROUND_TRUTH_SEEDS)} seeds, all 30 stations)...")
        t0 = time.time()
        ground_truth = measure_all_stations(cfg, seeds=GROUND_TRUTH_SEEDS, duration=DURATION_S)
        gt_station = ground_truth_station(ground_truth)
        print(f"    done in {time.time() - t0:.1f}s -- ground truth: {gt_station}")

        # Sanity check, not an assumption: an engineered perturbation is not
        # guaranteed to dominate in every zone's own cycle-time/variability
        # regime. If it doesn't, this scenario is not a valid "known ground
        # truth" test case and must not be silently scored as if it were.
        if gt_station != station_id:
            print(
                f"    SKIPPING {station_id}: engineered perturbation did not produce it as "
                f"the sensitivity-based ground truth (got {gt_station} instead). "
                "Needs a larger multiplier for this zone, not a false test case."
            )
            continue

        print(f"  Running all six detectors across {len(SEEDS)} seeds...")
        for seed in SEEDS:
            stats = run_for_analysis(cfg, seed=seed, duration=DURATION_S)
            detector_results = run_all_detectors(stats)
            scores = evaluate_all(detector_results, ground_truth)
            for score in scores:
                rows.append(
                    {
                        "scenario_station": station_id,
                        "seed": seed,
                        "detector": score.name,
                        "top_pick": score.top_pick,
                        "ground_truth_station": score.ground_truth_station,
                        "top1_correct": score.top1_correct,
                        "top3_hit": score.top3_hit,
                        "mse": score.mse,
                    }
                )

    print(f"\nTotal wall time: {time.time() - t_start:.1f}s")

    out_path = out_dir / "detector_comparison_multiscenario.csv"
    with out_path.open("w", newline="", encoding="utf-8") as fh:
        fieldnames = [
            "scenario_station",
            "seed",
            "detector",
            "top_pick",
            "ground_truth_station",
            "top1_correct",
            "top3_hit",
            "mse",
        ]
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    print(f"wrote {out_path}")

    print()
    print("Per-scenario top-1 accuracy:")
    by_scenario_detector: dict[tuple[str, str], list[dict]] = {}
    for row in rows:
        by_scenario_detector.setdefault((row["scenario_station"], row["detector"]), []).append(row)
    for (scenario, name), entries in sorted(by_scenario_detector.items()):
        accuracy = sum(1 for e in entries if e["top1_correct"]) / len(entries)
        print(f"  {scenario:5} {name:16} top-1 accuracy={accuracy * 100:5.1f}%  (n={len(entries)})")

    print()
    print("Overall top-1 accuracy across ALL scenarios and seeds combined:")
    by_detector: dict[str, list[dict]] = {}
    for row in rows:
        by_detector.setdefault(row["detector"], []).append(row)
    for name, entries in sorted(by_detector.items()):
        accuracy = sum(1 for e in entries if e["top1_correct"]) / len(entries)
        n = len(entries)
        n_scenarios = n // len(SEEDS)
        print(f"  {name:16} accuracy={accuracy * 100:5.1f}%  (n={n}, {n_scenarios} scenarios)")


if __name__ == "__main__":
    main()
