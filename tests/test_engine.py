"""Engine mechanics: tick timing, control, restart, fault handling, and the
single-slot conflation bus -- no HTTP involved.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from twin.contracts import REAL_DT, SIM_DT, ControlCommand
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
async def test_dark_stations_report_missing_not_a_fabricated_estimate() -> None:
    """Phase 9 has not been built yet -- a dark station's cycle_time_s must
    honestly be MISSING, never a plausible-looking invented number.
    """
    engine = Engine(SCENARIO, seed=1)
    await _run_briefly(engine, 0.5)
    snap = engine.bus.latest
    assert snap is not None

    dark = [s for s in snap.stations if not s.instrumented]
    assert len(dark) == 8
    for station in dark:
        assert station.cycle_time_s.value is None
        assert station.cycle_time_s.missingness.value == "missing"
        assert station.cycle_time_s.confidence == 0.0


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
