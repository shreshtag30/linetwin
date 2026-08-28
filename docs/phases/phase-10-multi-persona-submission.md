# Phase 10 — Multi-Persona Views, Docs & Submission

**LineTwin** · Accenture Innovation Challenge 2026 · Round 2 · Problem Track 4 "DigitalTwin.ai"

---

## Purpose

Close the build: the two personas the brief names beyond the floor supervisor, the honesty ledger
written directly into the README rather than only in linked documents, and the submission
scaffolding (limitations, interview prep, a real one-command readiness check, and the video script).
Two real gaps left over from earlier phases got wired up here rather than built fresh, and both
surfaced a genuine bug while being fixed — consistent with this project's pattern of finding real
problems by actually running things, not by inspection alone.

---

## Wiring up two things earlier phases had left unfinished

**`predicted_bottleneck` had been hardcoded to `None`** in `engine.py` since Phase 8
(`# Phase 8` was the literal stub comment), even though `diagnostic/rolling_horizon.py`'s
`fork_and_predict` was built and tested that same phase. The plant-manager view's "predicted
downtime" panel needed it live, so it got wired in — as a background `asyncio` task, not an inline
await. **Real bug, found doing this**: awaiting it directly (even through `asyncio.to_thread`)
measured up to ~700ms early in a run — near-empty queues mean far more discrete simpy events occur
over the same 1800-second forecast horizon than once the line has settled into steady congestion
(mostly BLOCKED/STARVED stations produce very few events for the same wall-clock span). Awaiting
that inline, even off-thread, still delayed the tick loop's own publish by however long the fork
took, collapsing measured `real_time_factor` to ~0.6–0.7 — `to_thread` protects *other* coroutines
from blocking, not the coroutine that is itself awaiting it. Fixed with a genuine fire-and-forget
background task, gated so at most one runs at a time, guarded against a stale forecast from a
since-restarted run overwriting current state. Regression test:
`test_predicted_bottleneck_populates_without_starving_the_tick_loop`.

**Phase 9's greedy sensor placement had no way to reach the leadership view.** Rather than
duplicate the observed/prior cycle-time construction logic a second time, `engine.py`'s
`_compute_inference` was refactored to share `_observed_and_prior_cycle_times()` with a new
`sensor_placement_ranking()` method, exposed via `GET /api/twin/sensor_placement`. A second new
route, `GET /api/twin/economics_config`, exposes the leadership ROI constants
(`src/twin/economics.py`) once per session rather than duplicating them in JavaScript.

## `src/twin/economics.py` — ROI as a stated estimate, not an audited saving

Two constants, both named `synthetic — uncalibrated` per this project's citation discipline:
`QC_LAG_UNITS` (units typically in the pipeline between a station and final inspection) and
`REWORK_COST_DELTA_USD` (incremental rework cost avoided by catching a defect early rather than
late). Two pure functions turn a live mean defect-risk reading into "units at risk" and "dollars at
stake." Every function is named `estimate_*`, deliberately never `actual_*` or `measured_*` — there
is no real before/after production run to compare against, only this project's own simulated risk
scores, and the leadership panel's own copy says this directly rather than only in a linked
document.

## Three persona tabs, one shared stream

`web/index.html`/`app.js` gained Plant Manager (bottleneck frequency tally, the predicted-downtime
panel, a longer-window trend chart, a shifting-bottleneck timeline) and Leadership (the ROI
estimate, instrumentation required-vs-recommended off Phase 9's placement ranking) tabs — all
computed client-side from the *same* single `EventSource` connection the floor-supervisor view
already had, per this project's "one connection, no framework" discipline. Each tab is labelled
in-UI with the specific brief requirement it answers, not left implicit.

**Two real bugs found live in the browser while verifying the tab switch, not assumed to work:**

1. `.layout`'s own `display: grid` rule outranked the browser's default `[hidden] { display: none }`
   — a class selector beats an attribute selector of otherwise-equal specificity, and the app's own
   stylesheet loads after the user-agent one. A "hidden" persona view rendered anyway. Fixed with an
   explicit `.layout[hidden] { display: none; }` rule.
2. Three sibling `<main>` landmarks (one per persona view) meant any "extract the main content"
   tooling always picked the *first* one regardless of which tab was actually visible — caught using
   this project's own browser-automation tooling to read the page's text content and finding it
   didn't match the visibly active tab. Fixed to one `<main>` landmark wrapping three toggled
   `<div class="persona-view">` children, which is also the semantically correct structure (a
   document should have one main-content landmark, not one per tab).

Live-verified end to end: a judge-chosen-style 9× perturbation on S01 built visible WIP; the
plant-manager predicted-downtime panel showed a real forecast/actual divergence ("forecast: shift
from S17 to S01") that resolved into agreement once the live bottleneck actually reached S01 a few
ticks later — the rolling-horizon forecast genuinely predicting ahead of the live verdict, not just
echoing it; and the leadership view's sensor-placement ranking updated live as the budget slider
moved.

## `tools/run_server.py` and `tools/preflight.py`

`create_app()` requires a `scenario_path` argument, so there was no bare
`uvicorn twin.api.routes:create_app --factory` invocation that would actually work — discovered
while trying to manually verify the new routes in a browser. `tools/run_server.py` is the missing
wiring, and is now the one command the README's quickstart names.

`tools/preflight.py` runs the exact sequence CI runs (lint, fixture regen, Model B data generation
and training, full suite) in one command, so "does this pass on a fresh machine" has one answer
rather than five separately-run commands that could quietly drift from what CI actually checks.
Run end to end while writing this record: 160/160 tests green.

## Documentation

README rewritten in full: quickstart above the second fold (verified against a real run — an early
draft was missing the `tools/generate_training_data.py` step before `train_station_risk.py`, caught
by actually running the documented sequence rather than assuming it), the per-layer minimum-signal
table (the flow layer needs one bit per station and automotive lines already have it; the quality
layer is where real sensor gaps bite, and graph propagation explicitly does **not** cover a missing
quality signal — stated directly rather than implied), a capability ladder naming one real test file
per row, the honesty ledger, and integration/scalability notes. 150 lines, well under the 400-line
budget the plan set.

`docs/LIMITATIONS.md` consolidates every limitation surfaced across all ten phases into one place,
including the deployment decision (see below). `docs/adr/` gained the three missing records
(ADR-001 simulation core, ADR-003 ML data provenance, ADR-004 sensor gap — ADR-002 transport already
existed from Phase 2). `docs/VIDEO_SCRIPT.md` scripts the three falsifiability beats concretely,
including the exact `curl` command for beat 2, verified to actually work against a running server
while writing it.

## Deployment: not attempted, and why

Standing up a hosted instance needs a new external account and a push to a third-party platform —
both are the kind of action this project treats as needing the user's explicit sign-off first, not
something to do unilaterally. The verified path is the local one (`tools/run_server.py`), which is
what every live-verification claim across all ten phases was actually checked against. Recorded in
`docs/LIMITATIONS.md` rather than silently skipped.

## What's for the user, not the submission

`docs/tracker.html`, `docs/phases/*.pdf`, and `docs/INTERVIEW.md` are kept locally updated but are
**not committed to the repository** — they're for tracking progress and interview prep, not part of
what a judge clones. `.gitignore` updated accordingly; nothing was deleted, only untracked.

---

## Deliverables produced

| Artefact | What it does |
|---|---|
| `src/twin/economics.py` | ROI estimate for the leadership view, every constant labeled `synthetic — uncalibrated` |
| `web/index.html`, `web/app.js`, `web/styles.css` (extended) | Three persona tabs over one stream |
| `src/twin/sim/engine.py` (extended) | `predicted_bottleneck` live, as a background task; shared observed/prior helper |
| `src/twin/api/routes.py` (extended) | `GET /api/twin/sensor_placement`, `GET /api/twin/economics_config` |
| `tools/run_server.py` | The one command the README's quickstart names |
| `tools/preflight.py` | One command running the exact CI sequence |
| `README.md` (rewritten) | Quickstart, minimum-signal table, capability ladder, honesty ledger |
| `docs/LIMITATIONS.md`, `docs/adr/ADR-00{1,3,4}-*.md` | The remaining honesty/documentation deliverables |
| `docs/VIDEO_SCRIPT.md` | The three falsifiability beats, scripted concretely |

---

## Exit criteria

| Criterion | Status |
|---|---|
| Plant-manager view + leadership ROI panel, each labelled with the requirement it answers | Met |
| README — quickstart, minimum-signal table, capability ladder with a real test name per row | Met |
| Limitations and interview docs written; four ADRs complete | Met |
| Full suite green; CI green on both operating systems | Met — 160/160, verified via `gh run view` |
| Hosted deploy, or a limitations entry naming exactly why not | Met — not attempted, reason recorded |
| Three video beats scripted concretely, one command verified to work | Met |
| Clean clone → one documented command → working twin | Met — `tools/preflight.py` run end to end |

**160/160 tests passing** (150 from Phases 1–9 + 10 new this phase: `test_economics.py` (5),
`test_sensor_placement_*`/`test_economics_config_*` in `test_api.py` (3),
`test_predicted_bottleneck_populates_without_starving_the_tick_loop` +
`test_sensor_placement_ranking_only_picks_dark_stations` in `test_engine.py` (2)).
