# LineTwin

A live discrete-event digital twin of a vehicle assembly line — bottleneck detection, defect-risk
prediction, and inference at sensor-poor stations.

Built for the **Accenture Innovation Challenge 2026, Round 2, Problem Track 4 "DigitalTwin.ai"** as
the Working Prototype deliverable.

> **Status: under construction.** Phase 2 of 10. This README is a placeholder; the full document
> (quickstart, minimum-signal table, capability ladder, honesty ledger, integration and scalability
> notes) is written in Phase 10. See `docs/tracker.html` for live build progress and
> `docs/phases/` for the per-phase record.

## Scope, stated up front

LineTwin models **30 stations across three zones** (body construction, paint, final assembly) as a
deliberately scoped subset of the 30–50 station line the brief describes. Station count, topology,
cycle-time distributions and per-station instrumentation are all configuration — scaling is a new
YAML file, not new code.

**22 of 30 stations are instrumented; 8 are dark.** The inference layer is therefore load-bearing
rather than decorative.

## What this is, precisely

Under Kritzinger et al. (2018) this is a **Digital Shadow** — automated one-way data flow, no
write-back to any physical system. Under Grieves & Vickers (2017) it is a **Digital Twin Prototype**:
a virtual construct that exists before any physical instance, which is what the brief asked for.
Under Villegas et al. (2025) it sits at the **Predictive** maturity level.

All simulation data is exactly that — simulated, from stated first principles, seed-reproducible, and
labelled as model output. Parameters are calibrated against cited public sources where such sources
exist, and stamped `synthetic — uncalibrated` where they do not. See `docs/CITATIONS.md`.

## Documentation

| File | Contents |
|---|---|
| `docs/REQUIREMENTS.md` | Every brief requirement traced to a feature, module, and its evidence |
| `docs/CITATIONS.md` | Verified sources · individually-banned figures · our own numbers and their mandatory qualifications |
| `docs/DECISIONS.md` | Identity, architecture, scope cuts, contingency ladder |
| `docs/PRIOR_ART.md` | Position on US 12,353,197 B2 (Accenture) and mandatory citation discipline |
| `docs/phases/` | Per-phase record, one markdown + PDF per phase |
| `docs/tracker.html` | Live build progress |

## Licence

Apache-2.0 — chosen over MIT for its explicit patent grant.
