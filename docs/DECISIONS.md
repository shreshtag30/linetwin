# Phase 1 Decisions

Locked at Phase 1 so later phases do not relitigate them. Each entry states the decision, the reason,
and — where it matters — what would change our mind.

---

## 1. Identity and framing

**Repo name: `linetwin`.** Working title. The submission names the solution *LineTwin*, positioned
against Round 2 Problem Track 4 "DigitalTwin.ai".

**Licence: Apache-2.0.** Chosen over MIT for its explicit patent grant — the same licence XGBoost
itself uses. Given that this project cites a granted Accenture patent as prior art (see
`PRIOR_ART.md`), an explicit patent grant is the coherent choice.

**Station count: 30, across all three zones.** The brief's reference parameters suggest 30–50
stations and mark them "directional, not a fixed dataset". LineTwin sits at the lower end of that
range: body 1–12, paint 13–18, final assembly 19–30.

> The README says this on its first screen, before a reader can infer it: **LineTwin models 30
> stations as a deliberately scoped subset of a 30–50 station line. Station count, zones,
> cycle-time distributions and per-station instrumentation are all configuration.**

*(This section originally recorded a 6-station line (A–F) — the Phase 2 scope — and was never
updated when the line grew to 30. Corrected. One claim in the old wording is also narrowed: topology
is **not** configuration. The line is a hardcoded serial chain (`sim/line.py`) and the inference
graph a hardcoded path (`graph/inference.py`), so a parallel or rework-loop layout would need new
code, not a new YAML file. See `docs/REQUIREMENTS.md` row A6.)*

Leaving this to be discovered would read as a limitation concealed. Stating it first makes it a
scoping decision, which is what it is.

**Sensor coverage: 22 of 30 instrumented, 8 dark (27 % gap).** The brief describes "a majority of
stations well-instrumented, a meaningful minority reliant on manual checks". We deliberately run a
heavier gap than that phrasing implies, so the inference layer is genuinely load-bearing rather than
decorative — and that is now *measured*, not asserted: the graph layer beats a zone-base baseline by
20–32 % at every coverage level (`docs/phases/degradation_curve.csv`). A lighter gap would let the
twin look fine whether or not the inference worked.

**Positioning: Digital Shadow (Kritzinger) / Digital Twin Prototype (Grieves & Vickers) / Predictive
maturity (Villegas et al.).** All three stated in the README **before being asked**, plus a
machine-readable `provenance.py`. The honest classification is stronger than an unqualified "digital
twin", and it is the direct answer to the hardest question a judge can ask.

---

## 2. Architecture — settled, not to be relitigated

| Decision | Reason |
|---|---|
| Plain `simpy.Environment`, advanced by `env.run(until=tick*SIM_DT)` from one asyncio task. **Not** `simpy.rt.RealtimeEnvironment` | `rt.py`'s `step()` blocking-sleeps once per event and owns its thread — it cannot service a control channel. `strict=False` silently requires an undocumented `env.sync()`. Several independent ways to lose a live demo |
| **Server-Sent Events** for state, plain REST for control. **Not** WebSocket | The flows are asymmetric: 8 Hz one-way state versus sub-1 Hz control. SSE gets auto-reconnect and `Last-Event-ID` free, and `curl -N` shows real frames in any terminal — a judge needs no extra tooling to verify the stream is real |
| Vanilla HTML/CSS/JS + vendored uPlot. **Not** React | A port costs days for zero new capability. An 8 Hz stream in React means re-rendering 8×/s or bypassing React with refs — at which point it is vanilla JS wearing React |
| `simpy.Store(env, capacity=k)` between stations. **Never** `Container` | `Container` models undifferentiated bulk with a float `level` and carries no per-unit identity — which would make defect genealogy impossible |
| No `Resource` wrapping a capacity-1 station | The station's own sequential generator *is* the semaphore |
| One `SeedSequence(seed).spawn(n)`, one `Generator(PCG64(child))` per station. **Never** stdlib `random`, never one shared generator | Needed for bit-reproducibility on video, and so baseline-vs-intervention is a genuine Common Random Numbers paired comparison |
| Full snapshot per tick, no deltas | A dropped frame desyncs the UI; full snapshots self-heal on reconnect |
| Single-slot conflation bus; each SSE generator pulls, emitting only when `seq` changes | A slow or dead client cannot stall the simulation clock. **Forbidden:** `for ws in connections: await ws.send_json(...)` inside the tick |
| Absolute-deadline anchored sleep (`t0 + tick*REAL_DT`) | A requested sleep rounds *up* to the next timer quantum. Anchoring self-corrects each overshoot; measured real-time factor 0.9994 vs 0.908 for naive `sleep(dt)` |
| ML inference called directly, **not** via `asyncio.to_thread` | Python's own docs restrict `to_thread` to IO-bound work; dispatch overhead costs more than a 6-row predict |

**Tick geometry:** `SIM_DT / REAL_DT == 60` always, so "1 sim-minute ≈ 1 real second" holds at every
rate. Local `SIM_DT=7.5 / REAL_DT=0.125` → 8 ticks/s. Hosted `SIM_DT=30 / REAL_DT=0.5` → 2 ticks/s.
Same code, one config value.

---

## 3. Absorbed after Phase 1 source review

Four changes the literature review earned, none of which were in the original build brief:

1. **Significance ANNOTATION beside every bottleneck verdict** (Kumbhar et al. 2023). ANOVA +
   Tukey–Kramer over the run's accumulated active-period durations; every verdict carries
   `confidence: established | provisional | none` and a p-value.
   **CORRECTION — this was written as a "gate" and it is not one.** The plan was for the detector to
   return "no significant bottleneck" rather than always naming someone. That was not built, and
   deliberately so: at 8 ticks/s a station has completed 1–2 active periods inside the 2-second
   live-response requirement, nowhere near enough for ANOVA's asymptotics, and consecutive active
   periods at one station are autocorrelated anyway, so the independence assumption does not hold.
   `diagnostic/bottleneck.py` states this correctly and always answers, with the annotation saying
   how confident the answer is. Two other documents described the unbuilt gate as if it shipped;
   both are corrected. The annotation is still the citable answer to the brief's false-alarm
   warning — it is just an honest one.
2. **Three-state value tagging — OBSERVED / INFERRED / SIMULATED**, each with confidence and
   freshness, and **zero / missing / not-applicable kept distinct** (Detzner & Eigner 2018). Prevents
   the classic failure of treating an estimate as a measurement.
3. **Transfer-delay timestamp realignment in the genealogy trace** (US 12,353,197 B2). Contributing
   factors are compared at (detection time − cumulative lag), not at detection time.
4. **Per-station rather than global thresholds** (Fani et al. 2026), where thresholds are used at all.

---

## 4. Explicit scope cuts — do not build these

Recorded so that a later phase does not quietly re-add them under time pressure.

| Cut | Why |
|---|---|
| Breakdowns / MTBF-MTTR / shared technician | Buys DOWN-is-ACTIVE UI confusion, unguarded-yield interrupt crashes, and MTBF numbers with no source. Cycle-time slowdown alone drives the cascade narrative |
| Mondrian conformal prediction + an "Uncalibrated" pill tier | Roadmap sentence only |
| Sensor-placement *sweep* | We ship the value **ranking**; the optimisation sweep is cut. Citing Krause/Singh/Guestrin's (1 − 1/e) bound is most of the credit |
| Implementations of comparison detectors (Arrow, Turning Point) | Cite Roser & Nakano's published comparison table instead |
| Paired baseline-vs-intervention counterfactual chart | Ship the single number with `qc_lag_units` visible as config, so it reads as checkable arithmetic rather than an oracle |
| Telemetry-inspector extras (changed-field highlighting, msg/s meter, frame pinning) | Keep verbatim `event.data`, `Δ ms`, and the copyable `curl` line — those are the parts that prove anything |
| Devcontainer / Codespaces | An untested door that fails on a judge's click manufactures exactly the concealed limitation we swear off |
| `shap`, `crepes`, `orjson` dependencies | Driver contributions are computed **exactly** as `weight × feature value` — the literal logit decomposition of Model B's linear model — so no attribution library is needed at all. (This row previously justified the cut by "`booster.predict(dm, pred_contribs=True)` is exact TreeSHAP in C++". There is no booster any more; the conclusion holds, the reason changed — see `docs/adr/ADR-003`'s superseded banner) |
| Ten ADRs | Four: sim core, transport, ML data provenance, sensor gap |
| `/ws/twin` | Either build it in ~15 lines against the existing bus, or delete any claim it exists. Never carry a claim that depends on a stretch item |
| n=10 station scaling tests | Nobody will ask; the config-driven argument is made by `scenarios/`, not by a test |
| **Bosch as training data, in any form** | Anonymised features with no released semantics — no mapping to our features exists even in principle. 14.3 GB for zero usable signal |

### Cut from the vision document (`Architecture_Plan.md`)

Absorbed from it: three-state value tagging, defect genealogy, the graceful-degradation experiment,
sensor-placement ranking, and the *Read → Reason → Simulate → Recommend* stance.

Rejected from it — all infrastructure that adds operational surface without advancing the core claim,
a judgement its own §43 shares: knowledge graph / Neo4j, TimescaleDB / InfluxDB, Kafka / MQTT brokers,
React / Next.js, a GenAI copilot, 3D or Omniverse rendering, and 40 fully-modelled stations.

### Rejected outright

`deep-research-report (3).md` — a generic Kaggle competition playbook assuming a leaderboard, an
official numeric metric, GPU-scale deep learning, a 3–6 person team and a 12–24 week schedule. None of
those exist here. Following it would have optimised for the wrong thing entirely.

---

## 5. Contingency ladder

If time runs short, cut in this order. The deliverable stays shippable after each cut.

1. Plant-manager trend view and leadership ROI panel
2. Hosted deployment → README quotes the platform doc sentences that ruled each option out
3. MQTT replay demo → keep the `TelemetrySource` ABC and its test; **the test is the proof**
4. AI4I benchmark → the live model still stands alone
5. Graph propagation → inferred stations fall back to the cycle-time prior, still tagged INFERRED, so
   the honesty commitment survives and only the sophistication drops
6. Six stations → four

**Irreducible minimum:** four stations; plain `Environment` chunked at 8 Hz with `sim_time_s ==
tick × 7.5`; the station pattern proven green by the cascade test; Active Period bottleneck detection
with the queue-derivative explanation; a per-station risk score tagged Sensor-verified / Inferred;
SSE + REST + static files; the vitals bar; the kill-stream freeze; the README; and a 3-minute video
carrying all three falsifiability beats.

---

## 6. Open — needs a human

Neither blocks any phase. Defaults are in force until overridden.

| # | Item | Default in force |
|---|---|---|
| 1 | Repo visibility — private with judges as collaborators, flipped public at submission? | Local only. **No remote push without explicit sign-off** |
| 2 | Live hosted deployment (needs a Render account) | Deferred to Phase 10; falls back to a documented limitations entry |
| 3 | `CITATION.cff` authorship — teammate names | Placeholder |
| 4 | "Inferred — 61 % sensor-derived" wording inside the pill | **Adopted.** It extends committed Round 1 copy and is the exact evidence-attribution number, which kills the "is your decay principled?" question outright |
| 5 | Who runs the fresh-machine clean-clone test | Unassigned. Should be someone other than the author, ideally on a laptop without Python installed — any friction they hit is friction a judge hits |
