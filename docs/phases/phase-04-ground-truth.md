# Phase 4 — Ground Truth by Sensitivity Analysis

**LineTwin** · Accenture Innovation Challenge 2026 · Round 2 · Problem Track 4 "DigitalTwin.ai"

---

## Purpose

Compute a true bottleneck by measurement rather than by citation. Every published comparison of
bottleneck-detection methods (Roser & Nakano's MSE table, Kumbhar et al.'s Busy Ratio argument) had to
validate against someone else's line, because they didn't own the simulator producing it. This project
does, which is precisely why Skoogh et al. (2023) name real-world validation of these methods as an
open research problem — this phase is the foundation Phase 5's detector benchmark stands on.

---

## Four problems found and fixed before the harness could be trusted

A quick probe — perturb each station ±15%, measure the throughput effect — was run *before* writing
the formal harness, specifically to catch a bad assumption early rather than build detailed
infrastructure on top of one. It found four real problems in already-committed Phase 3 code, not in
this phase's own code, all documented in full in the Phase 3 record's addendum and summarized here
because they are the reason this phase's ground truth can be trusted at all:

1. **The variant multiplier was computed but never applied.** `Station.run()` called
   `cycle_time_sampler()` with zero arguments; the part carrying `variant_multiplier` was never passed
   in. The "mixed-model" line had no mixed-model behavior. Fixed by threading the part through the
   sampler call.
2. **A single global scalar per variant can never shift the bottleneck.** Uniform scaling preserves
   relative station ranking by construction, which would have made this phase's shifting-bottleneck
   requirement unsatisfiable regardless of how the harness was written. Fixed with per-zone variant
   multipliers.
3. **Even fixed, the configured bottleneck was still an unchallengeable ceiling.** At the original
   `bottleneck_multiplier=1.3` and `suv.final=1.35`, a 100%-SUV mix still fell short (74.2s vs. S17's
   89.6s). Retuned to `1.15`/`1.55` and verified the crossover both arithmetically and in an actual
   simulation run before trusting it.
4. **A third variant of the pipeline-transient trap**, distinct from the two found in Phase 3: even
   once flow has reached every station, comparing cumulative active time over a short fixed window is
   biased toward upstream stations. Fixed by using a verified-sufficient duration (20,000s) for any
   cross-station comparison — which became `_MIN_SAFE_DURATION_S` in this phase's own harness, enforced
   as a loud `ValueError` rather than a silent wrong answer.

---

## The harness (`src/twin/diagnostic/ground_truth.py`)

**Definition:** Kuo & Lim's sensitivity criterion — the bottleneck is the station whose perturbation
produces the largest `|Δ throughput / Δ cycle_time|`. This is the same basis Roser used to produce the
published MSE table this project cites.

**Common Random Numbers make the comparison exact, not noisy.** Because each station has its own
`PCG64` stream (Phase 3), a (baseline, perturbed) pair at the same seed is a genuine paired comparison
— verified empirically that two identical-config, same-seed runs produce bit-identical throughput.
The only source of variation across replications is every *other* station's randomness, which is
exactly the uncertainty a confidence interval should capture.

**Replication and bootstrap CIs.** Twenty seeds per station, non-parametric percentile bootstrap
(2000 resamples, fixed seed so the CI itself is reproducible) on the resulting sensitivity samples.

---

## Results

**Full 30-station ranking** (`ground_truth.csv`, 20 replications each, 20,000 sim-seconds):

| Rank | Station | Mean sensitivity | 95% CI |
|---|---|---|---|
| 1 | **S17** | **−0.546** | **[−0.563, −0.528]** |
| 2 | S18 | −0.077 | [−0.094, −0.060] |
| 3 | S13 | −0.076 | [−0.112, −0.046] |

S17 leads the runner-up by roughly 7×, with non-overlapping confidence intervals — the discrimination
the exit gate requires, met with a wide margin rather than a photo finish.

**Shifting-bottleneck trace** (`shifting_trace.csv`, same replication settings, restricted to the four
plausible candidates identified by the full ranking):

| Sedan | SUV | Hatchback | Ground truth |
|---|---|---|---|
| 1.00 | 0.00 | 0.00 | S17 |
| 0.50 | 0.30 | 0.20 | S17 *(the configured normal mix)* |
| 0.30 | 0.60 | 0.10 | S17 |
| 0.10 | 0.80 | 0.10 | **S19** |
| 0.05 | 0.90 | 0.05 | **S19** |

A genuine, gradual shift — not a coin flip or a single lucky configuration. S17 remains dominant through
60% SUV concentration and only cedes ground truth to a final-assembly station once SUV share crosses
somewhere between 60% and 80%. This is exactly the kind of shifting-bottleneck behavior Kumbhar et al.
(2023) could only *observe* week-to-week in real MES data; here it is produced on demand and verified
against a ground truth their study never had access to.

---

## Deliverables produced

| Artefact | What it does |
|---|---|
| `src/twin/diagnostic/ground_truth.py` | `measure_sensitivity`, `measure_all_stations`, `ground_truth_station`, `shifting_trace` |
| `tools/run_sensitivity_analysis.py` | One-time CLI producing the committed CSVs (~110s full run) |
| `docs/phases/ground_truth.csv` | Full 30-station ranking with bootstrap CIs |
| `docs/phases/shifting_trace.csv` | 5-point variant-mix sweep showing the genuine shift |
| `tests/test_ground_truth.py` | 7 tests — spot-checked on a fast station subset; the CLI script covers the full 30 |

---

## Exit criteria

| Criterion | Status |
|---|---|
| Ground truth stable under re-seeding | Met — a completely disjoint seed set (101–105 vs. 1–5) reproduces the same winner |
| CIs narrow enough to discriminate the top-2 stations | Met, with a wide margin — non-overlapping at ~7× separation, not a marginal call |
| Labelled shifting-bottleneck trace produced | Met — a genuine, gradual shift from S17 to S19 as SUV concentration increases |
| Harness reproducible from one command | Met — `tools/run_sensitivity_analysis.py`, ~110s |
| Duration below the verified-safe floor rejected loudly, not silently wrong | Met — `ValueError` naming the specific transient it guards against |

**44/44 tests passing** (7 new in this phase).

---

## Next

**Phase 5 — Detector Benchmark.** Implement all six detectors (Active Period Method, Busy Ratio,
Arrow, Turning Point, Queue Length, Utilization) behind one interface and score every one of them
against this phase's ground truth — settling the Kumbhar-vs-APM disagreement empirically, on our own
line, for the first time either side of that disagreement has had access to a real answer.
