#!/usr/bin/env python3
"""Produce the committed ground-truth artifacts for Phase 4.

Runs the full 30-station sensitivity analysis (docs/phases/phase-04-*.md) and
writes two CSVs: the per-station sensitivity ranking, and the variant-mix
shifting-bottleneck trace. This is deliberately a one-time analysis script,
not something re-run on every test — tests/test_ground_truth.py covers the
same logic on a fast, restricted station subset.

Runtime: ~30 stations x 20 seeds x 2 runs x 20,000 sim-seconds each, plus the
5-point shifting sweep over 4 candidate stations -- a few minutes on this
machine. Not something to run in CI.
"""

from __future__ import annotations

import csv
import time
from pathlib import Path

from twin.diagnostic.ground_truth import ground_truth_station, measure_all_stations, shifting_trace
from twin.sim.line import LineConfig

SCENARIO = Path(__file__).resolve().parents[1] / "scenarios" / "line30.yaml"
# 60, raised from 20. Correlated condition drift (scenarios/line30.yaml's
# `condition` block) adds genuine run-to-run variance, and 20 replications no
# longer resolve rank 1 from rank 2. Measured directly on S17 (the engineered
# bottleneck) against its two closest competitors:
#     20 seeds -> S17 -0.4272, S20 -0.4416, S29 -0.4224  => picks S20 (wrong)
#     60 seeds -> S17 -0.4560, S20 -0.4176, S29 -0.4064  => picks S17
#    120 seeds -> S17 -0.4596, S20 -0.3964, S29 -0.4078  => picks S17
# The structure was never in doubt; the estimator was under-powered. Raising
# replications is the correct response to added variance -- lowering the drift
# until a noisy estimator agrees would have been fitting the measurement to the
# tool.
SEEDS = list(range(1, 61))
DURATION_S = 20_000.0

# The shifting-bottleneck sweep is restricted to 4 plausible candidates rather
# than all 30 purely to keep the sweep's runtime reasonable (5 points x 4
# stations x 20 seeds x 2 runs, vs x30 for the full ranking above); the full
# ranking above already establishes these are the only stations that come
# close to competing across the variant-mix range.
SHIFT_CANDIDATES = ["S13", "S17", "S19", "S23"]
SHIFT_SWEEP = [
    {"sedan": 1.00, "suv": 0.00, "hatchback": 0.00},
    {"sedan": 0.50, "suv": 0.30, "hatchback": 0.20},  # the configured normal mix
    {"sedan": 0.30, "suv": 0.60, "hatchback": 0.10},
    {"sedan": 0.10, "suv": 0.80, "hatchback": 0.10},
    {"sedan": 0.05, "suv": 0.90, "hatchback": 0.05},
]


def main() -> None:
    config = LineConfig.from_yaml(SCENARIO)
    out_dir = Path(__file__).resolve().parents[1] / "docs" / "phases"
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"Running full 30-station sensitivity analysis ({len(SEEDS)} seeds each)...")
    t0 = time.time()
    results = measure_all_stations(config, seeds=SEEDS, duration=DURATION_S)
    print(f"  done in {time.time() - t0:.1f}s")

    ranked = sorted(results, key=lambda r: -abs(r.mean_sensitivity))
    gt_path = out_dir / "ground_truth.csv"
    with gt_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(
            ["rank", "station_id", "mean_sensitivity", "ci_low", "ci_high", "n_replications"]
        )
        for rank, r in enumerate(ranked, start=1):
            writer.writerow(
                [rank, r.station_id, r.mean_sensitivity, r.ci_low, r.ci_high, r.n_replications]
            )
    print(f"wrote {gt_path} -- ground truth: {ground_truth_station(results)}")

    # SEPARATION DIAGNOSTIC, printed rather than left for a reader to
    # discover. `ground_truth_station` takes an argmax, which says nothing
    # about whether rank 1 is distinguishable from rank 2. On this line it
    # frequently is not: the top several stations' bootstrap CIs overlap, so
    # "top-1 accuracy" is being scored against an ordering that is partly
    # within noise. That is a real limitation of a sensitivity-derived ground
    # truth on a line whose stations are deliberately balanced, and it is the
    # honest frame for every detector number computed against it.
    top, second = ranked[0], ranked[1]
    gap = abs(top.mean_sensitivity) - abs(second.mean_sensitivity)
    overlaps = not (top.ci_high < second.ci_low or top.ci_low > second.ci_high)
    n_overlapping = sum(
        1
        for r in ranked[1:]
        if not (top.ci_high < r.ci_low or top.ci_low > r.ci_high)
    )
    print(
        f"  separation: #1 {top.station_id} ({top.mean_sensitivity:+.4f}) vs "
        f"#2 {second.station_id} ({second.mean_sensitivity:+.4f}), gap={gap:+.4f}"
    )
    print(
        f"  #1 CI overlaps #2: {overlaps}; "
        f"{n_overlapping} of {len(ranked) - 1} other stations overlap #1's CI"
    )
    if overlaps:
        print(
            "  NOTE: rank 1 is NOT statistically separated from rank 2. Report "
            "top-1 detector accuracy against this ground truth with that caveat."
        )

    print(f"Running shifting-bottleneck sweep over {len(SHIFT_SWEEP)} variant mixes...")
    t0 = time.time()
    trace = shifting_trace(
        config, SHIFT_SWEEP, seeds=SEEDS, duration=DURATION_S, station_ids=SHIFT_CANDIDATES
    )
    print(f"  done in {time.time() - t0:.1f}s")

    trace_path = out_dir / "shifting_trace.csv"
    with trace_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["sedan_weight", "suv_weight", "hatchback_weight", "ground_truth_station"])
        for weights, winner in trace:
            writer.writerow([weights["sedan"], weights["suv"], weights["hatchback"], winner])
    print(f"wrote {trace_path}")
    for weights, winner in trace:
        s, u, h = weights["sedan"], weights["suv"], weights["hatchback"]
        print(f"  sedan={s:.2f} suv={u:.2f} hatch={h:.2f} -> {winner}")


if __name__ == "__main__":
    main()
