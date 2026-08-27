"""REST control surface + the FastAPI app factory.

A factory (`create_app`), not a module-level `app`, so tests can spin up an
isolated Engine + app per test rather than sharing process-global state.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import APIRouter, FastAPI, HTTPException, Response
from fastapi.sse import EventSourceResponse

from twin.contracts import ControlAck, ControlCommand
from twin.sim.engine import Engine

from .sse import make_stream_route


def make_control_routes(engine: Engine) -> APIRouter:
    router = APIRouter()

    @router.get("/api/twin/state", response_model=None)
    async def state() -> Response:
        # REAL BUG, found while verifying this route by hand (Phase 6): an
        # earlier version of this handler used `-> Snapshot` as an implicit
        # response_model. FastAPI does not just call `.model_dump_json()` on
        # an already-valid pydantic instance in that case -- it re-validates
        # the whole nested structure through its own jsonable_encoder, which
        # measured at 1.35s for one 30-station Snapshot (vs ~2ms for
        # `model_dump_json()` directly). Because the engine's tick loop and
        # every request run on the SAME single-threaded asyncio event loop,
        # that 1.35s blocks tick production for over ten ticks' worth of
        # time. Under concurrent requests this compounds: each slow response
        # adds more lag, which the engine then has nothing left to catch up
        # with, and the whole process wedges at 100% CPU with every request
        # hanging indefinitely -- reproduced directly against a running
        # server, not merely suspected. `response_model=None` + a manually
        # built `Response` bypasses FastAPI's response-model machinery
        # entirely, matching the SSE route's already-correct approach.
        if engine.bus.latest is None:
            raise HTTPException(status_code=503, detail="engine has not produced a tick yet")
        return Response(
            content=engine.bus.latest.model_dump_json(),
            media_type="application/json",
        )

    @router.post("/api/twin/control")
    async def control(cmd: ControlCommand) -> ControlAck:
        # cmd.cycle_time_multiplier's [0.1, 10.0] bound is already enforced by
        # ControlCommand's own Field constraint -- FastAPI 422s an out-of-range
        # value before this handler body ever runs. Only station existence is
        # checked here.
        if cmd.station_id not in engine.config.station_ids:
            raise HTTPException(status_code=404, detail=f"unknown station_id: {cmd.station_id}")

        await engine.control_queue.put(cmd)
        return ControlAck(
            accepted=True,
            station_id=cmd.station_id,
            cycle_time_multiplier=cmd.cycle_time_multiplier,
            # Drained at the top of the NEXT tick loop iteration, per engine.py.
            applied_at_tick=engine.tick + 1,
        )

    @router.post("/api/twin/heartbeat")
    async def heartbeat() -> dict:
        return {"status": "ok", "tick": engine.tick, "run_id": engine.run_id}

    @router.post("/api/twin/restart")
    async def restart() -> dict:
        previous_run_id = engine.run_id
        engine.request_restart()
        return {
            "accepted": True,
            "previous_run_id": previous_run_id,
            "note": "reconnect to /api/twin/stream for the new run_meta",
        }

    @router.get("/healthz")
    async def healthz() -> dict:
        return {"status": "ok"}

    return router


def create_app(scenario_path: Path | str, *, seed: int | None = None) -> FastAPI:
    engine = Engine(scenario_path, seed=seed)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        task = asyncio.create_task(engine.run())
        _app.state.engine_task = task
        try:
            yield
        finally:
            engine.stop()
            await task

    app = FastAPI(lifespan=lifespan)
    app.state.engine = engine
    app.include_router(make_control_routes(engine))
    app.router.add_api_route(
        "/api/twin/stream",
        make_stream_route(engine),
        methods=["GET"],
        response_class=EventSourceResponse,
    )
    return app


__all__ = ["create_app", "make_control_routes"]
