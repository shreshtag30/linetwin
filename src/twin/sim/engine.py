"""Real-time-paced asyncio engine: one tick loop, one conflation bus.

Owns exactly one `simpy.Environment` + `Line`, advanced by `env.run(until=tick *
SIM_DT)` from a single asyncio task -- never `simpy.rt.RealtimeEnvironment`
(its `step()` blocking-sleeps on its own thread and cannot service a control
channel), and never `while env.peek() < until: env.step()` (that leaves
`env.now` at the last event time, not exactly `until` -- `env.run(until=T)`
schedules an URGENT sentinel at T instead, so `env.now == T` exactly).

Absolute-deadline anchored sleep: a requested `asyncio.sleep(dt)` rounds UP to
the next OS timer quantum regardless of `dt`, so naive per-tick sleeping drifts
slow. Anchoring every sleep on `t0 + tick * REAL_DT` self-corrects each
overshoot instead of compounding it. If lag ever exceeds 5 ticks (a slow
tick, a debugger pause, a laptop sleep), catching up by sleeping "negative"
would spin the loop hot forever -- so the anchor is reset instead, and the
reported `real_time_factor` is measured, not asserted.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
import uuid
from pathlib import Path

import simpy

from twin.contracts import (
    REAL_DT,
    SIM_DT,
    ControlCommand,
    Missingness,
    RunMeta,
    Snapshot,
    StationSnapshot,
    TaggedValue,
    ValueSource,
)
from twin.diagnostic.bottleneck import StationView, diagnose
from twin.sim.line import Line, LineConfig

RELAG_TICKS = 5  # ticks of lag before re-anchoring rather than trying to catch up


class ConflationBus:
    """Single-slot pub/sub: only the latest snapshot matters. A slow or dead
    SSE consumer can never stall the simulation clock, because publishing
    never waits on a consumer -- it just replaces `latest` and notifies.
    """

    def __init__(self) -> None:
        self._condition = asyncio.Condition()
        self.latest: Snapshot | None = None

    async def publish(self, snapshot: Snapshot) -> None:
        async with self._condition:
            self.latest = snapshot
            self._condition.notify_all()

    async def wait_for_next(self, last_seq: int) -> Snapshot:
        """Blocks until a snapshot with seq > last_seq is available, then
        returns it. Each caller tracks its own `last_seq`, so multiple
        independent SSE consumers can each pull at their own pace without
        interfering with one another or with the tick loop.
        """
        async with self._condition:
            await self._condition.wait_for(
                lambda: self.latest is not None and self.latest.seq > last_seq
            )
            assert self.latest is not None
            return self.latest


class Engine:
    def __init__(self, scenario_path: Path | str, *, seed: int | None = None) -> None:
        self._scenario_path = Path(scenario_path)
        self._seed_override = seed
        self.bus = ConflationBus()
        self.control_queue: asyncio.Queue[ControlCommand] = asyncio.Queue()
        self._restart_requested = False
        self._stopped = False
        self.run_meta: RunMeta | None = None
        self._reset(initial=True)

    def _reset(self, *, initial: bool = False) -> None:
        config = LineConfig.from_yaml(self._scenario_path)
        if self._seed_override is not None:
            config.seed = self._seed_override
        self.config = config
        self.env = simpy.Environment()
        self.line = Line(self.env, config)
        self.tick = 0
        self.seq = 0
        self.status: str = "running"
        self.fault_detail: str | None = None
        self.run_id = str(uuid.uuid4())
        self._t0: float | None = None  # set on first loop iteration (needs a running loop)
        self.run_meta = RunMeta(
            run_id=self.run_id,
            seed=config.seed,
            scenario=config.name,
            station_count=len(config.station_ids),
            instrumented_count=len(config.instrumented_stations),
            started_at_unix=time.time(),
        )
        if not initial:
            # Draining stale commands from a previous run's queue -- a control
            # message aimed at the old line makes no sense against a fresh one.
            while not self.control_queue.empty():
                self.control_queue.get_nowait()

    def request_restart(self) -> None:
        """Called from the API layer. Safe to call at any time: asyncio is
        single-threaded/cooperative, so this flag can only be observed by the
        run loop between awaits, never mid-tick.
        """
        self._restart_requested = True

    def stop(self) -> None:
        self._stopped = True

    def _drain_control_queue(self) -> None:
        while not self.control_queue.empty():
            cmd = self.control_queue.get_nowait()
            # The API layer already validates station_id exists (404
            # otherwise) before enqueueing, so KeyError should not happen in
            # practice -- but a queued command outliving a restart is a real
            # race, and dropping it silently is the safe behavior.
            with contextlib.suppress(KeyError):
                self.line.set_cycle_time_multiplier(cmd.station_id, cmd.cycle_time_multiplier)

    async def run(self) -> None:
        """The one asyncio task that owns the simulation clock. Runs until
        `stop()` is called.
        """
        loop = asyncio.get_running_loop()
        self._t0 = loop.time()

        while not self._stopped:
            if self._restart_requested:
                self._reset()
                self._t0 = loop.time()
                self._restart_requested = False

            self._drain_control_queue()

            if self.status == "faulted":
                # Keep serving the fault frame at a slow, non-spinning cadence
                # rather than busy-looping; a restart is still honored above.
                await asyncio.sleep(REAL_DT)
                await self._publish_snapshot(lag_s=0.0, tick_compute_ms=0.0, real_time_factor=0.0)
                continue

            self.tick += 1
            compute_start = loop.time()
            # A faulted sim must degrade to a fault frame, not crash the process.
            try:
                self.env.run(until=self.tick * SIM_DT)
            except Exception as exc:
                self.status = "faulted"
                self.fault_detail = f"{type(exc).__name__}: {exc}"
                await self._publish_snapshot(lag_s=0.0, tick_compute_ms=0.0, real_time_factor=0.0)
                continue
            tick_compute_ms = (loop.time() - compute_start) * 1000.0

            assert self._t0 is not None
            target = self._t0 + self.tick * REAL_DT
            now = loop.time()
            lag_s = now - target

            if lag_s > RELAG_TICKS * REAL_DT:
                # Too far behind to catch up without spinning hot -- re-anchor
                # so the reported factor reflects "on pace from here", not an
                # ever-growing deficit from one bad tick.
                self._t0 = now - self.tick * REAL_DT
                lag_s = 0.0
            else:
                sleep_for = target - now
                if sleep_for > 0:
                    await asyncio.sleep(sleep_for)

            elapsed_wall = loop.time() - self._t0
            real_time_factor = (self.tick * REAL_DT) / elapsed_wall if elapsed_wall > 0 else 1.0

            await self._publish_snapshot(
                lag_s=max(lag_s, 0.0),
                tick_compute_ms=tick_compute_ms,
                real_time_factor=real_time_factor,
            )

    def _station_snapshot(self, station_id: str) -> StationSnapshot:
        station = self.line.stations[station_id]
        instrumented = station_id in self.config.instrumented_stations

        if instrumented:
            has_value = station.last_cycle_time_s is not None
            cycle_time = TaggedValue(
                value=station.last_cycle_time_s,
                source=ValueSource.OBSERVED,
                missingness=Missingness.PRESENT if has_value else Missingness.MISSING,
                confidence=1.0,
                staleness_s=0.0,
                sensor_share=None,
            )
        else:
            # Honest, not fabricated: Phase 9 has not been built yet, so there
            # is no inference model to produce an estimate. Reporting a
            # plausible-looking number here -- even the simulation's own true
            # value -- would defeat the entire premise of a sensor gap. Until
            # Phase 9's harmonic extension exists, a dark station's value is
            # simply MISSING, not INFERRED.
            cycle_time = TaggedValue(
                value=None,
                source=ValueSource.OBSERVED,
                missingness=Missingness.MISSING,
                confidence=0.0,
                staleness_s=self.env.now,
                sensor_share=None,
            )

        total_time = sum(station.time_in_state.values())
        throughput = (3600.0 / station.last_cycle_time_s) if station.last_cycle_time_s else 0.0

        return StationSnapshot(
            station_id=station_id,
            zone=self.config.zone_of[station_id],
            instrumented=instrumented,
            state=station.state,
            queue_depth=len(station.in_buf.items),
            buffer_capacity=self.config.buffer_capacity_of[station_id],
            cycle_time_s=cycle_time,
            throughput_uph=throughput,
            units_completed=station.units_completed,
            defect_risk=None,  # Phase 8
            risk_drivers=[],  # Phase 8
            risk_updated_tick=None,  # Phase 8
            time_in_state=(
                {s: t / total_time for s, t in station.time_in_state.items()} if total_time else {}
            ),
        )

    async def _publish_snapshot(
        self, *, lag_s: float, tick_compute_ms: float, real_time_factor: float
    ) -> None:
        self.seq += 1
        station_ids = self.config.station_ids
        stations = [self._station_snapshot(sid) for sid in station_ids]
        views = [StationView.from_station(self.line.stations[sid]) for sid in station_ids]

        # REAL BUG, found via a py-spy-style faulthandler stack dump against a
        # server hung at 100% CPU (Phase 7): under an extreme perturbation
        # (a 9x multiplier, tested by hand), the active-period distributions
        # for different stations become extremely separated, and
        # statsmodels' pairwise_tukeyhsd computes its p-value via scipy's
        # studentized-range survival function -- adaptive numerical
        # integration (scipy.integrate.quad) that becomes pathologically slow
        # for extreme, widely-separated inputs. That is synchronous,
        # CPU-bound code with no await points, so it blocked the ENTIRE
        # asyncio event loop -- including all HTTP handling -- for as long as
        # the integration ran. Reproduced directly: heavy perturbation +
        # closing/reopening the SSE stream deadlocked the whole server, every
        # request hanging indefinitely, confirmed via a real stack trace
        # rather than assumed.
        #
        # `asyncio.to_thread` is the right tool here specifically because
        # this computation's cost is unbounded and can genuinely reach
        # multiple seconds -- unlike Phase 8's live risk-scoring inference
        # (a ~microsecond XGBoost predict, where to_thread's own dispatch
        # overhead would dominate and thread offload is the wrong call).
        # Offloading here does not make the computation itself faster; it
        # keeps the event loop -- and therefore every other request --
        # responsive while it runs.
        bottleneck = await asyncio.to_thread(diagnose, views)

        throughputs = [s.throughput_uph for s in stations]
        line_throughput = sum(throughputs) / len(throughputs) if throughputs else 0.0
        wip = sum(s.queue_depth for s in stations)

        snapshot = Snapshot(
            seq=self.seq,
            tick=self.tick,
            sim_time_s=self.tick * SIM_DT,
            status=self.status,  # type: ignore[arg-type]
            fault_detail=self.fault_detail,
            stations=stations,
            bottleneck=bottleneck,
            predicted_bottleneck=None,  # Phase 8
            line_throughput_uph=line_throughput,
            wip=wip,
            real_time_factor=real_time_factor,
            lag_s=lag_s,
            tick_compute_ms=tick_compute_ms,
        )
        await self.bus.publish(snapshot)


__all__ = ["ConflationBus", "Engine"]
