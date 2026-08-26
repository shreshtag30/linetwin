# Requirements Traceability Matrix

Every requirement stated in the Accenture Innovation Challenge 2026 Round 2 brief (Problem Track 4,
"DigitalTwin.ai") mapped to the LineTwin feature that answers it, the module that implements it, and
the artefact that proves it.

Rows marked **PROPOSAL** are answered in the Business Proposal / Pitch (teammate-owned deliverables);
this repository supplies the supporting evidence named in the Evidence column.

---

## A. Real-World Complexities

The brief lists seven complexities under "Real-World Complexities to Consider". All seven are addressed.

| # | Complexity (as stated in the brief) | LineTwin answer | Module | Evidence |
|---|---|---|---|---|
| A1 | Sensor coverage is inconsistent — some stations richly instrumented, others rely entirely on manual checklists | Laplacian harmonic extension infers state at uninstrumented stations from instrumented neighbours; every value carries an OBSERVED / INFERRED / SIMULATED tag with confidence and freshness | `graph/inference.py` | `test_graph_identities.py`; graceful-degradation chart at 100/80/60/40 % coverage; `ablation.csv` |
| A2 | Bottlenecks and defects have multi-causal, intermittent root causes hard to isolate from data alone | Risk drivers surfaced via exact TreeSHAP and labelled **associative, not causal**, in the UI and the README. We do not claim causal isolation | `risk/scorer.py` | README "associative not causal" section; driver labels rendered in `web/` |
| A3 | Modifying live production systems (PLCs, line control logic) carries real operational risk; retrofits only during infrequent scheduled maintenance windows | Read-only telemetry tap (OPC-UA / historian), never in the control path, never writing to a PLC. Zero maintenance windows required for Phase 1 of the rollout | `sources.py` (`TelemetrySource` ABC) | `test_source_agnostic.py` — full analytics pipeline runs from hand-written frames with `simpy` unimported |
| A4 | A defect introduced early may not surface until a much later inspection point; many downstream units carry the same issue; root-cause tracing after the fact is difficult | Defect genealogy: each unit carries an id and per-station event log; on detection we walk the chain back with cumulative transfer-delay realignment and report origin station, affected unit range, and confidence | `line.py` (event log), Phase 8 genealogy module | Genealogy trace reproduced in tests; demo beat in the video |
| A5 | Different stakeholders need very different views of the same twin | Three distinct views over one model: floor supervisor (real-time), plant manager (rolling-window trends), leadership (investment case) | `web/` | Three views demonstrated live; each labelled in-UI with the requirement it answers |
| A6 | Extending beyond one line or plant means accounting for variation in layout, equipment vintage, sensor maturity | Station count, topology, cycle-time distributions and per-station instrumentation are all config — a new line is a new YAML file, not new code | `scenarios/*.yaml` | Multiple scenario files run by the same engine; `baseline4.yaml` and `line6.yaml` both green |
| A7 | Predictive claims must be validated against real outcomes over time; false alarms erode floor-level trust quickly | Three separate mechanisms: (i) ANOVA + Tukey–Kramer significance gate before naming any bottleneck; (ii) isotonic calibration with Brier score and reliability curve; (iii) a published single-feature baseline beside every headline metric | `diagnostic/bottleneck.py`, `risk/scorer.py` | `metrics.json` carries PR-AUC, Brier, MCC-with-threshold, and the baseline row; significance gate returns "no significant bottleneck" when the difference is not significant |

---

## B. Solutioning Areas

The brief lists six areas under "Solutioning Areas You Could Explore".

| # | Area | LineTwin answer | Module | Evidence |
|---|---|---|---|---|
| B1 | **Modelling approach** — what to represent explicitly vs. infer indirectly, especially at sensor-poor stations | Explicit: cycle time, per-station state, queue depth, throughput, buffer occupancy. Inferred: defect risk, and all state at uninstrumented stations. A per-layer minimum-signal table states the least instrumentation each layer needs | `sim/`, `docs/DATA.md` | Minimum-signal table in README; `scenarios/line6.yaml` marks C and E uninstrumented |
| B2 | **Predictive techniques** — and how you'd validate them before trusting their output | Active Period Method for bottlenecks (Roser et al.; independently validated by Ragazzini et al. 2024) + monotone XGBoost for defect risk. Validation: held-out **line configuration** split (never shuffled), isotonic calibration, PR-AUC/Brier/MCC-at-threshold, mandatory single-feature logistic baseline, significance gate | `diagnostic/`, `risk/`, `ml/` | `metrics.json` with `"evaluated_on": "config E (UNSEEN)"`; `test_no_config_leak.py`; `test_monotonic.py` |
| B3 | **Handling data gaps** — staying useful at partially/uninstrumented stations, including low-cost sensing | Harmonic extension over the line graph with exact partition-of-unity evidence attribution ("Inferred — N % sensor-derived"). Low-cost sensing proposal: a single photo-eye or PLC completion bit per station unlocks the entire flow/diagnostic layer; the quality layer is where the gap genuinely bites | `graph/inference.py` | Graceful-degradation chart; sensor-placement value ranking; minimum-signal table |
| B4 | **User experience** — three personas from the same underlying model | See A5 | `web/` | See A5 |
| B5 | **Integration approach** — legacy PLCs, OT data, live-production constraints | Read-only tap via OPC-UA or the plant historian; the `TelemetrySource` ABC is the architectural evidence this is designed for, not merely claimed. Never in the control path | `sources.py` | `test_source_agnostic.py`; README integration section |
| B6 | **Scalability & ROI** — extending to other lines, plants, sites with different starting conditions | Config-driven scaling (A6) + sensor-placement value ranking so a new site with different sensor maturity knows where to instrument first | `scenarios/`, Phase 8 ranking | Leadership ROI panel; README scalability section |

---

## C. Reference Parameters

The brief supplies three "Reference Parameters (Illustrative — Adapt Freely)" and explicitly invites us
to make our own reasonable assumptions and state them clearly.

| # | Brief parameter | Our position | Stated where |
|---|---|---|---|
| C1 | ~30–50 stations across body construction, paint, final assembly | We model **6 stations (A–F)** as a deliberately scoped illustrative subset. The brief marks these parameters "directional, not a fixed dataset". Scaling to 30–50 is a YAML change, not a code change | README, first screen — stated before it can be inferred |
| C2 | Meaningful but uneven sensor coverage; a majority instrumented, a meaningful minority on manual checks | 4 of 6 stations instrumented, 2 (C and E) uninstrumented — a 33 % gap, proportionally heavier than the brief's "meaningful minority", chosen so the inference layer is genuinely load-bearing rather than cosmetic | `scenarios/line6.yaml`; README |
| C3 | Production pauses for instrumentation only during a small number of scheduled maintenance windows per year | Phase 1 of the proposed rollout requires **zero** maintenance windows (read-only tap on signals that already exist). Only the low-cost retrofit sensing proposal consumes a window, and the placement ranking exists to make that window count | README integration + scalability sections |

---

## D. Deliverables

| # | Deliverable | Owner | Status source |
|---|---|---|---|
| D1 | Detailed Business Proposal — problem framing, solution design, target users, business case and impact, phased roadmap, key risks with mitigations | Teammates | This repo supplies: roadmap, risk register, ROI figures, target-user definitions |
| D2 | **Working Prototype** — functional demonstration of the core mechanism on illustrative or sample data | **This repository** | `docs/tracker.html` |
| D3 | Pitch Presentation — presenting proposal and prototype | Teammates | This repo supplies: 3-min video, screenshots, live demo URL or documented local run |

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
