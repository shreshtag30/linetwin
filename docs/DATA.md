# Data & Label-Generating Process

Written **before** any metric in Phase 8 is computed — per this project's own discipline (`docs/
CITATIONS.md`, `docs/DECISIONS.md`): a metric without a documented process behind it is not evidence,
it's a number. This document is that process, for the record, not retrofitted to match a result.

---

## What "defect risk" means here, stated plainly

LineTwin's simulation has no physical defect model — no dimensional tolerances, no torque curves, no
material properties. `oracle_risk` is a **synthetic, explicitly-defined function of simulated stress
indicators**, chosen so that a defensible, monotone relationship exists between "this station is under
stress" and "a unit passing through it right now is more likely to be defective." It is not a claim
about real defect physics. It exists so that:

1. Model B (the live scorer) has something honest to learn — a real, computable, non-trivial function
   of features, not noise dressed up as a label.
2. The relationship is monotone by construction, matching the domain-knowledge framing already used for
   Model B's `monotone_constraints` (per the original build brief this project inherited: "shape is
   imposed domain knowledge, not learned").
3. The base rate can be calibrated to a real, cited number (Bosch's ~0.58% prevalence, arXiv:2101.11715)
   as a **calibration target only** — never as training data. `docs/CITATIONS.md` §1 already commits to
   this; this document is where that commitment is executed.

---

## The five features

Chosen to be **honestly computable from what this simulation actually tracks** — none of them resurrect
a cut feature (breakdowns/MTBF, `docs/DECISIONS.md` §4) under a new name.

| Feature | Definition | What it captures |
|---|---|---|
| `cycle_time_z` | `(this unit's cycle_time_s − this station's running mean) / running std`, per-station | Unusual slowness relative to the station's own baseline, not an absolute threshold — a body-zone station and a paint-zone station have different normal cycle times |
| `queue_pressure` | `queue_depth / buffer_capacity` at the moment this unit entered | Upstream congestion pressing on this station |
| `blocked_fraction` | Fraction of the last `WINDOW_S` seconds this station spent BLOCKED | Downstream congestion — this station can't push its output out |
| `starved_fraction` | Fraction of the last `WINDOW_S` seconds this station spent STARVED | Upstream starvation — this station lacks input |
| `upstream_risk_ewma` | Exponentially-weighted moving average of the immediately-upstream station's `oracle_risk`, carried forward per unit | Defect propagation: a part arriving with elevated risk carries that risk forward. Reuses the same directional assumption as the sensor-gap weighting already committed to in `scenarios/line30.yaml` (`w_down=1.0` — "defects ride the part forward") |

`WINDOW_S = 300` (5 minutes of sim time) for the two fraction features — long enough to smooth past a
single cycle, short enough to reflect current conditions rather than the whole run's history.

**Implementation note, recorded because a first attempt got this wrong:** the fractions are estimated
by sampling `Station.state` directly once per tick and taking the share of samples in the window that
were BLOCKED/STARVED — not by diffing `Station.time_in_state`'s cumulative totals between samples. An
earlier version of `features.py` did the latter and was wrong: `time_in_state` is only updated *at a
state transition*, so a station holding one state for many consecutive samples shows a delta of zero
each time and then dumps its entire accumulated duration into a single sample the instant it finally
transitions — measured directly at 4.59 for a fraction that must be bounded to `[0, 1]`. Presence
sampling is immune to this because it never depends on how long any one state lasts.

**Monotonicity direction, stated as a modeling choice, not an empirical finding:** all five features are
defined so that risk is assumed to increase as each one increases (`monotone_constraints=(1,1,1,1,1)`).
`starved_fraction`'s direction is the least intuitive and is worth stating explicitly: the assumption is
that a station idling and then suddenly fed work is a stress event too (rushed handling, work-in-progress
degradation while waiting), not that starvation itself is protective. This is the same directional
assumption the inherited build brief's original five-feature design made; it is not re-derived here from
data, because there is no real data to derive it from.

---

## The label function

```
raw_score = 0.9·cycle_time_z + 2.6·queue_pressure + 2.2·blocked_fraction
          + 1.1·starved_fraction + 1.8·upstream_risk_ewma + bias

oracle_risk = sigmoid(raw_score)
defect      = Bernoulli(oracle_risk)     # sampled once per completed unit
```

The five weights are **synthetic, hand-chosen to keep all five monotone directions positive with
`queue_pressure` and `blocked_fraction` weighted heaviest** (congestion is the dominant stress signal in
a discrete-event line with no physical wear model). `bias` is the one term that is **numerically
calibrated**, not hand-chosen: solved so that the mean `oracle_risk` across a long baseline run (no
perturbation, sedan-heavy mix) lands at Bosch's cited **0.58%** prevalence. This is calibration to a
published number, not training on Bosch's dataset — no Bosch row is ever read, downloaded, or fitted
against; only the single summary prevalence statistic is used as a target, exactly as `docs/CITATIONS.md`
commits to.

`upstream_risk_ewma` makes this label function **recursive along the line**: station *i*'s risk depends
on station *i−1*'s risk, which depended on *i−2*'s, and so on. The first station in the line has no
upstream and uses `upstream_risk_ewma = 0`.

---

## The five line configurations

Never a shuffled `train_test_split` — adjacent ticks are near-duplicates of each other (same station,
similar recent history), so a shuffled split would leak information between train and test through
temporal adjacency alone. The split is **by configuration**, each a distinct variant-mix scenario run
with its own seed:

| Config | Variant mix (sedan/suv/hatchback) | Role |
|---|---|---|
| A | 70/20/10 | Train |
| B | 50/30/20 (the scenario default) | Train |
| C | 30/50/20 | Train |
| D | 20/30/50 | Train |
| **E** | 10/80/10 (heavy SUV — the shifting-bottleneck mix from Phase 4) | **Held out. Never trained on.** |

`test_no_config_leak.py` (Phase 8) asserts the row sets for config E and configs A–D share no unit
across the boundary and that E is excluded from every training/fitting step, not merely reported as
excluded.

---

## Known limitations, stated rather than discovered later

- `oracle_risk` is a function this project wrote. Model B is therefore learning to approximate a known,
  computable function from a subset of its own inputs (all five features, minus whatever monotone
  binning/regularization loses) — this is a **capability proof**, not evidence the model would perform
  this well against a real defect distribution with real confounders and noise a synthetic label doesn't
  have.
- The 0.58% prevalence calibration is a single summary statistic from a real, cited source. It is not
  validation that the *shape* of this synthetic label's distribution resembles Bosch's real distribution
  in any other respect (feature correlations, temporal clustering, multi-modal failure causes).
- `upstream_risk_ewma`'s recursive definition means early-line label noise compounds forward. This is a
  deliberate modeling choice (defects propagate), not an oversight, but it does mean final-zone stations'
  labels are the least independent of the five features by construction.
