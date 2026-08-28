#!/usr/bin/env python3
"""Graceful-degradation curve: how does inference error grow as sensor
coverage drops from 100% to 40%? Pre-checked before being promised anywhere
(docs/DECISIONS.md's own discipline): a claim of "graceful" degradation is
only honest if measured, and a short line could plausibly show a cliff
instead of a gentle slope.
"""

from __future__ import annotations

import csv
import random
from pathlib import Path

import simpy

from twin.graph.inference import harmonic_extension
from twin.sim.line import build_line

SCENARIO = Path(__file__).resolve().parents[1] / "scenarios" / "line30.yaml"
OUT_PATH = Path(__file__).resolve().parents[1] / "docs" / "phases" / "degradation_curve.csv"
COVERAGE_LEVELS = [1.0, 0.9, 0.8, 0.7, 0.6, 0.5, 0.4]
N_TRIALS_PER_LEVEL = 30


def run_one_trial(all_station_ids: list[str], dark_stations: set[str], seed: int) -> float:
    """Runs a live simulation once, computes the TRUE cycle time at every
    station (via the sim's own ground truth), then measures how far the
    harmonic extension's inferred value is from that ground truth for the
    stations we pretend are dark -- using every OTHER station as "observed."
    """
    env = simpy.Environment()
    line = build_line(env, SCENARIO)
    env.run(until=8000.0)

    true_values = {
        sid: (st.last_cycle_time_s or line.config.base_cycle_time_of[sid])
        for sid, st in line.stations.items()
    }

    observed = {sid: v for sid, v in true_values.items() if sid not in dark_stations}
    prior = {sid: line.config.base_cycle_time_of[sid] for sid in dark_stations}

    results = harmonic_extension(all_station_ids, dark_stations, observed, prior)
    errors = [abs(r.value - true_values[r.station_id]) / true_values[r.station_id] for r in results]
    return sum(errors) / len(errors) if errors else 0.0


def main() -> None:
    env = simpy.Environment()
    line = build_line(env, SCENARIO)
    all_station_ids = list(line.config.station_ids)
    n = len(all_station_ids)

    rng = random.Random(20260828)
    rows = []

    for coverage in COVERAGE_LEVELS:
        n_dark = round(n * (1 - coverage))
        trial_errors = []
        for trial in range(N_TRIALS_PER_LEVEL):
            dark = set(rng.sample(all_station_ids, n_dark)) if n_dark > 0 else set()
            err = run_one_trial(all_station_ids, dark, seed=trial)
            trial_errors.append(err)
        mean_err = sum(trial_errors) / len(trial_errors)
        row = {
            "coverage_pct": round(coverage * 100),
            "n_dark": n_dark,
            "mean_relative_error": mean_err,
        }
        rows.append(row)
        pct = coverage * 100
        print(f"coverage={pct:.0f}%  n_dark={n_dark:2d}  mean_relative_error={mean_err:.4f}")

    with OUT_PATH.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=["coverage_pct", "n_dark", "mean_relative_error"])
        writer.writeheader()
        writer.writerows(rows)
    print(f"\nwrote {OUT_PATH}")


if __name__ == "__main__":
    main()
