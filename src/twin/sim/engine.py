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
    BottleneckVerdict,
    ControlCommand,
    Missingness,
    RiskDriver,
    RunMeta,
    Snapshot,
    StationSnapshot,
    TaggedValue,
    ValueSource,
)
from twin.diagnostic.bottleneck import StationView, diagnose
from twin.diagnostic.rolling_horizon import fork_and_predict
from twin.graph.inference import InferenceResult, harmonic_extension
from twin.graph.placement import greedy_sensor_placement
from twin.risk.features import FeatureExtractor
from twin.risk.scorer import ModelNotTrainedError, StationRiskScorer
from twin.sim.line import Line, LineConfig

RELAG_TICKS = 5  # ticks of lag before re-anchoring rather than trying to catch up
RISK_SCORE_HZ = 1.0  # docs/DATA.md / phase-08 plan: Model B runs at 1Hz regardless of tick rate
# Rolling-horizon prediction (diagnostic/rolling_horizon.py), run as a
# background task the tick loop never awaits -- see the REAL BUG note beside
# `_prediction_task` below for why. Checked at this cadence for whether the
# PREVIOUS forecast has finished and a new one should be started.
PREDICTION_HZ = 1.0
PREDICTION_HORIZON_S = 1800.0  # 30 sim-minutes forward, Ragazzini et al.'s tuning parameter T


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

        # Optional: Model B needs ml/models/ populated (tools/generate_training_
        # data.py then tools/train_station_risk.py). Absence is not fatal --
        # the twin still runs fully without live risk scoring, it just omits
        # `defect_risk`/`risk_drivers` from every snapshot (already their
        # documented defaults in contracts.py).
        try:
            self._risk_scorer: StationRiskScorer | None = StationRiskScorer()
        except ModelNotTrainedError:
            self._risk_scorer = None

        self._ticks_per_risk_score = max(1, round(1.0 / REAL_DT / RISK_SCORE_HZ))
        self._ticks_per_prediction = max(1, round(1.0 / REAL_DT / PREDICTION_HZ))
        self._reset(initial=True)

    def _reset(self, *, initial: bool = False) -> None:
        config = LineConfig.from_yaml(self._scenario_path)
        if self._seed_override is not None:
            config.seed = self._seed_override
        self.config = config
        self.env = simpy.Environment()
        self.line = Line(self.env, config)
        self._feature_extractor = FeatureExtractor(
            config.station_ids, config.buffer_capacity_of, sample_dt=SIM_DT
        )
        # (TaggedValue, drivers, tick computed) per station -- refreshed only
        # every `_ticks_per_risk_score` ticks; a snapshot in between reuses
        # the last computed value, with `risk_updated_tick` showing its age.
        self._risk_cache: dict[str, tuple[TaggedValue, list[RiskDriver], int]] = {}
        self._last_inference: dict[str, InferenceResult] = {}
        self._predicted_bottleneck: BottleneckVerdict | None = None
        # Deliberately NOT awaited inline in the tick loop -- see the REAL BUG
        # note where this task is created. A restart must not let a forecast
        # started under the OLD run_id overwrite state under the new one;
        # `_run_prediction` checks `run_id` before assigning, so an in-flight
        # task is left to finish and self-discard rather than cancelled (a
        # `to_thread` call cannot be interrupted mid-computation anyway).
        self._prediction_task: asyncio.Task[None] | None = None
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

            self._feature_extractor.sample_tick(self.line.stations)
            if self._risk_scorer is not None and self.tick % self._ticks_per_risk_score == 0:
                self._update_risk_scores()
            self._compute_inference()
            if (
                self.tick % self._ticks_per_prediction == 0
                and (self._prediction_task is None or self._prediction_task.done())
            ):
                # REAL BUG, found by running the tick-timing test: awaiting
                # `fork_and_predict` inline -- even via `asyncio.to_thread` --
                # measured at 5-10ms once the line had settled into steady-
                # state congestion, but ~700ms EARLY in a run, when queues are
                # near-empty and 1800 forecast sim-seconds of mostly WORKING
                # stations means simpy actually processes far more discrete
                # events than the same wall-clock horizon does once stations
                # are mostly BLOCKED/STARVED (few events, because nothing is
                # happening). Awaiting even the threaded call inline still
                # delays THIS tick's own publish by however long the fork
                # takes, which is exactly what blew real_time_factor down to
                # ~0.6-0.7 -- to_thread only protects OTHER coroutines
                # (HTTP handlers) from blocking, not the tick loop that is
                # itself awaiting it.
                #
                # Fixed: launch as a fire-and-forget background task the tick
                # loop never awaits, gated so at most one runs at a time.
                # `fork_and_predict` reads `self.line`'s station attributes
                # while the tick loop concurrently mutates them in later
                # ticks -- accepted deliberately: this is an advisory
                # forecast display, plain-attribute reads are GIL-serialized
                # at the bytecode level so this cannot corrupt anything, and
                # a torn read at worst yields a forecast one tick staler than
                # claimed, never a wrong simulation result.
                self._prediction_task = asyncio.create_task(
                    self._run_prediction(self.run_id, self.line)
                )

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

    async def _run_prediction(self, run_id: str, line: Line) -> None:
        """Background task body -- see the REAL BUG note at the call site.
        `run_id` is captured at task creation so a forecast from a run that
        has since been restarted over cannot overwrite the new run's state
        once it finally finishes.
        """
        result = await asyncio.to_thread(fork_and_predict, line, horizon_s=PREDICTION_HORIZON_S)
        if run_id == self.run_id:
            self._predicted_bottleneck = result

    def _update_risk_scores(self) -> None:
        """Model B at ~1Hz (RISK_SCORE_HZ), regardless of tick rate. Called
        directly, not via `asyncio.to_thread`: a 5-feature XGBoost + isotonic
        predict is a microsecond-scale operation, where `to_thread`'s own
        dispatch overhead would dominate and thread offload would be the
        wrong call -- see engine.py's `diagnose()` call above for the
        opposite case (Phase 7), where offloading was necessary because that
        computation's cost is unbounded.

        Stations are scored in LINE ORDER, not station_ids' incidental order
        (they are the same here, but this is asserted, not assumed): each
        station's `upstream_risk_ewma` feature must reflect its immediate
        upstream neighbor's score from the SAME scoring pass, not a stale one.
        """
        assert self._risk_scorer is not None
        for i, sid in enumerate(self.config.station_ids):
            upstream = self.config.station_ids[i - 1] if i > 0 else None
            feats = self._feature_extractor.features_for(sid, self.line.stations[sid], upstream)
            tagged, drivers = self._risk_scorer.score(feats)
            self._feature_extractor.update_risk_ewma(sid, tagged.value)
            self._risk_cache[sid] = (tagged, drivers, self.tick)

    def _observed_and_prior_cycle_times(self) -> tuple[dict[str, float], dict[str, float]]:
        """Shared by `_compute_inference` (every tick) and
        `sensor_placement_ranking` (on demand, Phase 10's leadership view) --
        one definition of "what does the graph layer currently know" rather
        than two that could silently drift apart.

        Uses each instrumented station's last completed cycle time, or its
        own zone's base cycle time as a fallback before that station has
        completed anything yet -- a station that has not produced a real
        reading is not meaningfully different from one with no sensor at
        all, for this one tick.
        """
        observed: dict[str, float] = {}
        prior: dict[str, float] = {}
        for sid in self.config.station_ids:
            zone_base = self.config.base_cycle_time_of[sid]
            if sid in self.config.instrumented_stations:
                station = self.line.stations[sid]
                observed[sid] = station.last_cycle_time_s or zone_base
            else:
                prior[sid] = zone_base
        return observed, prior

    def sensor_placement_ranking(self, budget: int) -> list[str]:
        """Which currently-dark stations would most improve inference
        coverage if instrumented next, per Phase 9's greedy placement
        (graph/placement.py) -- exposed for the leadership view's
        instrumentation-required-vs-recommended panel.
        """
        observed, prior = self._observed_and_prior_cycle_times()
        return greedy_sensor_placement(
            self.config.station_ids, self.config.dark_stations, observed, prior, budget
        )

    def _compute_inference(self) -> None:
        """Harmonic extension over the 8 uninstrumented stations, run once
        per tick (a 30x30 linear solve; measured well under a millisecond,
        no `to_thread` needed -- see the Tukey-Kramer / risk-scoring
        precedents above for when that call is and isn't warranted).
        """
        observed, prior = self._observed_and_prior_cycle_times()

        results = harmonic_extension(
            self.config.station_ids,
            self.config.dark_stations,
            observed,
            prior,
        )
        self._last_inference = {r.station_id: r for r in results}

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
            inferred = self._last_inference.get(station_id)
            if inferred is not None:
                cycle_time = TaggedValue(
                    value=inferred.value,
                    source=ValueSource.INFERRED,
                    missingness=Missingness.PRESENT,
                    # sensor_share IS the confidence, directly -- it is
                    # already an honest [0,1] measure with zero tuning
                    # parameters (graph/inference.py's module docstring);
                    # inventing a separate rescaled "confidence" would just
                    # add an unexplained transform on top of an already-exact
                    # quantity.
                    confidence=inferred.sensor_share,
                    staleness_s=0.0,
                    sensor_share=inferred.sensor_share,
                )
            else:
                # Only reachable before the first inference pass has run at
                # all (e.g. the very first tick) -- honest MISSING, not a
                # fabricated placeholder, for that brief window.
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

        cached = self._risk_cache.get(station_id)
        defect_risk = cached[0] if cached else None
        risk_drivers = cached[1] if cached else []
        risk_updated_tick = cached[2] if cached else None

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
            defect_risk=defect_risk,
            risk_drivers=risk_drivers,
            risk_updated_tick=risk_updated_tick,
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
            predicted_bottleneck=self._predicted_bottleneck,
            line_throughput_uph=line_throughput,
            wip=wip,
            real_time_factor=real_time_factor,
            lag_s=lag_s,
            tick_compute_ms=tick_compute_ms,
        )
        await self.bus.publish(snapshot)


__all__ = ["ConflationBus", "Engine"]
