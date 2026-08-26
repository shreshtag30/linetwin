"""Frozen data contracts for LineTwin.

FROZEN AT PHASE 2. This module is the interface every other phase codes against:
the simulation writes these, the API serializes them, the frontend parses them,
and Phase 9's defect genealogy walks `UnitEvent` records.

Changing a field here is a schema change. Bump `SCHEMA_VERSION` and say so in the
phase record -- do not silently widen a type.

Two design rules carried from the research, both load-bearing:

1.  `ValueSource` keeps OBSERVED / INFERRED / SIMULATED distinct on every value that
    could be any of them, each with confidence and freshness. A twin that presents an
    estimate as a measurement is the specific failure mode Detzner & Eigner (2018)
    warn about, and 8 of our 30 stations have no instrumentation at all.

2.  `Missingness` keeps ZERO, MISSING and NOT_APPLICABLE as three distinct states
    rather than collapsing them into one null (Detzner & Eigner 2018). "The sensor
    read 0.0", "the sensor did not report", and "this station has no such sensor"
    are different facts and must not be conflated.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# Schema + tick geometry
# ---------------------------------------------------------------------------

SCHEMA_VERSION = "0.1.0"

# SIM_DT / REAL_DT == 60 always, so "1 sim-minute ~ 1 real second" holds at every
# rate. Local: 7.5 / 0.125 -> 8 ticks/s. Hosted: 30 / 0.5 -> 2 ticks/s (what a
# throttled shared CPU can actually hold). Same code, one config value.
SIM_DT: float = 7.5
REAL_DT: float = 0.125

TICK_RATIO: float = SIM_DT / REAL_DT
assert TICK_RATIO == 60.0, "SIM_DT / REAL_DT must be 60 so the sim-minute mapping holds"


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class StationState(StrEnum):
    """Per-station state.

    The ACTIVE / INACTIVE split is Roser, Nakano & Tanaka's (2001) and drives the
    Active Period Method. DOWN, REPAIR and SETUP are ACTIVE -- a breakdown does NOT
    end an active period. This is counterintuitive and load-bearing: treating DOWN as
    inactive silently degrades APM into the utilization method.
    """

    WORKING = "working"
    DOWN = "down"
    REPAIR = "repair"
    SETUP = "setup"
    STARVED = "starved"
    BLOCKED = "blocked"
    IDLE = "idle"

    @property
    def is_active(self) -> bool:
        return self in _ACTIVE_STATES


_ACTIVE_STATES: frozenset[StationState] = frozenset(
    {
        StationState.WORKING,
        StationState.DOWN,
        StationState.REPAIR,
        StationState.SETUP,
    }
)

_INACTIVE_STATES: frozenset[StationState] = frozenset(
    {
        StationState.STARVED,
        StationState.BLOCKED,
        StationState.IDLE,
    }
)

assert set(StationState) == _ACTIVE_STATES | _INACTIVE_STATES, (
    "every StationState must be classified ACTIVE or INACTIVE for APM to be well-defined"
)
assert not (_ACTIVE_STATES & _INACTIVE_STATES), "a state cannot be both ACTIVE and INACTIVE"


class ValueSource(StrEnum):
    """Provenance of a value. Never collapse these."""

    OBSERVED = "observed"  # measured directly at an instrumented station
    INFERRED = "inferred"  # estimated by the graph layer from neighbours
    SIMULATED = "simulated"  # produced by a what-if scenario, not the live line


class Missingness(StrEnum):
    """Why a value is absent -- three distinct facts, never one null."""

    PRESENT = "present"
    ZERO = "zero"  # genuinely measured as zero
    MISSING = "missing"  # should exist, did not arrive
    NOT_APPLICABLE = "not_applicable"  # no such sensor at this station


class Zone(StrEnum):
    BODY = "body"
    PAINT = "paint"
    FINAL = "final"


# ---------------------------------------------------------------------------
# Provenance-carrying value
# ---------------------------------------------------------------------------

Confidence = Annotated[float, Field(ge=0.0, le=1.0)]


class TaggedValue(BaseModel):
    """A number that knows where it came from and how stale it is.

    `sensor_share` is an exact partition of unity from the graph influence operator
    (rows sum to 1), not a tuned decay -- which is why the UI can honestly render
    "Inferred - 61% sensor-derived" with zero tuning parameters.
    """

    model_config = ConfigDict(frozen=True)

    value: float | None = None
    source: ValueSource = ValueSource.OBSERVED
    missingness: Missingness = Missingness.PRESENT
    confidence: Confidence = 1.0
    # Seconds since the last DIRECT observation feeding this value. 0.0 when
    # observed this tick; grows while a station is dark.
    staleness_s: float = Field(default=0.0, ge=0.0)
    # Fraction of this estimate attributable to real sensor evidence. 1.0 when
    # observed. Only meaningful when source is INFERRED.
    sensor_share: Confidence | None = None


# ---------------------------------------------------------------------------
# Unit event log -- consumed by Phase 9 genealogy. Freeze carefully.
# ---------------------------------------------------------------------------


class UnitEvent(BaseModel):
    """One unit's passage through one station.

    This is the as-built record. Detzner & Eigner (2018) require the as-planned ->
    as-built -> as-maintained chain be keyed per unit, and that the Bill of Processes
    be inverted so each process is assigned to a part -- which is exactly what a
    per-unit list of these events is.

    Phase 9 walks these backwards from a defect detected at final inspection,
    realigning timestamps by cumulative transfer delay (the mechanism disclosed in
    US 12,353,197 B2) to identify the likely origin station and the affected unit
    range. Every field below is needed for that walk; do not trim.
    """

    model_config = ConfigDict(frozen=True)

    unit_id: int = Field(ge=0)
    variant: str
    station_id: str
    zone: Zone

    entered_at: float = Field(ge=0.0, description="sim seconds")
    exited_at: float = Field(ge=0.0, description="sim seconds")
    cycle_time_s: float = Field(gt=0.0)

    # Time from leaving this station to entering the next. Phase 9 sums these to
    # realign a downstream detection back onto this station's time frame.
    transfer_delay_s: float = Field(default=0.0, ge=0.0)

    state_at_exit: StationState
    # Risk assigned to the unit as it left. None before the risk layer exists
    # (Phase 8), which is why this is optional rather than defaulted to 0.0 --
    # "not scored" and "scored as zero risk" are different facts.
    risk_at_exit: float | None = Field(default=None, ge=0.0, le=1.0)


# ---------------------------------------------------------------------------
# Live snapshot
# ---------------------------------------------------------------------------


class StationSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True)

    station_id: str
    zone: Zone
    instrumented: bool

    state: StationState
    queue_depth: int = Field(ge=0)
    buffer_capacity: int = Field(ge=0)

    # Measured directly from the running simulation.
    cycle_time_s: TaggedValue
    throughput_uph: float = Field(ge=0.0)
    units_completed: int = Field(ge=0)

    # Computed by a model FROM those measurements. The distinction is volunteered
    # in the payload, not buried in the README.
    defect_risk: TaggedValue | None = None
    # Top-2 TreeSHAP contributors. ASSOCIATIVE, NOT CAUSAL -- the brief itself notes
    # these causes are hard to isolate from data alone.
    risk_drivers: list[RiskDriver] = Field(default_factory=list)
    risk_updated_tick: int | None = None

    # Fraction of time in each state since run start; APM reads this.
    time_in_state: dict[StationState, float] = Field(default_factory=dict)


class RiskDriver(BaseModel):
    model_config = ConfigDict(frozen=True)

    feature: str
    contribution: float
    # Always rendered with this qualifier attached.
    relation: Literal["associative"] = "associative"


class BottleneckVerdict(BaseModel):
    """Result of the diagnostic layer.

    `station_id` is None when no station is identified. `confidence` carries the
    Phase 5 significance annotation rather than suppressing the verdict outright:
    consecutive active periods at a blocking station are autocorrelated, so ANOVA's
    independence assumption does not strictly hold, and a hard suppression gate would
    also delay detection past the 2 s live-response requirement. Annotating is
    honest about both facts; see docs/LIMITATIONS.md.
    """

    model_config = ConfigDict(frozen=True)

    station_id: str | None
    method: str = "active_period_momentary"
    confidence: Literal["established", "provisional", "none"] = "provisional"
    p_value: float | None = None
    runner_up_id: str | None = None
    # Why it is the bottleneck: e.g. {"working": 0.59, "repair": 0.41}
    mode_decomposition: dict[str, float] = Field(default_factory=dict)
    # Operator-facing sentence (Bottleneck Walk phrasing).
    explanation: str = ""


class RunMeta(BaseModel):
    """Sent once at stream open, before any snapshot."""

    model_config = ConfigDict(frozen=True)

    schema_version: str = SCHEMA_VERSION
    run_id: str
    seed: int
    scenario: str
    station_count: int
    instrumented_count: int
    sim_dt: float = SIM_DT
    real_dt: float = REAL_DT
    started_at_unix: float


class Snapshot(BaseModel):
    """Full line state for one tick. Emitted whole every tick, never as a delta:
    a dropped frame desyncs the UI, whereas full snapshots self-heal on reconnect.
    """

    model_config = ConfigDict(frozen=True)

    schema_version: str = SCHEMA_VERSION
    # Monotonic, and the conflation bus emits only when this changes.
    seq: int = Field(ge=0)
    tick: int = Field(ge=0)
    # Invariant asserted in tests: sim_time_s == tick * SIM_DT exactly.
    sim_time_s: float = Field(ge=0.0)

    status: Literal["running", "paused", "faulted"] = "running"
    fault_detail: str | None = None

    stations: list[StationSnapshot]
    bottleneck: BottleneckVerdict | None = None
    predicted_bottleneck: BottleneckVerdict | None = None

    line_throughput_uph: float = Field(default=0.0, ge=0.0)
    wip: int = Field(default=0, ge=0)

    # Liveness instrumentation, measured not asserted.
    real_time_factor: float = Field(default=1.0, ge=0.0)
    lag_s: float = 0.0
    tick_compute_ms: float = Field(default=0.0, ge=0.0)


# ---------------------------------------------------------------------------
# Control
# ---------------------------------------------------------------------------


class ControlCommand(BaseModel):
    """Live parameter change. Drained at the top of a tick, before env.run()."""

    model_config = ConfigDict(frozen=True)

    station_id: str
    # Bounded deliberately: the API must 422 on out-of-range rather than accept a
    # value that would freeze the line.
    cycle_time_multiplier: float = Field(ge=0.1, le=10.0)


class ControlAck(BaseModel):
    model_config = ConfigDict(frozen=True)

    accepted: bool
    station_id: str
    cycle_time_multiplier: float
    applied_at_tick: int
    schema_version: str = SCHEMA_VERSION


# Resolve the forward reference from StationSnapshot to RiskDriver.
StationSnapshot.model_rebuild()

__all__ = [
    "REAL_DT",
    "SCHEMA_VERSION",
    "SIM_DT",
    "BottleneckVerdict",
    "Confidence",
    "ControlAck",
    "ControlCommand",
    "Missingness",
    "RiskDriver",
    "RunMeta",
    "Snapshot",
    "StationSnapshot",
    "StationState",
    "TaggedValue",
    "UnitEvent",
    "ValueSource",
    "Zone",
]
