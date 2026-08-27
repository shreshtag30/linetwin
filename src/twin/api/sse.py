"""SSE stream endpoint. See docs/adr/ADR-002-transport.md for the two
non-obvious requirements this module is built against -- both are binding
constraints found by a spike, not stylistic choices:

1. `EventSourceResponse` is a marker read by FastAPI's routing layer, not a
   wrapper you return -- the path operation itself must be `response_class=
   EventSourceResponse` and must itself be the async generator.
2. Pre-serialized JSON goes through `raw_data=`, never `data=`, or it gets
   encoded twice and a browser's `JSON.parse(event.data)` returns a string.

CORRECTION, left here because the wrong turn is instructive: an earlier
version of `stream()` below polled `await request.is_disconnected()` once per
loop iteration. Investigating a real deadlock (heavy perturbation + closing
and reopening the stream from a browser pinned the whole server at ~100% CPU,
every request including `/healthz` hanging indefinitely -- never reproduced
via `curl`, only a real browser `EventSource`), that polling pattern looked
like a plausible cause: Starlette's `is_disconnected()` reads the next
message off the request's ASGI receive channel inside an already-cancelled
`anyio.CancelScope`, which is a more invasive operation than a side-effect-
free connectivity check. The poll was removed on that theory, relying instead
on FastAPI/Starlette's documented behavior of cancelling a route's async
generator (`GeneratorExit`/`CancelledError`) when the underlying connection
goes away.

**That fix did not work.** The exact same deadlock was reproduced again
immediately afterward. A `faulthandler`-based stack dump of the hung process
(see `docs/phases/phase-07-floor-supervisor.md`) showed the real cause was
entirely unrelated to this file: `diagnostic/bottleneck.py`'s ANOVA +
Tukey-Kramer significance test, called synchronously from `engine.py`'s tick
loop, becomes pathologically slow via `scipy`'s numerical integration when a
perturbation is extreme enough to produce widely-separated active-period
distributions -- blocking the single-threaded event loop for as long as that
integration runs. Fixed in `engine.py` with `asyncio.to_thread`, not here.

The `is_disconnected()` removal is kept anyway -- it is still a more correct
pattern for the reason described above -- but it gets no credit for fixing
anything. Recorded so a future reader does not repeat the same wrong
inference from the same evidence.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi.sse import ServerSentEvent

from twin.sim.engine import Engine


def make_stream_route(engine: Engine):
    """Returns a plain (undecorated) async-generator route function bound to
    one Engine instance. Deliberately NOT decorated onto a shared module-level
    router here -- an APIRouter accumulates every route ever registered on
    it, so decorating inside this factory would leak stale routes from one
    test's Engine into the next test's app. The caller (routes.py's
    `create_app`) registers this function directly via `add_api_route(...,
    response_class=EventSourceResponse)` on its own per-app router instead.
    """

    async def stream() -> AsyncIterator[ServerSentEvent]:
        if engine.run_meta is not None:
            yield ServerSentEvent(
                event="run_meta",
                raw_data=engine.run_meta.model_dump_json(),
            )

        last_seq = -1
        # A client disconnect surfaces as this generator being cancelled by
        # the ASGI layer (GeneratorExit/CancelledError), not as a value to
        # poll for. Letting it propagate naturally out of this loop -- rather
        # than catching and suppressing it -- is the correct, minimal
        # behavior: FastAPI/Starlette already treat that as a routine,
        # silent stream closure.
        while True:
            snapshot = await engine.bus.wait_for_next(last_seq)
            last_seq = snapshot.seq
            yield ServerSentEvent(
                event="snapshot",
                raw_data=snapshot.model_dump_json(),
            )

    return stream


__all__ = ["make_stream_route"]
