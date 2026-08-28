#!/usr/bin/env python3
"""Solves for the one calibrated constant in labels.py: BIAS, such that mean
oracle_risk over a long baseline run (no perturbation, the scenario's default
variant mix) lands at Bosch's cited ~0.58% prevalence (docs/CITATIONS.md).

Run this, then paste the printed BIAS into src/twin/risk/labels.py by hand --
deliberately not auto-written, so the calibrated value is committed and
reviewed like any other number in this project, not silently regenerated.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import simpy
from scipy.optimize import brentq

from twin.risk.features import FEATURE_NAMES, FeatureExtractor
from twin.risk.labels import WEIGHTS, oracle_risk
from twin.sim.line import Line, LineConfig

SCENARIO = Path(__file__).resolve().parents[1] / "scenarios" / "line30.yaml"
TARGET_PREVALENCE = 0.0058  # Bosch, cited in docs/CITATIONS.md
DURATION_S = 60_000.0
SAMPLE_DT = 7.5


def collect_features(seed: int, bias: float) -> list[dict[str, float]]:
    """Runs one full simulation, computing oracle_risk AT the given bias at
    every step and feeding that real value into the EWMA chain -- not a
    placeholder. `upstream_risk_ewma` is recursive (docs/DATA.md), so which
    bias is "correct" and what the resulting feature distribution looks like
    are mutually dependent; see the fixed-point loop in `main()` below for how
    that circularity is resolved rather than shortcut around.
    """
    config = LineConfig.from_yaml(SCENARIO)
    config.seed = seed
    env = simpy.Environment()
    line = Line(env, config)
    extractor = FeatureExtractor(
        config.station_ids, config.buffer_capacity_of, sample_dt=SAMPLE_DT
    )

    rows: list[dict[str, float]] = []
    n_ticks = int(DURATION_S / SAMPLE_DT)
    for tick in range(1, n_ticks + 1):
        env.run(until=tick * SAMPLE_DT)
        extractor.sample_tick(line.stations)
        for i, sid in enumerate(config.station_ids):
            upstream = config.station_ids[i - 1] if i > 0 else None
            feats = extractor.features_for(sid, line.stations[sid], upstream)
            rows.append(feats)
            extractor.update_risk_ewma(sid, oracle_risk(feats, bias=bias))
    return rows


def mean_risk_at_bias(rows: list[dict[str, float]], bias: float) -> float:
    return float(np.mean([oracle_risk(r, bias=bias) for r in rows]))


def main() -> None:
    bias = -7.0  # arbitrary starting guess; the fixed-point loop below corrects it
    rows: list[dict[str, float]] = []

    print("Solving BIAS as a fixed point: the feature distribution (via the ")
    print("recursive upstream_risk_ewma) depends on bias, and bias is solved ")
    print("from the feature distribution. Iterating to convergence:\n")

    for iteration in range(4):
        print(f"Iteration {iteration}: collecting features at bias={bias:.4f}...")
        rows = collect_features(seed=1, bias=bias)

        def objective(b: float, _rows: list[dict[str, float]] = rows) -> float:
            return mean_risk_at_bias(_rows, b) - TARGET_PREVALENCE

        new_bias = brentq(objective, -30.0, 10.0, xtol=1e-4)
        print(f"  -> re-solved bias = {new_bias:.4f} (was {bias:.4f})")
        if abs(new_bias - bias) < 0.01:
            bias = new_bias
            print("  converged.")
            break
        bias = new_bias

    print()
    for name in FEATURE_NAMES:
        vals = [r[name] for r in rows]
        print(f"  {name:20} mean={np.mean(vals):.4f} std={np.std(vals):.4f}")

    achieved = mean_risk_at_bias(rows, bias)
    print()
    print(f"Weights: {WEIGHTS}")
    print(f"Solved BIAS = {bias:.4f}")
    print(f"Achieved mean oracle_risk = {achieved:.5f} (target {TARGET_PREVALENCE})")
    print()
    print("Paste this into src/twin/risk/labels.py:")
    print(f"BIAS = {bias:.4f}")


if __name__ == "__main__":
    main()
