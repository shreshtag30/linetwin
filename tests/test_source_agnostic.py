"""Non-de-scopable: proves LineTwin is source-agnostic, not simulation-only.

The direct, structural answer to "is this really a twin, or a simulation with a
dashboard bolted on?" -- `ReplaySource` reconstructs frozen `Snapshot` objects
from a fixture file and nothing else.

IMPORTANT, and a real bug found while building this: the "simpy is not imported"
claim CANNOT be checked by asserting `"simpy" not in sys.modules` from within an
ordinary pytest test function. pytest collects and runs all test files in one
shared process, and `test_cascade.py` / `test_active_period.py` both import
`twin.sim.station`, which imports `simpy`. Once any test file in the session has
done that, `simpy` stays in `sys.modules` for the rest of the process --
including inside this file's tests, regardless of what THIS file itself
imports. An in-process check was therefore asserting something true about test
execution order, not about this module's actual dependencies, and failed
non-deterministically depending on which files pytest happened to collect
first.

The only architecturally sound way to make this claim is to run it in a
genuinely separate process that imports ONLY `twin.contracts` and
`twin.sources`, with nothing else on the import path to pull `simpy` in. That is
what this file does below.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from twin.contracts import Snapshot
from twin.sources import ReplaySource

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "replay_30x60.json"

_ISOLATED_CHECK = """
import sys
from pathlib import Path
from twin.sources import ReplaySource

fixture = Path(sys.argv[1])

assert "simpy" not in sys.modules, "simpy must not be imported merely by importing twin.sources"

async def main():
    source = ReplaySource(fixture)
    count = 0
    async for snap in source.frames():
        count += 1
    await source.close()
    assert count == 60, f"expected 60 snapshots, got {count}"
    assert "simpy" not in sys.modules, "consuming the whole replay must not import simpy"
    print("OK")

import asyncio
asyncio.run(main())
"""


def test_simpy_is_absent_from_the_import_chain_in_a_clean_process() -> None:
    """Runs in a fresh interpreter -- no other test file's imports can leak in."""
    result = subprocess.run(
        [sys.executable, "-c", _ISOLATED_CHECK, str(FIXTURE)],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, (
        f"isolated source-agnosticism check failed:\nstdout={result.stdout}\nstderr={result.stderr}"
    )
    assert result.stdout.strip() == "OK"


@pytest.mark.asyncio
async def test_replay_source_yields_valid_snapshots_in_tick_order() -> None:
    """In-process: fine to run alongside simpy-importing tests, since this test
    makes no claim about sys.modules -- only about ReplaySource's own behavior.
    """
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
