"""Builds a configured line of Stations from a scenario YAML file.

30 stations across 3 zones (docs/DECISIONS.md), config-driven so that scaling to
a different station count or topology is a new YAML file, not new code
(docs/REQUIREMENTS.md B6 -- scalability). Buffers are `simpy.Store(capacity=k)`,
never `Container` -- Container carries no per-unit identity, which would make
Phase 9's defect genealogy impossible.

NOTE on transfer_delay_s (Phase 9): stations still connect directly via a
shared Store, with no separate conveyor-transit process affecting simulation
timing -- only `UnitEvent.transfer_delay_s` carries a nonzero value now,
a fixed, documented, synthetic per-link constant (`CONVEYOR_TRANSFER_DELAY_S`
below), recorded as metadata for `diagnostic/genealogy.py`'s transfer-delay
realignment to have something real to correct for. It is deliberately NOT fed
back into `env.timeout()` anywhere: introducing an actual conveyor-transit
delay would change cascade timing throughout the line, which is exactly the
kind of scope creep this project's phase boundaries exist to prevent -- this
field's purpose is to make genealogy's realignment math meaningful, not to
model conveyor transit as a simulated process.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import simpy
import yaml

from twin.contracts import StationState, UnitEvent, Zone
from twin.sim.dists import sample_cycle_time
from twin.sim.rng import make_station_generators
from twin.sim.station import BreakdownProfile, Station

# Synthetic, fixed, metadata-only -- see the module docstring's note on
# transfer_delay_s. `synthetic -- uncalibrated`, same discipline as every
# other timing parameter in this project (docs/CITATIONS.md).
CONVEYOR_TRANSFER_DELAY_S = 4.0

# See _source()'s docstring: paces the arrival source slightly slower than
# the first station's own mean cycle time, giving the line real slack instead
# of running at exactly 100% capacity. `synthetic -- uncalibrated`; verified
# directly (not assumed) to remove a structural detection artifact where S01
# wins bottleneck-detector picks by being immune to starvation, not by being
# genuinely disrupted.
ARRIVAL_SLACK_FACTOR = 1.08


@dataclass
class _Part:
    unit_id: int
    variant: str


@dataclass(frozen=True)
class ConditionParams:
    """Slow, spatially-correlated drift in how fast each station is running.

    WHY THIS EXISTS (and why its absence was a real defect). Every station's
    cycle time used to be an INDEPENDENT lognormal draw around a known,
    per-zone constant. Under that generating process a neighbour's reading
    carries no information about a dark station -- so the sensor-gap layer
    (graph/inference.py) could not possibly beat the trivial "just use the
    zone's base cycle time" estimator, and measured 33-121% WORSE than it.
    Harmonic extension assumes SMOOTHNESS OVER THE GRAPH; the simulation was
    violating that assumption by construction, so the method was being
    applied where its own precondition did not hold.

    This field supplies the structure the method assumes, and it is not a
    thumb on the scale: shared slow drift between physically adjacent
    stations is exactly what the brief means by "equipment wear" and
    "environmental conditions", and it is the standard reason neighbouring
    machines on a real line co-vary (shared air handling, shared tooling
    replenishment cycles, shared upstream material lot).

    STRUCTURE, stated explicitly so nothing here reads as invented physics:
    a zero-mean Gaussian field `z` over stations, AR(1) in SPACE (station
    index) so adjacent stations correlate at `spatial_alpha`, and AR(1) in
    TIME so the field drifts slowly with persistence `temporal_phi`. The
    per-station multiplier is `exp(sigma*z - sigma^2/2)`, which is
    lognormal with mean exactly 1.0 -- so this adds correlated variation
    WITHOUT shifting any station's long-run mean, and therefore cannot by
    itself manufacture or move a bottleneck.

    `synthetic -- uncalibrated`. What would calibrate it: the empirical
    station-to-station correlation of cycle-time residuals from a real
    line's MES, which this project does not have.
    """

    update_interval_s: float
    temporal_phi: float
    spatial_alpha: float
    sigma: float

    @property
    def enabled(self) -> bool:
        return self.sigma > 0.0


class ConditionField:
    """The `z` field described in ConditionParams, advanced on a fixed tick."""

    def __init__(self, n: int, params: ConditionParams, rng: np.random.Generator) -> None:
        self._n = n
        self._p = params
        self._rng = rng
        self._z = np.zeros(n)
        self.step()  # start from a drawn field, not from all-zeros

    def step(self) -> None:
        eps = self._rng.standard_normal(self._n)
        # AR(1) in space: correlation between stations i and i+k is alpha**k.
        alpha = self._p.spatial_alpha
        spatial = np.empty(self._n)
        spatial[0] = eps[0]
        scale = math.sqrt(max(0.0, 1.0 - alpha * alpha))
        for i in range(1, self._n):
            spatial[i] = alpha * spatial[i - 1] + scale * eps[i]
        # AR(1) in time, preserving unit marginal variance.
        phi = self._p.temporal_phi
        self._z = phi * self._z + math.sqrt(max(0.0, 1.0 - phi * phi)) * spatial

    def multiplier(self, index: int) -> float:
        """Lognormal with mean exactly 1.0 -- see ConditionParams."""
        sigma = self._p.sigma
        return float(math.exp(sigma * self._z[index] - sigma * sigma / 2.0))


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
    # variant_id -> zone -> multiplier. Per-zone, not a single scalar per
    # variant: a uniform scalar would scale every station identically and
    # could never change WHICH station is the bottleneck, which would make
    # Phase 4's shifting-bottleneck requirement unsatisfiable by construction.
    variant_zone_multiplier: dict[str, dict[Zone, float]]
    condition: ConditionParams
    breakdowns: BreakdownProfile | None
    # Read from the scenario's `sensor_gap_weights` block. These were
    # declared in scenarios/line30.yaml from the beginning but never parsed,
    # so the hardcoded defaults in graph/inference.py were what actually ran
    # and a differently-instrumented site could not retune the operator from
    # config. Now genuinely config-driven.
    sensor_gap_weights: dict[str, float]

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

        variant_zone_multiplier: dict[str, dict[Zone, float]] = {}
        for v in raw["variants"]:
            variant_zone_multiplier[v["id"]] = {
                Zone(zname): float(mult) for zname, mult in v["zone_multipliers"].items()
            }

        # All three blocks are optional so an older scenario file (and every
        # test that builds a minimal config) still loads: absent `condition`
        # means sigma=0 (no drift), absent `breakdowns` means none.
        cond_raw = raw.get("condition") or {}
        condition = ConditionParams(
            update_interval_s=float(cond_raw.get("update_interval_s", 60.0)),
            temporal_phi=float(cond_raw.get("temporal_phi", 0.0)),
            spatial_alpha=float(cond_raw.get("spatial_alpha", 0.0)),
            sigma=float(cond_raw.get("sigma", 0.0)),
        )

        bd_raw = raw.get("breakdowns") or {}
        breakdowns = (
            BreakdownProfile(
                mtbf_productive_s=float(bd_raw["mtbf_productive_s"]),
                detect_s=float(bd_raw.get("detect_s", 0.0)),
                mttr_s=float(bd_raw["mttr_s"]),
            )
            if bd_raw.get("mtbf_productive_s")
            else None
        )

        sgw_raw = raw.get("sensor_gap_weights") or {}
        sensor_gap_weights = {
            "w_down": float(sgw_raw.get("w_down", 1.0)),
            "w_up": float(sgw_raw.get("w_up", 0.35)),
            "lam": float(sgw_raw.get("lambda", 0.15)),
        }

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
            variant_zone_multiplier=variant_zone_multiplier,
            condition=condition,
            breakdowns=breakdowns,
            sensor_gap_weights=sensor_gap_weights,
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

        # Live-adjustable per-station multiplier, separate from the static
        # scenario config, so Phase 6's control endpoint can perturb a
        # station's cycle time mid-run without rebuilding the line. Seeded
        # from the scenario's configured bottleneck multiplier so existing
        # behavior (Phases 3-5) is unchanged until a control command arrives.
        self._live_multiplier: dict[str, float] = dict.fromkeys(config.station_ids, 1.0)
        self._live_multiplier[config.bottleneck_station_id] = config.bottleneck_multiplier

        self._rngs = make_station_generators(config.seed, config.station_ids)
        # Separate stream for variant assignment, independent of any station's
        # own stream, so adding/removing a variant does not perturb station
        # cycle-time draws (Common Random Numbers discipline, see rng.py).
        self._variant_rng = np.random.default_rng(config.seed + 10_000)
        # Separate stream for arrivals -- see _source()'s docstring for why
        # this exists at all.
        self._arrival_rng = np.random.default_rng(config.seed + 20_000)
        # Two more independent streams, same CRN discipline: enabling
        # breakdowns or condition drift must not shift any station's
        # cycle-time draws, and a (baseline, perturbed) pair at a fixed seed
        # must see the IDENTICAL failure and drift sequence so those cancel
        # exactly in the paired difference (diagnostic/ground_truth.py).
        self._failure_rngs = make_station_generators(config.seed + 30_000, config.station_ids)
        self._condition_rng = np.random.default_rng(config.seed + 40_000)

        self._station_index = {sid: i for i, sid in enumerate(config.station_ids)}
        self.condition: ConditionField | None = (
            ConditionField(len(config.station_ids), config.condition, self._condition_rng)
            if config.condition.enabled
            else None
        )

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
            rng = self._rngs[sid]

            def sampler(
                part: object,
                _sid: str = sid,
                _rng: np.random.Generator = rng,
                _mean: float = base_cycle,
                _cv: float = cv,
                _zone: Zone = zone,
                _idx: int = i,
            ) -> float:
                assert isinstance(part, _Part)
                variant_mult = self.config.variant_zone_multiplier[part.variant][_zone]
                live_mult = self._live_multiplier[_sid]
                # Mean-1 by construction (ConditionParams), so this adds
                # spatially-correlated variation without moving any
                # station's long-run mean.
                condition_mult = self.condition.multiplier(_idx) if self.condition else 1.0
                return sample_cycle_time(
                    _rng, _mean * live_mult * variant_mult * condition_mult, _cv
                )

            def make_on_departure(_sid: str, _zone: Zone, _is_last: bool = is_last):
                # No next station to transfer to from the last one -- 0.0 is
                # the correct value there, not a shortcut (see module
                # docstring: every OTHER station carries the fixed synthetic
                # conveyor delay).
                delay = 0.0 if _is_last else CONVEYOR_TRANSFER_DELAY_S

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
                            transfer_delay_s=delay,
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
                breakdowns=config.breakdowns,
                failure_rng=self._failure_rngs[sid],
            )

        self._source_buf = buffers[config.station_ids[0]]
        self._source_process = env.process(self._source())
        if self.condition is not None:
            self._condition_process = env.process(self._drift_condition())

    def _drift_condition(self):
        """Advances the shared condition field on a fixed tick. Separate from
        any station's own process so the drift is a property of the LINE, not
        of whichever station happens to complete a unit next.
        """
        interval = self.config.condition.update_interval_s
        while True:
            yield self.env.timeout(interval)
            assert self.condition is not None
            self.condition.step()

    def _pick_variant(self) -> str:
        weights = np.array([v["weight"] for v in self.config.variants], dtype=float)
        weights /= weights.sum()
        idx = self._variant_rng.choice(len(self.config.variants), p=weights)
        return str(self.config.variants[idx]["id"])

    def _source(self):
        """Feeds units into the first station's buffer at a realistic pace,
        not instantly.

        REAL BUG, found during Phase 5: an earlier version of this method fed
        S01 as fast as its buffer would accept units -- effectively an
        infinite, instantaneous upstream supply. That made S01's input queue
        sit permanently near-full (5.99/6 measured) regardless of anything
        downstream, which is not "S01 is a bottleneck" -- it is an artifact of
        an unrealistic source. It silently corrupted TWO of Phase 5's six
        detectors: the Queue Length method always picked S01 (100% across 5
        seeds, for exactly this reason), and even the production momentary
        Active Period Method split its pick between S01 and the true
        bottleneck S17 roughly 54%/45% across 80 samples of a normal run,
        because an artificially-never-starved S01 accumulates long active
        periods just like a genuine bottleneck does.

        Fixed (Phase 5): arrivals are paced with the same lognormal sampling
        used everywhere else in this line, at the first zone's own (mean, cv)
        -- modelling an upstream supply process with its own natural
        variability, rather than an unconstrained tap.

        REAL BUG, found much later (post-Phase-10): that fix's own docstring
        already recorded that it was incomplete -- "the production momentary
        Active Period Method split its pick between S01 and the true
        bottleneck S17 roughly 54%/45%." Pacing the source at EXACTLY the
        first station's own mean cycle time (zero slack) means the arrival
        rate equals the service rate on average -- a critically loaded queue
        (utilization -> saturation) with no slack, in which S01 is
        structurally immune to starvation (it always has the paced source
        behind it) even though it CAN still be blocked. Once a separate,
        larger confound (uneven zone-to-zone base cycle times) was found and
        fixed, this smaller one -- previously masked by the larger one --
        became visible on its own: S01 won 9 of 10 detector picks in a fresh
        multi-scenario test. `ARRIVAL_SLACK_FACTOR` gives the source a modest
        margin below the line's own processing capacity, matching real
        production-line practice (a line is deliberately paced with headroom,
        not run at exactly 100% capacity) -- verified directly to remove
        S01's artifact without materially changing engineered-bottleneck
        detection elsewhere. `synthetic -- uncalibrated`, same as every other
        timing parameter in this project; what would calibrate it is the
        arrival-rate distribution of the actual upstream process (parts
        kitting, prior line segment, etc.), which this project does not have.
        """
        first_station = self.config.station_ids[0]
        arrival_mean = self.config.base_cycle_time_of[first_station] * ARRIVAL_SLACK_FACTOR
        arrival_cv = self.config.cv_of[first_station]

        while True:
            variant = self._pick_variant()
            part = _Part(unit_id=self._next_unit_id, variant=variant)
            self._next_unit_id += 1
            yield self._source_buf.put(part)
            yield self.env.timeout(sample_cycle_time(self._arrival_rng, arrival_mean, arrival_cv))

    def time_in_state(self, station_id: str) -> dict[StationState, float]:
        return dict(self.stations[station_id].time_in_state)

    def set_cycle_time_multiplier(self, station_id: str, multiplier: float) -> None:
        """Live perturbation entry point for Phase 6's control endpoint.
        Takes effect on this station's NEXT sampled cycle time -- a unit
        already mid-cycle keeps its already-sampled duration, matching how a
        real machine would finish its current cycle before a parameter
        change takes effect.
        """
        if station_id not in self._live_multiplier:
            raise KeyError(station_id)
        self._live_multiplier[station_id] = multiplier


def build_line(env: simpy.Environment, scenario_path: Path | str) -> Line:
    config = LineConfig.from_yaml(scenario_path)
    return Line(env, config)


__all__ = ["ConditionField", "ConditionParams", "Line", "LineConfig", "build_line"]
