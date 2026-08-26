#!/usr/bin/env python3
"""Generate a schema-conformant replay fixture.

Do NOT hand-author thousands of values. This script produces a plausible,
deterministic (seeded) 60-tick trace across the 30-station line, satisfying every
constraint in `contracts.Snapshot` -- enough to exercise `ReplaySource` and prove
the contract, and to source-agnosticism-test the analytics layers before the real
simulation (Phase 3) exists.

This is fixture data, not the simulation. It does not implement the merged station
pattern or the two load-bearing bugs those tests protect -- that is Phase 3's job.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from twin.contracts import (
    SCHEMA_VERSION,
    SIM_DT,
    BottleneckVerdict,
    Missingness,
    Snapshot,
    StationSnapshot,
    StationState,
    TaggedValue,
    ValueSource,
    Zone,
)

SEED = 20260826
N_TICKS = 60

# 30 stations, 3 zones. Dark stations chosen so 22/30 (73%) are instrumented,
# matching docs/DECISIONS.md -- proportionally heavier than "a meaningful minority"
# so the inference layer is load-bearing rather than decorative.
_ZONE_RANGES: list[tuple[Zone, int, int]] = [
    (Zone.BODY, 1, 12),
    (Zone.PAINT, 13, 18),
    (Zone.FINAL, 19, 30),
]
_DARK_STATIONS: set[str] = {
    "S07",  # body: 1 of 12 dark
    "S15", "S16",  # paint: 2 of 6 dark
    "S22", "S24", "S27", "S28", "S30",  # final: 5 of 12 dark
}
_BOTTLENECK_STATION = "S17"  # paint zone, deliberately loaded


def _station_ids() -> list[tuple[str, Zone]]:
    out: list[tuple[str, Zone]] = []
    for zone, lo, hi in _ZONE_RANGES:
        for n in range(lo, hi + 1):
            out.append((f"S{n:02d}", zone))
    return out


def build_fixture(seed: int = SEED, n_ticks: int = N_TICKS) -> dict:
    stations = _station_ids()
    assert len(stations) == 30
    dark = {sid for sid, _ in stations if sid in _DARK_STATIONS}
    assert len(dark) == 8, "fixture drift: expected exactly 8 dark stations"
    assert len(stations) - len(dark) == 22, "fixture drift: expected 22 instrumented"

    rng = np.random.default_rng(seed)
    # One independent stream per station, so a station's own trace is reproducible
    # in isolation -- mirrors the Phase 3 per-station PCG64 discipline even though
    # this fixture generator is not the real simulation.
    station_rngs = {sid: np.random.default_rng(seed + i) for i, (sid, _) in enumerate(stations)}

    time_in_state: dict[str, dict[str, float]] = {sid: {} for sid, _ in stations}
    units_completed: dict[str, int] = dict.fromkeys((sid for sid, _ in stations), 0)

    snapshots = []
    for tick in range(1, n_ticks + 1):
        sim_time_s = tick * SIM_DT
        station_snaps = []

        for sid, zone in stations:
            r = station_rngs[sid]
            is_bottleneck = sid == _BOTTLENECK_STATION
            is_dark = sid in dark

            # Plausible state distribution: mostly WORKING, occasional stress states,
            # the designated bottleneck spends much more time BLOCKED.
            if is_bottleneck:
                probs = [0.45, 0.05, 0.02, 0.03, 0.05, 0.35, 0.05]
            else:
                probs = [0.72, 0.03, 0.01, 0.02, 0.08, 0.08, 0.06]
            state = StationState(
                r.choice(
                    [s.value for s in StationState],
                    p=probs,
                )
            )
            time_in_state[sid][state.value] = time_in_state[sid].get(state.value, 0.0) + SIM_DT

            base_cycle = 45.0 if zone == Zone.BODY else 65.0 if zone == Zone.PAINT else 55.0
            cycle_time = float(base_cycle * (1.3 if is_bottleneck else 1.0) * r.uniform(0.9, 1.1))

            if state == StationState.WORKING:
                units_completed[sid] += 1

            queue_depth = int(r.integers(0, 5)) if not is_bottleneck else int(r.integers(2, 8))
            buffer_capacity = 6

            if is_dark:
                cycle_val = TaggedValue(
                    value=round(cycle_time, 2),
                    source=ValueSource.INFERRED,
                    missingness=Missingness.PRESENT,
                    confidence=round(float(r.uniform(0.55, 0.8)), 3),
                    staleness_s=round(float(tick * SIM_DT * 0.5), 1),
                    sensor_share=round(float(r.uniform(0.4, 0.7)), 3),
                )
            else:
                cycle_val = TaggedValue(
                    value=round(cycle_time, 2),
                    source=ValueSource.OBSERVED,
                    missingness=Missingness.PRESENT,
                    confidence=1.0,
                    staleness_s=0.0,
                    sensor_share=None,
                )

            total_time = sum(time_in_state[sid].values())
            fractions = (
                {s: v / total_time for s, v in time_in_state[sid].items()} if total_time else {}
            )

            station_snaps.append(
                StationSnapshot(
                    station_id=sid,
                    zone=zone,
                    instrumented=not is_dark,
                    state=state,
                    queue_depth=queue_depth,
                    buffer_capacity=buffer_capacity,
                    cycle_time_s=cycle_val,
                    throughput_uph=round(3600.0 / cycle_time, 2),
                    units_completed=units_completed[sid],
                    time_in_state={StationState(s): f for s, f in fractions.items()},
                )
            )

        verdict = BottleneckVerdict(
            station_id=_BOTTLENECK_STATION,
            confidence="provisional",
            explanation=(
                f"{_BOTTLENECK_STATION} -- queue building faster than downstream can "
                "drain (fixture data, not a measured verdict)"
            ),
        )

        snap = Snapshot(
            seq=tick,
            tick=tick,
            sim_time_s=sim_time_s,
            status="running",
            stations=station_snaps,
            bottleneck=verdict,
            line_throughput_uph=round(
                sum(s.throughput_uph for s in station_snaps) / len(station_snaps), 2
            ),
            wip=int(sum(s.queue_depth for s in station_snaps)),
            real_time_factor=1.0,
            lag_s=0.0,
            tick_compute_ms=round(float(rng.uniform(1.0, 4.0)), 2),
        )
        snapshots.append(json.loads(snap.model_dump_json()))

    return {
        "meta": {
            "schema_version": SCHEMA_VERSION,
            "generator": "tools/gen_fixture.py",
            "seed": seed,
            "n_ticks": n_ticks,
            "station_count": len(stations),
            "instrumented_count": len(stations) - len(dark),
            "note": (
                "Fixture data for contract conformance and source-agnosticism "
                "testing. Not the simulation -- see Phase 3."
            ),
        },
        "snapshots": snapshots,
    }


def main() -> None:
    out_path = Path(__file__).resolve().parents[1] / "fixtures" / "replay_30x60.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fixture = build_fixture()
    out_path.write_text(json.dumps(fixture, indent=2), encoding="utf-8")
    print(f"wrote {len(fixture['snapshots'])} snapshots -> {out_path}")


if __name__ == "__main__":
    main()
