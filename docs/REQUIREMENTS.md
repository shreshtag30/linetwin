# Requirements Traceability Matrix

Every requirement stated in the Accenture Innovation Challenge 2026 Round 2 brief (Problem Track 4,
"DigitalTwin.ai") mapped to the LineTwin feature that answers it, the module that implements it, and
the artefact that proves it.

**This document was rewritten after an audit found it describing a system that no longer exists.**
It previously claimed a 6-station line (the shipped line is 30), cited an XGBoost model (replaced by
monotone logistic regression), and named six files that are not in the repository —
`scenarios/line6.yaml`, `scenarios/baseline4.yaml`, `tests/test_graph_identities.py`,
`tests/test_monotonic.py`, `ablation.csv`, `docs/tracker.html`. It also asserted a Brier score and a
reliability curve that are computed nowhere. Every row below has been re-checked against the code.

The **Coverage** column is deliberately not all-green. A traceability matrix that claims full
coverage of everything is worth nothing; the value is in being precise about what is built, what is
designed, and what is out of scope.

| Coverage | Meaning |
|---|---|
| **Implemented** | Working code, exercised by a named test or committed artefact |
| **Partial** | Real implementation that does not cover the whole of what the brief describes |
| **Design only** | Answered in prose/architecture, with no running code behind it |
| **Not addressed** | Out of scope for this prototype, stated rather than hidden |

---

## A. Real-World Complexities

The brief lists seven complexities under "Real-World Complexities to Consider".

| # | Complexity (as stated in the brief) | Coverage | LineTwin answer | Module | Evidence |
|---|---|---|---|---|---|
| A1 | Sensor coverage is inconsistent — some stations richly instrumented, others rely entirely on manual checklists | **Partial** | Laplacian harmonic extension infers cycle-time trend at the 8 uninstrumented stations from instrumented neighbours; every value carries an OBSERVED / INFERRED / SIMULATED tag with confidence. **Coverage is a per-station boolean** — there is no partially-instrumented station, which is the commoner real case | `graph/inference.py`, `contracts.py` | `tests/test_inference.py` (both graph identities, *and* a baseline-beating test); `docs/phases/degradation_curve.csv` at 90/80/70/60/50/40 % coverage, both arms |
| A2 | Bottlenecks and defects have multi-causal, **intermittent** root causes (equipment wear, operator variation, upstream part quality, environmental conditions) | **Partial** | Risk drivers are exact linear contributions (`weight × value`), typed `relation: "associative"` at the contract level so causality cannot be claimed by accident. Of the four named causes: **equipment wear and environmental conditions** are modelled as a shared spatially-correlated condition field; **upstream part quality** via `upstream_risk_ewma`; **intermittency** via unplanned stoppages. **Operator variation is not modelled at all** | `risk/scorer.py`, `sim/line.py` (`ConditionParams`), `sim/station.py` (`BreakdownProfile`) | `tests/test_scorer.py`; `tests/test_active_period.py` (breakdowns occur on a running line); UI footnote "Associative indicators, not confirmed causes" |
| A3 | Modifying live production systems (PLCs, line control logic) carries real operational risk; retrofits only during infrequent scheduled maintenance windows | **Design only** | The twin is **read-only by construction**: `TelemetrySource` has no `write` method and no route in `api/` writes to any control logic. That is a genuine architectural property. But there is **no OPC-UA client, no historian adapter, and no maintenance-window model** — the integration story is an architectural seam, not an integration | `sources.py` (`TelemetrySource` ABC) | `tests/test_source_agnostic.py` — the full analytics path runs against `ReplaySource` with `simpy` never imported. This proves decoupling, *not* integration |
| A4 | A defect introduced early may not surface until a much later inspection point; many downstream units carry the same issue; root-cause tracing after the fact is difficult | **Implemented** | Defect genealogy: every unit carries an id and a per-station event log; on detection the chain is walked back with cumulative transfer-delay realignment, reporting origin station, affected unit range, and a monotone confidence. **Limitation**: origin attribution is a cycle-time z-score, so a defect with no timing signature is invisible to it | `diagnostic/genealogy.py`, `contracts.py` (`UnitEvent`) | `tests/test_genealogy.py` — an actually-injected origin station is recovered; `GET /api/twin/genealogy/{unit_id}` |
| A5 | Different stakeholders need very different views of the same twin | **Partial** | Four tabs over one SSE stream: Floor Supervisor (real-time), Plant Manager (rolling window), Leadership (exposure + placement), Method. **The Plant Manager view is not a weekly-planning view** — history is client-side and ~75 sim-minutes, with no persistence across a restart | `web/index.html`, `web/app.js` | Live demo; all four tabs read one `/api/twin/stream` |
| A6 | Extending beyond one line or plant means accounting for variation in layout, equipment vintage, sensor maturity | **Partial** | Station count, zones, cycle-time distributions, buffer capacities, dark set, variant mix, condition drift, breakdown profile and the sensor-gap operator weights are all scenario configuration. **Topology is not**: the line is a hardcoded serial chain (`sim/line.py`) and the inference graph is a hardcoded path (`graph/inference.py`), so parallel stations, rework loops and feeders would need new code. **Equipment vintage is not represented** | `scenarios/line30.yaml`, `sim/line.py` | One scenario file ships. The multi-scenario benchmark builds three *engineered-bottleneck variants* of it programmatically (`tools/run_detector_benchmark_multiscenario.py`) — that is bottleneck variation, not layout variation |
| A7 | Predictive claims must be validated against real outcomes over time; false alarms erode floor-level trust quickly | **Partial** | (i) ANOVA + Tukey–Kramer significance **annotation** — deliberately *not* a suppression gate, because at 8 ticks/s a hard gate would delay detection past the 2 s response requirement, and consecutive active periods are autocorrelated anyway; (ii) held-out line-configuration split, never shuffled; (iii) the operating point (precision, recall, flag rate, false alarms per catch) is now shown in the UI, not just computed; (iv) alerts fire on rising edges only, at the model's own MCC-tuned threshold. **No validation against real outcomes exists — there are none to validate against** | `diagnostic/bottleneck.py`, `risk/scorer.py`, `api/routes.py` | `ml/models/station_risk_metrics.json`; `tests/test_no_config_leak.py`; `tests/test_scorer.py` pins the operating point; `GET /api/twin/model_metrics` |

---

## B. Solutioning Areas

The brief lists six areas under "Solutioning Areas You Could Explore".

| # | Area | Coverage | LineTwin answer | Module | Evidence |
|---|---|---|---|---|---|
| B1 | **Modelling approach** — what to represent explicitly (cycle time, torque, vibration, temperature, throughput) vs. infer indirectly | **Partial** | Explicit: cycle time, per-station state, queue depth, throughput, buffer occupancy. Inferred: defect risk, and cycle-time trend at dark stations. **Torque, vibration and temperature are not modelled** — this is a discrete-event flow model with no physical-signal layer, and the brief names all three. Stated rather than implied | `sim/`, `docs/DATA.md` | Minimum-signal table in `README.md`; `contracts.py:StationSnapshot` is the complete state vector |
| B2 | **Predictive techniques** — anomaly detection, SPC, physics-informed, or ML — and how you'd validate them | **Partial** | Active Period Method (momentary rule) for bottlenecks, benchmarked against seven detectors on sensitivity-derived ground truth; monotone non-negative logistic regression + Platt calibration for defect risk; z-score anomaly detection for genealogy; rolling-horizon forecast. **SPC and physics-informed models are absent** — there is no control chart and no physics in the model | `diagnostic/`, `risk/`, `ml/` | `docs/phases/detector_comparison_multiscenario.csv`; `station_risk_metrics.json` with `"evaluated_on": "config E (UNSEEN)"`; `tests/test_no_config_leak.py`; `tests/test_scorer.py` |
| B3 | **Handling data gaps** — staying useful at partially/uninstrumented stations, **including any low-cost sensing you might propose** | **Partial** | Harmonic extension with exact partition-of-unity evidence attribution ("Inferred — N % sensor-derived"), plus a greedy placement ranking for which dark station to instrument next. **There is no low-cost sensing proposal**: the README's "one photo-eye or PLC completion bit" is a signal that table itself says already exists on virtually every station, so it is not a retrofit proposal. No sensor type, cost, or install effort is specified anywhere | `graph/inference.py`, `graph/placement.py` | `docs/phases/degradation_curve.csv` (graph arm vs zone-base baseline); `GET /api/twin/sensor_placement` |
| B4 | **User experience** — three personas from the same underlying model | **Partial** | See A5 | `web/` | See A5 |
| B5 | **Integration approach** — legacy PLCs, OT data, live-production constraints | **Design only** | See A3. The `TelemetrySource` ABC is the seam a real adapter would implement, and read-only-ness is structural. Nothing has been threaded through that seam except a JSON fixture | `sources.py` | `tests/test_source_agnostic.py` |
| B6 | **Scalability & ROI** — extending to other lines, plants, sites with different starting conditions | **Partial** | Config-driven scaling (A6, with the topology caveat) plus a placement ranking so a site with different sensor maturity knows where to instrument first. **ROI is two stated assumptions multiplied together** (`QC_LAG_UNITS`, `REWORK_COST_DELTA_USD`), both stamped `synthetic — uncalibrated` and rendered as such in the UI. It is an exposure estimate, not an investment case: no capex, payback, or rollout cost is modelled | `scenarios/`, `economics.py`, `graph/placement.py` | `tests/test_economics.py`; Leadership tab formula shown on screen with its constants named |

---

## C. Reference Parameters

The brief supplies three "Reference Parameters (Illustrative — Adapt Freely)" and explicitly invites
us to make our own reasonable assumptions and state them clearly.

| # | Brief parameter | Our position | Stated where |
|---|---|---|---|
| C1 | ~30–50 stations across body construction, paint, final assembly | **30 stations across all three zones** (body 1–12, paint 13–18, final 19–30) — the lower end of the brief's range, taken as a deliberately scoped subset | `scenarios/line30.yaml`; `README.md`, first screen |
| C2 | Meaningful but uneven sensor coverage; a majority instrumented, a meaningful minority on manual checks | **22 of 30 instrumented, 8 dark (27 %)** — proportionally heavier than "a meaningful minority", chosen so the inference layer is load-bearing rather than cosmetic. That claim is now *measured*: the graph layer beats a zone-base baseline by 20–32 % at every coverage level | `scenarios/line30.yaml`; `docs/phases/degradation_curve.csv` |
| C3 | Production pauses for instrumentation only during a small number of scheduled maintenance windows per year | **Not modelled.** The read-only tap needs no window, which is a real property of the architecture — but there is no rollout plan document, no window-length parameter, and no scheduling of the placement ranking into windows. The Leadership "Budget this window" slider is a bare count with no cost or install time behind it | This row; `graph/placement.py` |

---

## D. Deliverables

| # | Deliverable | Owner | Status source |
|---|---|---|---|
| D1 | Detailed Business Proposal — problem framing, solution design, target users, business case and impact, phased roadmap, key risks with mitigations | Teammates | This repo supplies: measured metrics, risk register (`docs/LIMITATIONS.md`), ROI arithmetic and its stated assumptions |
| D2 | **Working Prototype** — functional demonstration of the core mechanism on illustrative or sample data | **This repository** | `README.md` quickstart; `tools/preflight.py` runs exactly what CI runs |
| D3 | Pitch Presentation — presenting proposal and prototype | Teammates | This repo supplies: `docs/DEMO_SCRIPT.md`, `docs/VIDEO_SCRIPT.md`, documented local run |

---

## E. Requirements NOT imposed by the brief

Recorded so that no self-imposed constraint is mistaken for an external one. Confirmed by reading the
full Round 1 problem-statement PDF and the Round 2 brief text:

- **No evaluation rubric or scoring weights** are published.
- **No official metric or leaderboard** exists — this is not a Kaggle-style contest.
- **No technology mandate or ban** — language, framework, and platform choices are entirely ours.
- **No data restriction**, and no requirement to use real enterprise data; simulated data is
  "expected and encouraged".
- **No page/length limit** stated for Round 2 artefacts (Round 1's 3-slide / 3-minute caps applied to
  Round 1 only).
