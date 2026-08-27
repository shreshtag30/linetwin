"""SSE stream endpoint. See docs/adr/ADR-002-transport.md for the two
non-obvious requirements this module is built against -- both are binding
constraints found by a spike, not stylistic choices:

1. `EventSourceResponse` is a marker read by FastAPI's routing layer, not a
   wrapper you return -- the path operation itself must be `response_class=
   EventSourceResponse` and must itself be the async generator.
2. Pre-serialized JSON goes through `raw_data=`, never `data=`, or it gets
   encoded twice and a browser's `JSON.parse(event.data)` returns a string.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi import Request
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

    async def stream(request: Request) -> AsyncIterator[ServerSentEvent]:
        if engine.run_meta is not None:
            yield ServerSentEvent(
                event="run_meta",
                raw_data=engine.run_meta.model_dump_json(),
            )

        last_seq = -1
        while True:
            if await request.is_disconnected():
                # Clean client disconnect -- the conflation bus and the tick
                # loop are unaffected; other consumers keep streaming.
                break
            snapshot = await engine.bus.wait_for_next(last_seq)
            last_seq = snapshot.seq
            yield ServerSentEvent(
                event="snapshot",
                raw_data=snapshot.model_dump_json(),
            )

    return stream


__all__ = ["make_stream_route"]
