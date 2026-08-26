# ADR-002 — Transport: Server-Sent Events via `fastapi.sse`

**Status:** Accepted (Phase 2)
**Supersedes:** the unresolved "one import line" carried by earlier planning documents

---

## Context

The twin pushes a full state snapshot every tick (8 Hz locally, 2 Hz hosted) and receives
sub-1 Hz control input. Earlier planning documents carried **both** candidate mechanisms
simultaneously — `sse-starlette==3.4.8` as a pinned dependency *and* a note that `fastapi.sse` was
"documented, added in FastAPI 0.135.0" — without deciding between them. That left a pinned
dependency whose justification was unresolved.

## Decision

**Use `fastapi.sse` (built into FastAPI 0.141.1). Drop `sse-starlette` entirely.**

Transport shape:

- `GET /api/twin/stream` — SSE. One `run_meta` event, then `snapshot` events.
- Control travels by ordinary REST (`POST /api/twin/control`), not over the stream.

## Why SSE rather than WebSocket

The two flows are asymmetric: 8 Hz one-way state versus sub-1 Hz control. SSE gets automatic
reconnection and `Last-Event-ID` for free, and — decisively for this project — **`curl -N` shows real
frames in any terminal**, so a judge can verify the stream is genuinely server-pushed without
installing anything. That property is load-bearing for the falsifiability demo, where a browser's
EventStream panel and a terminal `curl` must be shown side by side displaying identical tick numbers.

## Why `fastapi.sse` rather than `sse-starlette`

Both work. `fastapi.sse` is built in, so it removes a dependency for no loss of capability. There is
no third-party version to track against FastAPI's own release cadence.

## Two non-obvious requirements the spike discovered

Both would have failed silently or confusingly in later phases. Recorded because neither is
guessable from the import surface.

**1. `EventSourceResponse` is a marker, not a wrapper.**

The encoding lives in FastAPI's routing layer, not the class. The path operation itself must be the
async generator, with `response_class=`:

```python
# CORRECT — frames flow
@app.get("/api/twin/stream", response_class=EventSourceResponse)
async def stream():
    yield ServerSentEvent(event="snapshot", raw_data=snap.model_dump_json())

# WRONG — 200 OK, correct content-type, and ZERO frames delivered.
# Fails with AttributeError: 'ServerSentEvent' object has no attribute 'encode'
@app.get("/api/twin/stream")
async def stream():
    return EventSourceResponse(gen())
```

The wrong form returns a healthy-looking `200` with `content-type: text/event-stream` and then
delivers nothing — the worst possible failure signature to debug against a live dashboard.

**2. Use `raw_data=`, not `data=`, for pre-serialized JSON.**

`ServerSentEvent` carries both `data: Any` (serialized *by* the routing layer) and
`raw_data: str | None` (passed through verbatim). Handing an already-serialized string to `data=`
serializes it a second time:

```
data=json.dumps(snap)   ->  data: "{\"tick\": 47, \"seq\": 47}"   # JSON string containing JSON
raw_data=snap_json      ->  data: {"tick": 47, "seq": 47}         # correct
```

Under the double-encoded form a browser's `JSON.parse(event.data)` returns a *string*, requiring a
second parse. We use `raw_data=` with pydantic's `model_dump_json()`, so serialization happens once
and we control it.

## Verified in the spike

| Property | Result |
|---|---|
| `content-type` | `text/event-stream; charset=utf-8` |
| `cache-control` | `no-cache` (set automatically) |
| Frame rate | 25 snapshot frames in 3 s against an 8 Hz ticker |
| Named event types | `run_meta` and `snapshot` distinguishable client-side |
| Conflation | Emits only when `seq` changes |
| Client disconnect | Clean — no traceback, no 500 |
| Single-parse | `JSON.parse(event.data)` yields an object, asserted |

## Consequences

- One fewer runtime dependency.
- Both requirements above become **binding constraints on `api/sse.py`** in Phase 6, and the
  single-parse property is asserted in a test so a regression cannot reintroduce double-encoding.
- Snapshot serialization goes through `model_dump_json()` on the frozen pydantic contract, keeping
  one serialization path for the wire format.
- Full snapshots every tick, never deltas: a dropped frame desyncs the UI, whereas full snapshots
  self-heal on reconnect.
