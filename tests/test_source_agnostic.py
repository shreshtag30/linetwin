"""Non-de-scopable: proves LineTwin is source-agnostic, not simulation-only.

The direct, structural answer to "is this really a twin, or a simulation with a
dashboard bolted on?" -- `ReplaySource` reconstructs frozen `Snapshot` objects from
a fixture file and nothing else. `simpy` is asserted absent from `sys.modules`
throughout, which a script cannot fake: importing `twin.sim.*` anywhere in this
process would pull `simpy` in transitively and this test would go red.

As later phases add bottleneck detection (Phase 5) and risk scoring (Phase 8),
extend this test to run those analyses against `ReplaySource` output too, keeping
the same "simpy never imported" assertion. Do not weaken that assertion to make a
later addition easier -- if something here needs simpy, it does not belong in the
source-agnostic path.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from twin.contracts import Snapshot
from twin.sources import ReplaySource

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "replay_30x60.json"


def test_simpy_is_not_imported_by_the_contract_or_source_modules() -> None:
    assert "simpy" not in sys.modules, (
        "twin.contracts / twin.sources must not transitively import simpy — "
        "if this fails, something in the import chain reaches into the simulation"
    )


@pytest.mark.asyncio
async def test_replay_source_yields_valid_snapshots_with_simpy_never_imported() -> None:
    source = ReplaySource(FIXTURE)
    count = 0
    last_seq = -1
    async for snap in source.frames():
        assert isinstance(snap, Snapshot)
        assert snap.seq > last_seq, "frames must arrive in strictly increasing seq order"
        last_seq = snap.seq
        count += 1
    await source.close()

    assert count == 60
    assert "simpy" not in sys.modules, "consuming the whole replay must not import simpy"


@pytest.mark.asyncio
async def test_replay_source_close_is_idempotent_and_stops_iteration() -> None:
    source = ReplaySource(FIXTURE)
    await source.close()
    await source.close()  # must not raise

    frames = [snap async for snap in source.frames()]
    assert frames == [], "a closed source must yield nothing rather than error"


@pytest.mark.asyncio
async def test_replay_reconstructs_the_committed_instrumentation_split() -> None:
    source = ReplaySource(FIXTURE)
    first = await anext(source.frames())
    instrumented = sum(1 for s in first.stations if s.instrumented)
    assert instrumented == 22
    assert len(first.stations) - instrumented == 8
    await source.close()
