"""REST control surface + the FastAPI app factory.

A factory (`create_app`), not a module-level `app`, so tests can spin up an
isolated Engine + app per test rather than sharing process-global state.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import APIRouter, FastAPI, HTTPException, Query, Response
from fastapi.sse import EventSourceResponse
from fastapi.staticfiles import StaticFiles

from twin.contracts import ControlAck, ControlCommand
from twin.diagnostic.genealogy import list_defect_candidates, trace_genealogy
from twin.economics import QC_LAG_UNITS, REWORK_COST_DELTA_USD
from twin.sim.engine import Engine

from .sse import make_stream_route

WEB_DIR = Path(__file__).resolve().parents[3] / "web"
# The Control Center (docs/CONTROL_CENTER.md) is a separate, parallel
# prototype -- built alongside the primary dashboard rather than replacing
# it, per explicit instruction. It reads the exact same live engine through
# these same routes; nothing about the backend is duplicated for it.
CONTROL_CENTER_DIR = Path(__file__).resolve().parents[3] / "web-control-center"


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

    @router.get("/api/twin/sensor_placement")
    async def sensor_placement(budget: int = Query(default=3, ge=0, le=30)) -> dict:
        # Phase 9's greedy placement (graph/placement.py), exposed for the
        # leadership view's instrumentation-required-vs-recommended panel --
        # ranked by how much each pick would currently improve coverage over
        # the remaining dark stations, re-solved after each pick.
        return {
            "dark_stations": sorted(engine.config.dark_stations),
            "recommended_next": engine.sensor_placement_ranking(budget),
            "budget": budget,
        }

    @router.get("/api/twin/economics_config")
    async def economics_config() -> dict:
        # Static per-process constants (src/twin/economics.py), not part of
        # the per-tick Snapshot -- fetched once, not streamed, so the frozen
        # wire contract (contracts.py) never needed touching for this.
        return {
            "qc_lag_units": QC_LAG_UNITS,
            "rework_cost_delta_usd": REWORK_COST_DELTA_USD,
        }

    @router.get("/api/twin/risk_threshold")
    async def risk_threshold() -> dict:
        # Model B's own MCC-tuned threshold (tools/train_station_risk.py),
        # exposed so the UI's alert system flags a station on the SAME
        # criterion the model was evaluated against -- not an arbitrary
        # round number invented in JavaScript. None if Model B isn't loaded
        # (ml/models/ not populated), matching how the rest of the payload
        # already treats an absent scorer as an honest omission, not an error.
        scorer = engine._risk_scorer
        return {"threshold": scorer.threshold if scorer is not None else None}

    @router.get("/api/twin/genealogy/candidates")
    async def genealogy_candidates(limit: int = Query(default=10, ge=1, le=50)) -> dict:
        # diagnostic/genealogy.py is built and tested (Phase 9) but was never
        # wired into any live view -- the Control Center's Root Cause screen
        # is its first real API surface. Ranks recently-completed units by
        # their own path's single most anomalous cycle time, so a caller
        # doesn't need to already know a unit_id to find one worth tracing.
        candidates = list_defect_candidates(engine.line.events, limit=limit)
        return {
            "candidates": [
                {
                    "unit_id": c.unit_id,
                    "peak_z_score": round(c.peak_z_score, 3),
                    "peak_station_id": c.peak_station_id,
                }
                for c in candidates
            ]
        }

    @router.get("/api/twin/genealogy/{unit_id}")
    async def genealogy_trace(unit_id: int) -> dict:
        try:
            result = trace_genealogy(engine.line.events, unit_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {
            "defect_unit_id": result.defect_unit_id,
            "origin_station_id": result.origin_station_id,
            "origin_z_score": round(result.origin_z_score, 3),
            "confidence": round(result.confidence, 3),
            "path": result.path,
            "affected_unit_ids": result.affected_unit_ids,
            "origin_realigned_time_s": round(result.origin_realigned_time_s, 2),
        }

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

    # Mounted before the "/" catch-all below, for the same reason API routes
    # are registered before it: Starlette matches mounts in registration
    # order by prefix, so "/" registered first would shadow "/control-center"
    # entirely (every path starts with "/"). This mount has nothing to do
    # with the API-vs-static ordering rule below -- it is its OWN instance of
    # the identical rule, one level up.
    if CONTROL_CENTER_DIR.is_dir():
        app.mount(
            "/control-center",
            StaticFiles(directory=CONTROL_CENTER_DIR, html=True),
            name="control-center",
        )

    # Mounted LAST, deliberately: a Mount is matched like any other route in
    # registration order, so mounting the static frontend before the API
    # routes above would let its catch-all prefix ("/") shadow them. This
    # ordering is what makes "API routes always win, static files are the
    # fallback" true rather than incidental.
    if WEB_DIR.is_dir():
        app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")

    return app


__all__ = ["create_app", "make_control_routes"]
