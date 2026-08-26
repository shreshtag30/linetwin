"""TelemetrySource: the abstraction that makes LineTwin source-agnostic.

Non-de-scopable (per docs/DECISIONS.md). This is the direct, structural answer to
"is this really a twin, or a hardcoded simulation with a dashboard bolted on?" --
and it is the architectural evidence behind the integration story: a real deployment
would implement this ABC over an OPC-UA or historian read, in place of the
simulation, with every downstream consumer (bottleneck detection, risk scoring,
sensor-gap inference) unchanged.

The contract is deliberately narrow: a `TelemetrySource` produces a stream of frozen
`Snapshot` objects (src/twin/contracts.py) and nothing else. Nothing downstream may
import `simpy`, reach into simulation internals, or assume any particular producer.
`tests/test_source_agnostic.py` runs the full analytics path against `ReplaySource`
with `simpy` never imported, as the direct proof of this claim.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from pathlib import Path

from twin.contracts import Snapshot


class TelemetrySource(ABC):
    """Anything that can produce a stream of line snapshots.

    Implementations: `ReplaySource` (this module, fixture-backed, no simpy) and the
    live simulation source built in Phase 6, which wraps `engine.py` and yields one
    `Snapshot` per tick. A real deployment's OPC-UA/historian tap (docs/PRIOR_ART.md,
    docs/REQUIREMENTS.md row A3) would be a third implementation of this same ABC,
    read-only by construction -- there is no `write` method on this class, and none
    should ever be added.
    """

    @abstractmethod
    def frames(self) -> AsyncIterator[Snapshot]:
        """Yield snapshots in tick order. Must not raise on end-of-stream; return."""
        raise NotImplementedError

    @abstractmethod
    async def close(self) -> None:
        """Release any held resource. Must be safe to call more than once."""
        raise NotImplementedError


class ReplaySource(TelemetrySource):
    """Replays a fixture file of pre-recorded snapshots.

    This is the "simpy is not even imported" leg of the source-agnosticism proof:
    it parses JSON and reconstructs frozen `Snapshot` objects, nothing more. Also the
    fallback demo path if a live simulation is ever unavailable (docs/DECISIONS.md,
    contingency ladder rung 3).
    """

    def __init__(self, fixture_path: Path | str) -> None:
        self._path = Path(fixture_path)
        self._closed = False

    async def frames(self) -> AsyncIterator[Snapshot]:
        if self._closed:
            return
        with self._path.open("r", encoding="utf-8") as fh:
            raw = json.load(fh)
        for entry in raw["snapshots"]:
            if self._closed:
                return
            yield Snapshot.model_validate(entry)

    async def close(self) -> None:
        self._closed = True


__all__ = ["ReplaySource", "TelemetrySource"]
