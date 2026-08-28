# ADR-001 — Simulation Core: SimPy `Store`, the Merged Station Pattern

**Status:** Accepted (Phase 3)

---

## Context

The twin needs a discrete-event simulation of a 30-station assembly line whose per-station
behavior (working, starved, blocked, down, repair, setup) is both simulatable and diagnosable —
Phase 5's Active Period Method needs the exact active/inactive state history, not just throughput
numbers. Two structural choices decide whether that is even possible: what SimPy primitive
represents a buffer, and how a station's occupy/process/depart sequence is ordered against its
downstream neighbor.

## Decision

**`simpy.Store(capacity=k)` for every buffer, never `simpy.Container`.** And **the merged station
pattern** (ProdSim's construction): a station's downstream `put()` executes *while the station is
still occupied*, before that station is freed to accept its next unit.

## Why `Store`, not `Container`

`Container` models a continuous quantity — it has no per-unit identity. Phase 9's defect genealogy
needs to walk a specific unit's path station-by-station and realign timestamps by transfer delay;
that is impossible if the buffer only knows "how much," never "which ones." `Store` carries actual
Python objects (`_Part` instances with a `unit_id`), so genealogy has something to walk.

## Why the merged station pattern, and the two silent bugs it guards against

Two bugs here would each invalidate a core project claim without ever raising an error —
found and fixed in Phase 3, each now protected by a dedicated regression test with an
`_unsafe_*` escape hatch that proves the test fails red when the guard is removed.

**Bug A — put-while-occupied ordering.** If the downstream `put()` executes only *after* the
station frees itself, a slowdown at a downstream station never propagates upstream as blocking —
the cascade thesis this entire project is built on (a bottleneck's effect on the whole line, not
just itself) would silently fail to model. `test_cascade.py` asserts a 10x downstream slowdown over
2000 sim-seconds produces `time_in_state['blocked'] > 0` upstream *and* the feeding buffer reaches
capacity — checked to actually go red when the guard is bypassed via `_unsafe_disable_merged_put`,
not merely assumed to.

**Bug B — the `.triggered` guard on STARVED/BLOCKED.** SimPy can satisfy a `Store.get()` request in
the same timestep it was issued. Setting a station's state to STARVED/BLOCKED unconditionally —
rather than only when the request did *not* fire immediately — truncates every active period to
one cycle time, which silently degrades the Active Period Method into the discredited Utilization
method (Roser, Nakano & Tanaka 2001/2002's own published comparison: APM MSE 0.04% vs Utilization
29.21%). `test_active_period.py` asserts WORKING→DOWN→WORKING counts as **one** active period
(a breakdown does not end it — the counterintuitive, load-bearing rule from the cited paper) and
WORKING→STARVED→WORKING counts as **two** — again checked to fail red with the guard removed.

## Consequences

- Every station's buffer is a `simpy.Store`, which is what makes Phase 9's genealogy and Phase 4's
  ground-truth sensitivity analysis (per-unit, per-station cycle-time attribution) possible at all.
- `station.py`'s merged pattern is now the single highest-value, most carefully guarded file in the
  project — any future change to station transition logic must re-run both regression tests with
  their guards deliberately disabled to confirm they still fail red.
- This decision is upstream of Phase 5's whole premise: a detector benchmark against ground truth is
  only meaningful if the underlying simulation actually produces the active-period semantics the
  detectors are being scored against.
