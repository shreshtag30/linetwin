"""Engine mechanics: tick timing, control, restart, fault handling, and the
single-slot conflation bus -- no HTTP involved.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest

from twin.contracts import REAL_DT, SIM_DT, BottleneckVerdict, ControlCommand
from twin.sim.engine import ConflationBus, Engine

SCENARIO = Path(__file__).resolve().parents[1] / "scenarios" / "line30.yaml"


async def _run_briefly(engine: Engine, seconds: float) -> None:
    task = asyncio.create_task(engine.run())
    await asyncio.sleep(seconds)
    engine.stop()
    await task


@pytest.mark.asyncio
async def test_sim_time_equals_tick_times_sim_dt_exactly() -> None:
    engine = Engine(SCENARIO, seed=1)
    await _run_briefly(engine, 1.0)
    snap = engine.bus.latest
    assert snap is not None
    assert snap.sim_time_s == pytest.approx(snap.tick * SIM_DT, abs=1e-9)


@pytest.mark.asyncio
async def test_real_time_factor_converges_near_one() -> None:
    engine = Engine(SCENARIO, seed=1)
    await _run_briefly(engine, 1.5)
    snap = engine.bus.latest
    assert snap is not None
    assert snap.real_time_factor == pytest.approx(1.0, abs=0.1)
    assert snap.lag_s < 5 * REAL_DT


@pytest.mark.asyncio
async def test_tick_and_seq_are_strictly_monotonic_across_publications() -> None:
    engine = Engine(SCENARIO, seed=1)
    seen_ticks: list[int] = []

    async def collector():
        last_seq = -1
        for _ in range(10):
            snap = await engine.bus.wait_for_next(last_seq)
            last_seq = snap.seq
            seen_ticks.append(snap.tick)

    task = asyncio.create_task(engine.run())
    await collector()
    engine.stop()
    await task

    assert seen_ticks == sorted(seen_ticks)
    assert len(set(seen_ticks)) == len(seen_ticks)


@pytest.mark.asyncio
async def test_control_command_changes_live_multiplier() -> None:
    engine = Engine(SCENARIO, seed=1)
    task = asyncio.create_task(engine.run())
    await asyncio.sleep(0.3)

    assert engine.line._live_multiplier["S05"] == 1.0
    await engine.control_queue.put(ControlCommand(station_id="S05", cycle_time_multiplier=4.0))
    await asyncio.sleep(0.3)

    assert engine.line._live_multiplier["S05"] == 4.0
    engine.stop()
    await task


@pytest.mark.asyncio
async def test_restart_resets_tick_run_id_and_live_multipliers() -> None:
    engine = Engine(SCENARIO, seed=1)
    task = asyncio.create_task(engine.run())
    await asyncio.sleep(0.3)

    old_run_id = engine.run_id
    await engine.control_queue.put(ControlCommand(station_id="S05", cycle_time_multiplier=4.0))
    await asyncio.sleep(0.2)
    assert engine.line._live_multiplier["S05"] == 4.0

    engine.request_restart()
    await asyncio.sleep(0.2)

    assert engine.run_id != old_run_id
    assert engine.tick < 5  # freshly restarted, not picking up where the old run left off
    assert engine.line._live_multiplier["S05"] == 1.0

    engine.stop()
    await task


@pytest.mark.asyncio
async def test_a_faulted_env_degrades_to_a_fault_frame_not_a_crash(monkeypatch) -> None:
    engine = Engine(SCENARIO, seed=1)

    def _boom(*_args, **_kwargs):
        raise RuntimeError("synthetic simulation fault")

    monkeypatch.setattr(engine.env, "run", _boom)

    task = asyncio.create_task(engine.run())
    await asyncio.sleep(0.3)

    snap = engine.bus.latest
    assert snap is not None
    assert snap.status == "faulted"
    assert "synthetic simulation fault" in (snap.fault_detail or "")

    engine.stop()
    await task  # must not raise -- the fault must not propagate out of run()


@pytest.mark.asyncio
async def test_restart_recovers_a_faulted_engine(monkeypatch) -> None:
    engine = Engine(SCENARIO, seed=1)

    call_count = 0
    real_run = engine.env.run

    def _boom_once(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("one-time synthetic fault")
        return real_run(*args, **kwargs)

    monkeypatch.setattr(engine.env, "run", _boom_once)

    task = asyncio.create_task(engine.run())
    await asyncio.sleep(0.2)
    assert engine.bus.latest is not None
    assert engine.bus.latest.status == "faulted"

    engine.request_restart()
    await asyncio.sleep(0.3)

    assert engine.bus.latest is not None
    assert engine.bus.latest.status == "running"

    engine.stop()
    await task


@pytest.mark.asyncio
async def test_dark_stations_report_inferred_with_an_honest_confidence() -> None:
    """Superseded by Phase 9: before the harmonic-extension inference layer
    existed, a dark station's cycle_time_s was honestly reported MISSING
    (never a plausible-looking fabricated number). Now that Phase 9's
    graph/inference.py is wired in, the correct behavior is INFERRED with
    `sensor_share` as the honest, zero-tuning-parameter confidence -- still
    never presented as OBSERVED, and never a bare number with no provenance.
    """
    engine = Engine(SCENARIO, seed=1)
    await _run_briefly(engine, 0.5)
    snap = engine.bus.latest
    assert snap is not None

    dark = [s for s in snap.stations if not s.instrumented]
    assert len(dark) == 8
    for station in dark:
        assert station.cycle_time_s.value is not None
        assert station.cycle_time_s.source.value == "inferred"
        assert station.cycle_time_s.missingness.value == "present"
        assert 0.0 <= station.cycle_time_s.confidence <= 1.0
        assert station.cycle_time_s.sensor_share == station.cycle_time_s.confidence


class TestConflationBus:
    @pytest.mark.asyncio
    async def test_wait_for_next_blocks_until_a_newer_seq_is_published(self) -> None:
        bus = ConflationBus()
        from twin.contracts import Snapshot

        async def publisher():
            await asyncio.sleep(0.05)
            await bus.publish(Snapshot(seq=1, tick=1, sim_time_s=7.5, stations=[]))

        task = asyncio.create_task(publisher())
        snap = await bus.wait_for_next(last_seq=0)
        await task
        assert snap.seq == 1

    @pytest.mark.asyncio
    async def test_multiple_waiters_each_get_the_same_latest_snapshot(self) -> None:
        bus = ConflationBus()
        from twin.contracts import Snapshot

        async def publisher():
            await asyncio.sleep(0.05)
            await bus.publish(Snapshot(seq=1, tick=1, sim_time_s=7.5, stations=[]))

        results = []

        async def waiter():
            snap = await bus.wait_for_next(last_seq=0)
            results.append(snap.seq)

        await asyncio.gather(publisher(), waiter(), waiter(), waiter())
        assert results == [1, 1, 1]

    @pytest.mark.asyncio
    async def test_a_slow_waiter_never_blocks_publish(self) -> None:
        """The whole point of a conflation bus: publish must never await a
        consumer. A waiter that never calls wait_for_next at all must not
        prevent new snapshots from being published.
        """
        bus = ConflationBus()
        from twin.contracts import Snapshot

        for seq in range(1, 6):
            await asyncio.wait_for(
                bus.publish(Snapshot(seq=seq, tick=seq, sim_time_s=seq * SIM_DT, stations=[])),
                timeout=0.5,
            )
        assert bus.latest is not None
        assert bus.latest.seq == 5


@pytest.mark.asyncio
async def test_a_slow_diagnose_does_not_block_the_event_loop(monkeypatch) -> None:
    """Regression test for the real deadlock this phase found: an extreme
    perturbation makes diagnostic/bottleneck.py's Tukey-Kramer computation
    pathologically slow (confirmed via a faulthandler stack dump against a
    hung server -- see docs/phases/phase-07-floor-supervisor.md), and because
    it used to run synchronously inside the tick loop, it blocked the entire
    single-threaded event loop for as long as it took: every other coroutine,
    including HTTP request handlers, starved completely.

    This test does not need scipy's actual pathology to prove the fix --
    it needs to prove the ARCHITECTURE is right: that a slow, synchronous
    `diagnose()` call cannot stall unrelated concurrent work. A "canary"
    coroutine increments a counter on a tight timer throughout the test;
    if `engine.py` still called `diagnose()` directly instead of via
    `asyncio.to_thread`, this canary would stall for the full 0.4s the fake
    diagnose blocks for, and the assertion below would fail.
    """
    import twin.sim.engine as engine_module

    def _slow_diagnose(_views):
        time.sleep(0.4)  # synchronous, CPU-bound-style blocking -- the actual failure mode
        return BottleneckVerdict(station_id=None, confidence="none", explanation="fake")

    monkeypatch.setattr(engine_module, "diagnose", _slow_diagnose)

    canary_ticks = 0
    canary_running = True

    async def canary():
        nonlocal canary_ticks
        while canary_running:
            await asyncio.sleep(0.01)
            canary_ticks += 1

    engine = Engine(SCENARIO, seed=1)
    canary_task = asyncio.create_task(canary())
    engine_task = asyncio.create_task(engine.run())

    await asyncio.sleep(0.6)  # long enough to span at least one slow diagnose() call

    canary_running = False
    engine.stop()
    await engine_task
    await canary_task

    # If the event loop had been blocked for the 0.4s the fake diagnose sleeps,
    # the canary -- ticking every 10ms -- would have accumulated far fewer
    # increments than the ~60 expected over 0.6s. A generous floor (30) leaves
    # headroom for CI scheduling noise while still failing hard on a genuine
    # regression back to a blocking call.
    assert canary_ticks > 30, (
        f"canary only ticked {canary_ticks} times in 0.6s -- the event loop was blocked, "
        "meaning diagnose() is no longer running in a thread"
    )


@pytest.mark.asyncio
async def test_risk_scores_populate_at_the_configured_cadence() -> None:
    from twin.risk.scorer import MODELS_DIR

    if not (MODELS_DIR / "station_risk_booster.json").exists():
        pytest.skip("ml/models/ not populated -- run tools/train_station_risk.py first")

    engine = Engine(SCENARIO, seed=1)
    assert engine._risk_scorer is not None
    await _run_briefly(engine, 2.0)

    snap = engine.bus.latest
    assert snap is not None
    for station in snap.stations:
        assert station.defect_risk is not None
        assert 0.0 <= station.defect_risk.value <= 1.0
        assert len(station.risk_drivers) == 2
        assert station.risk_updated_tick is not None
        # Refreshed only every `_ticks_per_risk_score` ticks -- must be
        # recent, but not necessarily equal to the current tick.
        assert station.risk_updated_tick <= snap.tick
        assert snap.tick - station.risk_updated_tick < engine._ticks_per_risk_score


@pytest.mark.asyncio
async def test_predicted_bottleneck_populates_without_starving_the_tick_loop() -> None:
    """Regression test for a real bug: an earlier version awaited
    `fork_and_predict` inline in the tick loop (even via `asyncio.to_thread`),
    which measured up to ~700ms early in a run (near-empty queues mean far
    more discrete events over the same forecast horizon than once the line
    settles into steady congestion) -- collapsing real_time_factor to ~0.6-0.7
    because the tick loop itself was waiting on it, not just other coroutines.
    Fixed by launching it as a background task the tick loop never awaits.
    """
    engine = Engine(SCENARIO, seed=1)
    task = asyncio.create_task(engine.run())

    for _ in range(100):
        if engine._predicted_bottleneck is not None:
            break
        await asyncio.sleep(0.05)
    assert engine._predicted_bottleneck is not None

    snap = engine.bus.latest
    assert snap is not None
    assert snap.real_time_factor > 0.85

    engine.stop()
    await task
    if engine._prediction_task is not None:
        await engine._prediction_task


@pytest.mark.asyncio
async def test_sensor_placement_ranking_only_picks_dark_stations() -> None:
    engine = Engine(SCENARIO, seed=1)
    await _run_briefly(engine, 0.3)

    ranking = engine.sensor_placement_ranking(budget=4)
    assert len(ranking) == 4
    assert set(ranking) <= engine.config.dark_stations
    assert len(set(ranking)) == 4
