#!/usr/bin/env python3
"""Generates the labeled training dataset for Model B, per docs/DATA.md.

Five line configurations (distinct variant mixes), never shuffled together:
configs A-D are for training, config E (heavy-SUV) is held out entirely and
never touched until Phase 8's evaluation step. Disjointness is enforced by
construction (each config's rows carry only that config's id) and re-checked
by tests/test_no_config_leak.py.
"""

from __future__ import annotations

import csv
from pathlib import Path

import numpy as np
import simpy

from twin.risk.features import FEATURE_NAMES, FeatureExtractor
from twin.risk.labels import oracle_risk
from twin.sim.line import Line, LineConfig

SCENARIO = Path(__file__).resolve().parents[1] / "scenarios" / "line30.yaml"
OUT_PATH = Path(__file__).resolve().parents[1] / "ml" / "data" / "training_data.csv"
SAMPLE_DT = 7.5
DURATION_S = 90_000.0  # per config; ~150k rows total across 5 configs x 30 stations

# variant weights per config -- (sedan, suv, hatchback). Config E is the
# heavy-SUV, shifting-bottleneck mix already established in Phase 4.
CONFIG_MIXES: dict[str, tuple[float, float, float]] = {
    "A": (0.70, 0.20, 0.10),
    "B": (0.50, 0.30, 0.20),
    "C": (0.30, 0.50, 0.20),
    "D": (0.20, 0.30, 0.50),
    "E": (0.10, 0.80, 0.10),  # HELD OUT -- never trained on
}
CONFIG_SEEDS = {"A": 101, "B": 102, "C": 103, "D": 104, "E": 105}


def _build_config(config_id: str) -> LineConfig:
    config = LineConfig.from_yaml(SCENARIO)
    config.seed = CONFIG_SEEDS[config_id]
    sedan, suv, hatch = CONFIG_MIXES[config_id]
    for v in config.variants:
        v["weight"] = {"sedan": sedan, "suv": suv, "hatchback": hatch}[v["id"]]
    return config


def generate_rows_for_config(config_id: str) -> list[dict]:
    config = _build_config(config_id)
    env = simpy.Environment()
    line = Line(env, config)
    # Separate RNG stream for defect sampling, independent of station/arrival/variant streams.
    rng = np.random.default_rng(CONFIG_SEEDS[config_id] + 50_000)

    extractor = FeatureExtractor(config.station_ids, config.buffer_capacity_of, sample_dt=SAMPLE_DT)
    station_index = {sid: i for i, sid in enumerate(config.station_ids)}

    rows: list[dict] = []
    n_events_seen = 0
    n_ticks = int(DURATION_S / SAMPLE_DT)

    for tick in range(1, n_ticks + 1):
        env.run(until=tick * SAMPLE_DT)
        extractor.sample_tick(line.stations)

        new_events = line.events[n_events_seen:]
        n_events_seen = len(line.events)
        # Process in line order within this tick so a station's
        # upstream_risk_ewma reflects an upstream completion from the SAME
        # tick, not a stale value from a previous one.
        new_events.sort(key=lambda ev: station_index[ev.station_id])

        for event in new_events:
            sid = event.station_id
            idx = station_index[sid]
            upstream = config.station_ids[idx - 1] if idx > 0 else None
            feats = extractor.features_for(sid, line.stations[sid], upstream)
            risk = oracle_risk(feats)
            extractor.update_risk_ewma(sid, risk)

            defect = int(rng.random() < risk)
            rows.append(
                {
                    "config_id": config_id,
                    "unit_id": event.unit_id,
                    "station_id": sid,
                    "variant": event.variant,
                    "tick": tick,
                    **feats,
                    "oracle_risk": risk,
                    "defect": defect,
                }
            )

    return rows


def main() -> None:
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "config_id",
        "unit_id",
        "station_id",
        "variant",
        "tick",
        *FEATURE_NAMES,
        "oracle_risk",
        "defect",
    ]

    all_rows: list[dict] = []
    for config_id in CONFIG_MIXES:
        print(f"Generating config {config_id} (mix={CONFIG_MIXES[config_id]})...")
        rows = generate_rows_for_config(config_id)
        prevalence = sum(r["defect"] for r in rows) / len(rows)
        print(f"  {len(rows)} rows, defect prevalence={prevalence * 100:.3f}%")
        all_rows.extend(rows)

    with OUT_PATH.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(all_rows)

    print(f"\nwrote {len(all_rows)} total rows -> {OUT_PATH}")
    overall_prevalence = sum(r["defect"] for r in all_rows) / len(all_rows)
    print(
        f"overall defect prevalence: {overall_prevalence * 100:.3f}% "
        "(target ~0.58%, Bosch-calibrated)"
    )


if __name__ == "__main__":
    main()
