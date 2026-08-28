# LineTwin

A live discrete-event digital twin of a vehicle assembly line — real-time bottleneck detection
validated against our own ground truth, defect-risk prediction with a stated honest-lift baseline,
and graph-based inference at sensor-poor stations.

Built for the **Accenture Innovation Challenge 2026, Round 2, Problem Track 4 "DigitalTwin.ai"** as
the Working Prototype deliverable. **160/160 tests green, CI green on ubuntu + windows.**

---

## Quickstart

```bash
git clone <this-repo> && cd linetwin
uv sync --all-extras
uv run python tools/generate_training_data.py   # simulates ~5 configs of labeled data; a few seconds, no network
uv run python tools/train_station_risk.py       # trains Model B (monotone XGBoost + isotonic calibration)
uv run python tools/run_server.py
```

Then open **http://127.0.0.1:8000/**. Three tabs — Floor Supervisor, Plant Manager, Leadership —
share one live stream. Try the perturbation slider on the Floor Supervisor tab (any station,
any multiplier 0.1×–10×) and watch the bottleneck, throughput, and WIP charts respond within ~2
seconds; then check the Plant Manager tab for the rolling-horizon forecast and the
shifting-bottleneck timeline it produces.

`uv run pytest -q` runs the full suite (160 tests, ~50s). `uv run ruff check .` for lint.

---

## Scope, stated up front

LineTwin models **30 stations across three zones** (body construction, paint, final assembly) — a
deliberately scoped subset of the 30–50 station line the brief describes. Station count, topology,
cycle-time distributions, and per-station instrumentation are all configuration
(`scenarios/line30.yaml`) — scaling to a different line is a new YAML file, not new code.

**22 of 30 stations are instrumented; 8 are dark.** The inference layer (Phase 9) is therefore
load-bearing, not decorative — verified with a real degradation-curve experiment, reported below
exactly as measured.

## What this is, precisely

Under Kritzinger et al. (2018) this is a **Digital Shadow** — automated one-way data flow, no
write-back to any physical system. Under Grieves & Vickers (2017) it is a **Digital Twin
Prototype**: a virtual construct that exists before any physical instance, which is what the brief
asks for. Under Villegas et al. (2025) it sits at the **Predictive** maturity level — one step short
of prescriptive control action.

All simulation data is exactly that — simulated, from stated first principles, seed-reproducible,
and labeled as model output at every layer. Parameters are calibrated against cited public sources
where such sources exist, and stamped `synthetic — uncalibrated` where they do not. Full ledger:
`docs/CITATIONS.md`.

---

## Minimum signal required, per layer

Stated honestly because it is not the same answer for every layer, and the brief's uneven-sensor
scenario deserves a real answer rather than one blanket claim.

| Layer | Minimum signal needed | Typically already exists on an automotive line? | Does graph inference cover a gap here? |
|---|---|---|---|
| **Flow / bottleneck detection** | One bit per station: a photo-eye or PLC completion signal marking "a unit left this station, at this time." | Yes — virtually every automotive station already emits this. Starved-vs-blocked state is *derived* from adjacent buffer occupancy, not a separate sensor. | Not needed here — this signal is assumed present everywhere; the Active Period Method runs on it directly. |
| **Quality / defect-risk** | The station's actual cycle-time distribution and operational-state features (queue pressure, micro-stoppage rate, upstream risk). | No — this is where real instrumentation gaps genuinely bite; a manual-check station may report nothing between inspections. | **No.** Harmonic extension (Phase 9) infers a station's *cycle-time trend* from its neighbors — it does not and cannot infer whether a *specific unit* was defective. A missing quality signal is not recoverable by graph propagation, and we say so rather than imply otherwise. |
| **Defect genealogy** | The per-unit event log (already required by the flow layer) plus one defect flag from final inspection. | Yes, for the flow half; the inspection flag is the one new requirement, and it can arrive well downstream of the actual defect. | Not applicable — genealogy walks the existing per-unit log backward in time; it does not need to infer any missing per-unit signal, only realign timestamps by transfer delay. |

---

## Capability ladder

Every row names the test that actually proves it — not a description of intended behavior.

| Capability | Proven by |
|---|---|
| A downstream slowdown propagates upstream as real blocking, not just a local slowdown | `tests/test_cascade.py` |
| Active-period counting matches the cited method exactly (a breakdown doesn't end an active period) | `tests/test_active_period.py` |
| Six bottleneck detectors, scored against ground truth computed from our own sensitivity analysis | `tests/test_ground_truth.py`, `tests/test_bottleneck.py`, `tests/test_detectors.py` |
| The wire contract (`contracts.py`) is frozen and every fixture conforms to it | `tests/test_fixture_matches_contract.py` |
| The analytics path runs with `simpy` unimported — genuinely source-agnostic | `tests/test_source_agnostic.py` |
| Model B never trains and evaluates on the same line configuration | `tests/test_no_config_leak.py` |
| Model B's risk score is monotone in `cycle_time_z` across the full feature range | `tests/test_scorer.py` |
| Feature extraction samples live state correctly (no cumulative-delta drift) | `tests/test_features.py`, `tests/test_labels.py` |
| Model A reproduces a real dataset benchmark, including a deliberately-run SMOTE failure case | `tests/test_benchmark_public.py` |
| Rolling-horizon prediction produces a materially different forecast at a materially different horizon | `tests/test_rolling_horizon.py` |
| The two mandatory graph identities (exact mean at λ=0; exact partition of unity) hold numerically | `tests/test_inference.py` |
| Greedy sensor placement only ever recommends currently-dark stations, and improves the rest | `tests/test_placement.py` |
| Defect genealogy correctly names an actually-injected origin station | `tests/test_genealogy.py` |
| The live engine's tick timing holds under real-time pacing, restart, and a background forecast task | `tests/test_engine.py` |
| Every REST route behaves correctly, including a live subprocess SSE stream | `tests/test_api.py` |
| The `ml` optional-dependency group is never imported by the server path | `tests/test_server_import_hygiene.py` |
| ROI arithmetic is correct and non-negative across a realistic risk range | `tests/test_economics.py` |

---

## The honesty ledger

The discipline that makes the rest of this credible, summarized here and detailed in full in the
linked documents:

- **`docs/CITATIONS.md`** — every verified source, thirteen individually-named figures we explicitly
  do *not* use, and every one of our own numbers with its mandatory qualification.
- **`docs/DATA.md`** — Model B's label-generating process, published *before* any metric was
  computed against it.
- **`docs/PRIOR_ART.md`** — our position on US 12,353,197 B2: what it *discloses*, what we adopt,
  how we differ, and the mandatory citation discipline (never "demonstrates").
- **`docs/LIMITATIONS.md`** — every known limitation in one place, including the ones a demo would
  usually prefer to leave out (the degradation curve is not graceful; Model B's features don't yet
  consume Phase 9's inferred values; the leadership ROI is an estimate, not an audited saving).
- **`docs/INTERVIEW.md`** — anticipated judge questions with the honest answer, not the convenient
  one.
- **`docs/adr/`** — four architecture decision records (simulation core, transport, ML data
  provenance, sensor gap), each stating the alternative considered and why it lost.

---

## Integration and scalability

**Integration.** The twin is read-only with respect to any physical system it would sit beside — a
`TelemetrySource` ABC (`src/twin/sources.py`) is the seam a real PLC/MES feed would implement; the
simulator is one concrete implementation of it, proven interchangeable by
`tests/test_source_agnostic.py` running the entire diagnostic and risk-scoring path with `simpy`
itself unimported.

**Scalability.** Station count, zone topology, cycle-time distributions, buffer capacities, and the
instrumented/dark split are all one YAML file (`scenarios/line30.yaml`). A different line — more
stations, a different zone mix, a different sensor-coverage ratio — is a new scenario file, not new
code; every module from the simulation core through the graph-inference layer is already written
against `LineConfig`, not against any specific station count.

---

## Documentation

| File | Contents |
|---|---|
| `docs/REQUIREMENTS.md` | Every brief requirement traced to a feature, module, and its evidence |
| `docs/CITATIONS.md` | Verified sources · individually-banned figures · our own numbers and their mandatory qualifications |
| `docs/DECISIONS.md` | Identity, architecture, scope cuts, contingency ladder |
| `docs/PRIOR_ART.md` | Position on US 12,353,197 B2 (Accenture) and mandatory citation discipline |
| `docs/DATA.md` | Model B's label-generating process, published before any metric |
| `docs/LIMITATIONS.md` | Every known limitation, named plainly, in one place |
| `docs/INTERVIEW.md` | Anticipated judge questions, answered honestly |
| `docs/adr/` | Four architecture decision records |
| `docs/phases/` | Per-phase build record, one markdown + PDF per phase |
| `docs/tracker.html` | Live build progress |

## Licence

Apache-2.0 — chosen over MIT for its explicit patent grant.
