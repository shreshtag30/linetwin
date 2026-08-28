# Interview Prep — Anticipated Questions, Honest Answers

Questions a judge is likely to ask, answered the way we'd answer them live — including the ones
whose honest answer is a real limitation. Every claim below is cross-referenced to where it's
proven, not just asserted here.

---

**"Is this real data, or did you make it up?"**

Neither, exactly — it's *simulated*: a discrete-event model computing telemetry from first
principles, seed-reproducible, and labeled as model output at every layer. What's real: AI4I 2020
(a genuine public dataset, CC BY 4.0) is what Model A is trained and evaluated on; Future Factories
V2 and PyScrew's published summary statistics calibrate our cycle-time and variability parameters;
Bosch's published ~0.58% defect prevalence calibrates our label balance. What's synthetic is stamped
`synthetic — uncalibrated` in the scenario YAML with a note on what would calibrate it, because the
real per-station MES/PLC data this would need is proprietary and not publicly available to us.
Full ledger: `docs/CITATIONS.md`.

**"How do you know your bottleneck detector is actually correct?"**

Because we own the simulator, we could do something the papers we drew on couldn't: compute a
*true* bottleneck by sensitivity analysis (perturb each station's cycle time, measure the throughput
response under Common Random Numbers) and score all six candidate detectors against it directly —
not cite someone else's dataset's result. Active Period Method won on our line (MSE 0.00179, 100%
top-1 across 10 seeds). We also went in prepared to publish a loss: if another detector had won, the
plan was to switch and say so.

**"Isn't your risk model just overfit to your own simulator?"**

That's the honest-lift gate's whole purpose. We compared it against a single-feature `cycle_time_z`
logistic baseline, evaluated both on a **held-out line configuration** never seen in training —
not a shuffled row split, which would let it memorize a configuration it's also scored on. Measured
lift was +44.7%, clearing our own pre-committed ~10% bar. Had it not cleared, the plan — made in
advance — was to publish the comparison anyway and reframe it honestly as a modest combiner, not
suppress the number.

**"What happens at a station with no sensor?"**

It's shown as `INFERRED`, never silently presented as a measurement. The value comes from a
harmonic-extension graph solve over the line's adjacency structure (Zhu, Ghahramani & Lafferty
2003), and the confidence shown is `sensor_share` — an exact partition-of-unity quantity from that
same linear system, zero tuning parameters, not a hand-picked decay curve. We also measured what
happens as coverage drops (100%→40%) and it is **not** a graceful curve — error jumps sharply the
instant any station goes dark, then plateaus. We say so in `docs/LIMITATIONS.md` rather than
describe it as "graceful degradation" just because that's the expected framing.

**"What's your biggest actual weakness right now?"**

Model B's live risk-scoring features still read the simulator's ground-truth cycle time for dark
stations, not the Phase 9 inferred value — the display layer is honest, the feature-extraction layer
underneath it isn't fully wired to that same honesty yet. Named directly in
`docs/phases/phase-09-sensor-gaps-genealogy.md` and `docs/LIMITATIONS.md` rather than left for
someone else to discover.

**"Why is this a digital twin and not just a fancier simulation?"**

Under Kritzinger et al. (2018)'s own taxonomy, strictly it's a **Digital Shadow** — automated
one-way data flow from the physical (here, simulated) process to the digital model, with no
write-back control path. We say this plainly rather than claim more. Under Grieves & Vickers (2017)
it is a **Digital Twin Prototype**, since a virtual construct exists before any physical instance —
which is exactly the brief's own framing. Under Villegas et al. (2025)'s maturity ladder it sits at
the **Predictive** level (bottleneck forecasting, defect-risk scoring), one level short of
prescriptive control action.

**"How would this handle false alarms in production?"**

Two mechanisms, stated with their real limits. First, the significance annotation on every
bottleneck verdict ("established" vs "provisional") — not a suppression gate, because active periods
at a congested station are autocorrelated (violates ANOVA's independence assumption) and a hard gate
would delay the 2-second live response. Second, isotonic calibration on Model B's risk score, so a
"70% risk" actually means something close to a 70% empirical rate rather than an uncalibrated
score. Neither claims to eliminate false alarms; both are named honestly rather than oversold.

**"What's the real ROI here?"**

The leadership view's numbers are labeled an **estimate from stated assumptions**, not an audited
saving — there's no real before/after production run to compare against, only our own simulated risk
scores. `src/twin/economics.py`'s constants (`QC_LAG_UNITS`, `REWORK_COST_DELTA_USD`) are both
stamped `synthetic — uncalibrated`, and the panel says this in its own copy, not just in a document
a judge would have to go looking for.

**"Why SSE instead of WebSockets?"**

The two data flows are asymmetric — 8 Hz one-way state, sub-1 Hz control — and SSE gets automatic
reconnection for free. Decisively for a live demo: `curl -N` shows real server-pushed frames in any
terminal with nothing installed, which is exactly what the falsifiability video beat needs (a
browser's Network→EventStream panel and a terminal `curl` showing identical tick numbers side by
side). Full reasoning: `docs/adr/ADR-002-transport.md`.

**"What would you do with more time / a real plant's data?"**

Wire Model B's features to the Phase 9 inferred values (the named open item above); calibrate every
`synthetic — uncalibrated` parameter against a real per-station MES join once available; and
decompose the degradation curve's ~30% error floor to separate genuine inference limitation from
single-sample measurement noise, which the current experiment doesn't distinguish.
