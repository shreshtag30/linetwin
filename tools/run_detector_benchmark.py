#!/usr/bin/env python3
"""Produce the committed detector-comparison artifact for Phase 5.

Runs all six bottleneck detectors across many seeds and scores each against
Phase 4's sensitivity-based ground truth, writing detector_comparison.csv.
Deliberately a one-time analysis script, not re-run in CI -- tests/test_
detectors.py covers the same logic on a fast, restricted seed/station subset.
"""

from __future__ import annotations

import csv
import time
from pathlib import Path

from twin.diagnostic.detectors import run_all_detectors
from twin.diagnostic.evaluate import evaluate_all
from twin.diagnostic.ground_truth import measure_all_stations
from twin.diagnostic.run_stats import run_for_analysis
from twin.sim.line import LineConfig

SCENARIO = Path(__file__).resolve().parents[1] / "scenarios" / "line30.yaml"
SEEDS = list(range(1, 11))  # 10 replications -- cheap; ground truth (below) is the slow part
DURATION_S = 20_000.0
GROUND_TRUTH_SEEDS = list(range(1, 21))  # matches Phase 4's committed ground_truth.csv


def main() -> None:
    config = LineConfig.from_yaml(SCENARIO)
    out_dir = Path(__file__).resolve().parents[1] / "docs" / "phases"
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"Computing ground truth ({len(GROUND_TRUTH_SEEDS)} seeds, all 30 stations)...")
    t0 = time.time()
    ground_truth = measure_all_stations(config, seeds=GROUND_TRUTH_SEEDS, duration=DURATION_S)
    print(f"  done in {time.time() - t0:.1f}s")

    print(f"Running all six detectors across {len(SEEDS)} seeds...")
    rows = []
    t0 = time.time()
    for seed in SEEDS:
        stats = run_for_analysis(config, seed=seed, duration=DURATION_S)
        detector_results = run_all_detectors(stats)
        scores = evaluate_all(detector_results, ground_truth)
        for score in scores:
            rows.append(
                {
                    "seed": seed,
                    "detector": score.name,
                    "top_pick": score.top_pick,
                    "ground_truth_station": score.ground_truth_station,
                    "top1_correct": score.top1_correct,
                    "top3_hit": score.top3_hit,
                    "mse": score.mse,
                }
            )
    print(f"  done in {time.time() - t0:.1f}s")

    out_path = out_dir / "detector_comparison.csv"
    with out_path.open("w", newline="", encoding="utf-8") as fh:
        fieldnames = [
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
    print("Summary (top-1 accuracy across all seeds, mean MSE where applicable):")
    by_detector: dict[str, list[dict]] = {}
    for row in rows:
        by_detector.setdefault(row["detector"], []).append(row)
    for name, entries in by_detector.items():
        accuracy = sum(1 for e in entries if e["top1_correct"]) / len(entries)
        mses = [e["mse"] for e in entries if e["mse"] is not None]
        mse_str = f"{sum(mses) / len(mses):.5f}" if mses else "N/A (discrete-pick method)"
        print(f"  {name:16} top-1 accuracy={accuracy * 100:5.1f}%   mean MSE={mse_str}")


if __name__ == "__main__":
    main()
