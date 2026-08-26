"""Builds a configured line of Stations from a scenario YAML file.

30 stations across 3 zones (docs/DECISIONS.md), config-driven so that scaling to
a different station count or topology is a new YAML file, not new code
(docs/REQUIREMENTS.md B6 -- scalability). Buffers are `simpy.Store(capacity=k)`,
never `Container` -- Container carries no per-unit identity, which would make
Phase 9's defect genealogy impossible.

NOTE on transfer_delay_s: this build connects stations directly via a shared
Store, with no separate conveyor-transit process. transfer_delay_s on every
UnitEvent is therefore always 0.0 here. Phase 9's genealogy realignment
(contributing factors compared at detection time minus cumulative transfer
delay) needs a NONZERO delay to demonstrate correcting for something real; that
synthetic per-link transit time is introduced in Phase 9, not invented here
just to make this field non-trivial. Recorded so it is not mistaken for an
oversight later.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import simpy
import yaml

from twin.contracts import StationState, UnitEvent, Zone
from twin.sim.dists import sample_cycle_time
from twin.sim.rng import make_station_generators
from twin.sim.station import Station


@dataclass
class _Part:
    unit_id: int
    variant: str
    variant_multiplier: float


@dataclass
class LineConfig:
    name: str
    seed: int
    station_ids: list[str]
    zone_of: dict[str, Zone]
    base_cycle_time_of: dict[str, float]
    cv_of: dict[str, float]
    buffer_capacity_of: dict[str, int]
    dark_stations: set[str]
    bottleneck_station_id: str
    bottleneck_multiplier: float
    variants: list[dict[str, Any]]

    @classmethod
    def from_yaml(cls, path: Path | str) -> LineConfig:
        with Path(path).open(encoding="utf-8") as fh:
            raw = yaml.safe_load(fh)

        station_ids: list[str] = []
        zone_of: dict[str, Zone] = {}
        base_cycle_time_of: dict[str, float] = {}
        cv_of: dict[str, float] = {}
        buffer_capacity_of: dict[str, int] = {}

        for zone_name, zcfg in raw["zones"].items():
            zone = Zone(zone_name)
            lo, hi = zcfg["station_range"]
            for n in range(lo, hi + 1):
                sid = f"S{n:02d}"
                station_ids.append(sid)
                zone_of[sid] = zone
                base_cycle_time_of[sid] = float(zcfg["base_cycle_time_s"])
                cv_of[sid] = float(zcfg["cv"])
                buffer_capacity_of[sid] = int(zcfg["buffer_capacity"])

        station_ids.sort(key=lambda s: int(s[1:]))

        return cls(
            name=raw["name"],
            seed=int(raw["seed"]),
            station_ids=station_ids,
            zone_of=zone_of,
            base_cycle_time_of=base_cycle_time_of,
            cv_of=cv_of,
            buffer_capacity_of=buffer_capacity_of,
            dark_stations=set(raw["dark_stations"]),
            bottleneck_station_id=raw["bottleneck"]["station_id"],
            bottleneck_multiplier=float(raw["bottleneck"]["cycle_time_multiplier"]),
            variants=raw["variants"],
        )

    @property
    def instrumented_stations(self) -> set[str]:
        return set(self.station_ids) - self.dark_stations


class Line:
    """A running instance of a configured line, with per-unit event logging."""

    def __init__(self, env: simpy.Environment, config: LineConfig) -> None:
        self.env = env
        self.config = config
        self.stations: dict[str, Station] = {}
        self.events: list[UnitEvent] = []
        self._next_unit_id = 0

        self._rngs = make_station_generators(config.seed, config.station_ids)
        # Separate stream for variant assignment, independent of any station's
        # own stream, so adding/removing a variant does not perturb station
        # cycle-time draws (Common Random Numbers discipline, see rng.py).
        self._variant_rng = np.random.default_rng(config.seed + 10_000)

        buffers: dict[str, simpy.Store] = {
            sid: simpy.Store(env, capacity=config.buffer_capacity_of[sid])
            for sid in config.station_ids
        }

        for i, sid in enumerate(config.station_ids):
            in_buf = buffers[sid]
            is_last = i + 1 >= len(config.station_ids)
            out_buf = None if is_last else buffers[config.station_ids[i + 1]]
            zone = config.zone_of[sid]
            base_cycle = config.base_cycle_time_of[sid]
            cv = config.cv_of[sid]
            multiplier = (
                config.bottleneck_multiplier if sid == config.bottleneck_station_id else 1.0
            )
            rng = self._rngs[sid]

            def sampler(
                _rng: np.random.Generator = rng,
                _mean: float = base_cycle,
                _cv: float = cv,
                _mult: float = multiplier,
            ) -> float:
                return sample_cycle_time(_rng, _mean * _mult, _cv)

            def make_on_departure(_sid: str, _zone: Zone):
                def _on_departure(
                    part: object, entered_at: float, exited_at: float, cycle_time: float
                ) -> None:
                    assert isinstance(part, _Part)
                    self.events.append(
                        UnitEvent(
                            unit_id=part.unit_id,
                            variant=part.variant,
                            station_id=_sid,
                            zone=_zone,
                            entered_at=entered_at,
                            exited_at=exited_at,
                            cycle_time_s=cycle_time,
                            transfer_delay_s=0.0,  # see module docstring
                            state_at_exit=StationState.WORKING,
                            risk_at_exit=None,  # risk layer does not exist until Phase 8
                        )
                    )

                return _on_departure

            self.stations[sid] = Station(
                env=env,
                station_id=sid,
                zone=zone,
                in_buf=in_buf,
                out_buf=out_buf,
                cycle_time_sampler=sampler,
                instrumented=sid in config.instrumented_stations,
                on_departure=make_on_departure(sid, zone),
            )

        self._source_buf = buffers[config.station_ids[0]]
        self._source_process = env.process(self._source())

    def _pick_variant(self) -> tuple[str, float]:
        weights = np.array([v["weight"] for v in self.config.variants], dtype=float)
        weights /= weights.sum()
        idx = self._variant_rng.choice(len(self.config.variants), p=weights)
        chosen = self.config.variants[idx]
        return chosen["id"], float(chosen["cycle_time_multiplier"])

    def _source(self):
        """Feeds units into the first station's buffer as fast as it accepts
        them. Arrival is not the bottleneck of interest here -- the buffers and
        station cycle times are.
        """
        while True:
            variant, mult = self._pick_variant()
            part = _Part(unit_id=self._next_unit_id, variant=variant, variant_multiplier=mult)
            self._next_unit_id += 1
            yield self._source_buf.put(part)

    def time_in_state(self, station_id: str) -> dict[StationState, float]:
        return dict(self.stations[station_id].time_in_state)


def build_line(env: simpy.Environment, scenario_path: Path | str) -> Line:
    config = LineConfig.from_yaml(scenario_path)
    return Line(env, config)


__all__ = ["Line", "LineConfig", "build_line"]
