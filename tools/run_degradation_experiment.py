#!/usr/bin/env python3
"""Graceful-degradation curve: how does inference error grow as sensor
coverage drops from 90% to 40%, and does the graph layer beat the trivial
alternative at all?

Pre-checked before being promised anywhere (docs/DECISIONS.md's own
discipline): a claim of "graceful" degradation is only honest if measured,
and a short line could plausibly show a cliff instead of a gentle slope.

THREE REAL DEFECTS IN THE ORIGINAL VERSION OF THIS SCRIPT, all found by
audit and all fixed here. They are recorded rather than quietly corrected
because the first one invalidated the project's headline claim about this
layer for its entire life:

1.  NO BASELINE ARM. The script measured the graph layer's absolute error
    and nothing else, so it could not answer the only question that
    matters -- is this better than not bothering? It is not enough to be
    accurate; it has to beat the trivial estimator. Adding that arm showed
    the layer had been 33-121% WORSE than simply using each dark station's
    own zone base cycle time. Correct linear algebra (tests/test_inference.py
    verifies the identities exactly, and still does) applied where its
    precondition did not hold: harmonic extension assumes smoothness over
    the graph, and the simulation was drawing every station independently.
    Fixed at the source -- see scenarios/line30.yaml's `condition` block --
    and the baseline is now a permanent column, not a one-off check.

2.  THE SEED WAS ACCEPTED AND NEVER USED. `run_one_trial(..., seed)` took a
    seed and its body ignored it, so all 30 "trials" per coverage level ran
    the IDENTICAL simulation and only the random dark-set varied. Those were
    not independent replications and should never have been averaged as if
    they were. Each trial now genuinely reseeds the line.

3.  THE 100%-COVERAGE ROW WAS AN ARTIFACT. At coverage 1.0 there are no dark
    stations, `harmonic_extension` returns an empty list, and the function
    returned 0.0 -- "no error" only because there was nothing to infer.
    Plotting it next to the real points manufactured an apparent cliff
    between 100% and 90% that was reported in the UI as a finding. The row is
    gone; the curve now starts where inference actually starts.
"""

from __future__ import annotations

import csv
import random
from pathlib import Path

import simpy

from twin.graph.inference import harmonic_extension
from twin.sim.line import Line, LineConfig

SCENARIO = Path(__file__).resolve().parents[1] / "scenarios" / "line30.yaml"
OUT_PATH = Path(__file__).resolve().parents[1] / "docs" / "phases" / "degradation_curve.csv"
# 100% is deliberately absent -- see defect 3 in the module docstring.
COVERAGE_LEVELS = [0.9, 0.8, 0.7, 0.6, 0.5, 0.4]
N_TRIALS_PER_LEVEL = 30
WARMUP_S = 20_000.0


def run_one_trial(
    config: LineConfig, dark_stations: set[str], seed: int
) -> tuple[float, float]:
    """Runs one independently-seeded simulation, then measures how far each
    estimator lands from the truth for the stations we pretend are dark.

    Returns (graph_error, prior_only_error), both mean relative error.

    Ground truth is each station's own recent cycle-time TREND, not the
    duration of one specific unit. That is deliberate and is what this layer
    has always claimed to recover (docs/LIMITATIONS.md: "Inference recovers a
    dark station's cycle-time trend, never whether a specific unit was
    defective"). Scoring it against a single instantaneous draw would be
    scoring it against irreducible per-unit noise it never claimed to predict.
    """
    run_config = LineConfig(**{**config.__dict__, "seed": seed})
    env = simpy.Environment()
    line = Line(env, run_config)
    env.run(until=WARMUP_S)

    truth = {
        sid: st.mean_recent_cycle_time_s
        for sid, st in line.stations.items()
        if st.mean_recent_cycle_time_s is not None
    }
    station_ids = [sid for sid in run_config.station_ids if sid in truth]
    dark = {sid for sid in dark_stations if sid in truth}
    if not dark:
        raise ValueError("no dark station produced a reading -- warm-up too short")

    observed = {sid: truth[sid] for sid in station_ids if sid not in dark}
    prior = {sid: run_config.base_cycle_time_of[sid] for sid in dark}

    results = harmonic_extension(
        station_ids, dark, observed, prior, **run_config.sensor_gap_weights
    )
    graph_error = sum(
        abs(r.value - truth[r.station_id]) / truth[r.station_id] for r in results
    ) / len(results)
    prior_error = sum(abs(prior[sid] - truth[sid]) / truth[sid] for sid in dark) / len(dark)
    return graph_error, prior_error


def main() -> None:
    config = LineConfig.from_yaml(SCENARIO)
    all_station_ids = list(config.station_ids)
    n = len(all_station_ids)

    rng = random.Random(20260828)
    rows = []

    print(f"{'coverage':>9} {'n_dark':>7} {'graph':>9} {'prior-only':>11} {'improvement':>12}")
    for coverage in COVERAGE_LEVELS:
        n_dark = round(n * (1 - coverage))
        graph_errors: list[float] = []
        prior_errors: list[float] = []
        for trial in range(N_TRIALS_PER_LEVEL):
            dark = set(rng.sample(all_station_ids, n_dark))
            g, p = run_one_trial(config, dark, seed=config.seed + 1_000 + trial)
            graph_errors.append(g)
            prior_errors.append(p)

        graph = sum(graph_errors) / len(graph_errors)
        prior_only = sum(prior_errors) / len(prior_errors)
        improvement = (prior_only - graph) / prior_only if prior_only else 0.0
        rows.append(
            {
                "coverage_pct": round(coverage * 100),
                "n_dark": n_dark,
                "mean_relative_error": graph,
                "prior_only_mean_relative_error": prior_only,
                "improvement_over_prior_pct": improvement * 100.0,
            }
        )
        print(
            f"{coverage * 100:8.0f}% {n_dark:7d} {graph:9.4f} {prior_only:11.4f} "
            f"{improvement * 100:11.1f}%"
        )

    with OUT_PATH.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=[
                "coverage_pct",
                "n_dark",
                "mean_relative_error",
                "prior_only_mean_relative_error",
                "improvement_over_prior_pct",
            ],
        )
        writer.writeheader()
        writer.writerows(rows)
    print(f"\nwrote {OUT_PATH}")

    worst = min(r["improvement_over_prior_pct"] for r in rows)
    if worst <= 0:
        print(
            "\nWARNING: the graph layer does NOT beat the prior-only baseline at every "
            "coverage level. Report that finding; do not tune until it wins."
        )


if __name__ == "__main__":
    main()
