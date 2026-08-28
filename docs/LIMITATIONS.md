# Limitations

Named plainly, in one place, rather than left scattered across phase records and code comments.
Nothing here is a surprise to the team that built it — each item is cross-referenced to where it
was found and why it was accepted rather than "fixed away."

## Data

**Every parameter is either calibrated against a cited public source or stamped
`synthetic — uncalibrated`.** There is no real per-station MES/PLC dataset behind any cycle time,
buffer capacity, or state-duration in this project — that data is proprietary to individual plants
and not publicly available. `docs/CITATIONS.md` is the full ledger: what is calibrated against
Future Factories V2 and PyScrew, what is a genuine real-data benchmark (AI4I 2020), what is a single
published figure used for one number only (Bosch's ~0.58% defect prevalence, never downloaded), and
what is honestly synthetic with a stated note on what would calibrate it. This is the single largest
scope limitation of the whole project and is stated on the README's first screen, not buried here.

**The 30-station topology, the 22/8 instrumented/dark split, and the three-zone character
(body/paint/final) are illustrative choices**, scoped to be plausible for an automotive line and
heavy enough that inference is load-bearing — not measured from any specific real plant.

## Statistical significance (Phase 5)

Active periods at a blocking station are **autocorrelated** — a consecutive run of active periods at
the same congested station violates ANOVA's independence assumption, which the Tukey–Kramer
significance layer is built on. Rather than drop the test or apply it and claim more rigor than it
has, significance is shipped as a **confidence annotation** ("established" vs "provisional") on the
bottleneck verdict, never as a suppression gate — a hard gate would also delay detection past the
2-second live-response requirement. A second, structural limit: a station with a large, dominant
lead over every other station has *too few* active periods across a short run for the test to ever
reach "established" — not a bug, an inherent property of how few active periods a dominant
bottleneck produces before an operator would already have noticed it by eye.

## Rolling-horizon prediction (Phase 8/9)

`fork_and_predict` is **not a literal deterministic replay** of the live simulation's future. A
running `simpy.Process` wraps a generator, and generator frames are not deep-copyable, so the fork
cannot inherit exactly which unit is how far into its current cycle. What carries into the fork —
station state, active-period history, live perturbation multipliers, current queue depth — is what
actually drives near-term bottleneck behavior; the forecast is "what happens if the current
configuration and congestion level continue," run forward on an independently-seeded RNG stream, not
a claim of exact future replay. It also runs at a throttled ~1 Hz as a background task, not every
tick — a forecast a few ticks apart cannot meaningfully differ, and running it inline was found to
measurably stall the tick loop (see the engine.py commit history for the exact regression: up to
~700ms per fork early in a run, when near-empty queues mean far more discrete events over the same
forecast horizon than once the line is congested).

## Sensor-gap inference (Phase 9)

**The graceful-degradation curve is not graceful.** Measured at 100/90/80/70/60/50/40% coverage,
error jumps from 0 to ~30% the instant any station goes dark, then plateaus roughly flat down to
40% coverage. A meaningful share of that ~30% floor is likely inherent single-sample cycle-time
noise (coefficient of variation up to 0.28 in this line's parameters) rather than an inference
limitation, since the method estimates a smoothed value, not the exact noisy instantaneous sample it
is scored against — but this has not been decomposed further, and the curve is reported as measured,
not reframed to sound better. See `docs/phases/phase-09-sensor-gaps-genealogy.md` and
`docs/phases/degradation_curve.csv`.

**Model B's live risk-scoring features still read ground-truth cycle times for every station,
including dark ones — not this phase's inferred values.** The dashboard's dark-station *display*
value is honestly `INFERRED`; the risk *model's input features* are not yet wired to that inferred
value and instead read the simulator's internal ground truth for feature extraction. Closing this
gap is the first named item for whoever continues this project past Phase 10.

## Defect genealogy (Phase 9)

The origin-attribution confidence is a **continuous, monotone signal derived from a z-score**,
deliberately *not* claimed as calibrated against any real outcome — there is no ground truth for
"is this really the origin station" outside a synthetic line where the origin was injected by hand.
The transfer-delay realignment mechanism is adopted from US 12,353,197 B2, which the project cites
as *disclosing* the mechanism, never as *demonstrating* that this specific attribution method works
— the patent contains no dataset, evaluation, or reported performance of any kind (`docs/PRIOR_ART.md`).

## Leadership ROI (Phase 10)

`src/twin/economics.py`'s "units at risk" and "dollars at stake" are **estimates from stated,
labeled, `synthetic — uncalibrated` assumptions** (`QC_LAG_UNITS`, `REWORK_COST_DELTA_USD`) — not an
audited saving, and not derived from Bosch's published prevalence figure despite both numbers
appearing in the same module. There is no before/after production run to compare against; the
leadership view's own panel copy says so directly rather than only in this document.

## CI and deployment

Windows CI is run and green, but is **non-blocking for demo readiness** — ubuntu is the actual
deploy target, and a Windows-only failure would not gate a phase. See the deployment section of the
Phase 10 record for whether a hosted instance is live and, if not, exactly which platform
constraint ruled it out.
