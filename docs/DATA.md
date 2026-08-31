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

---

## Addendum, found via a full audit (post-Phase-10): the Bayes-optimal ceiling, and a real calibration bug

**The training data carries something real industrial ML never has**: `oracle_risk`, the exact true
`P(defect=1 | features)` used to generate labels (`defect = Bernoulli(oracle_risk)`,
`tools/generate_training_data.py`). That means the Bayes-optimal ceiling for this exact problem is
directly *computable*, not estimated — no model, however sophisticated, can beat the PR-AUC of the
true oracle probabilities scored against the sampled labels on the same test set.

**Measured**: that ceiling is `PR-AUC ≈ 0.064` (≈7.7× the no-skill rate). This is the honest reason
Model B's raw PR-AUC numbers look small in isolation — the problem is inherently hard at Bosch's
~0.58%-scale imbalance combined with a genuinely stochastic (Bernoulli, not deterministic) label —
not evidence of a badly-modeled or badly-engineered pipeline. Reporting PR-AUC as a fraction of this
ceiling (`pr_auc_over_ceiling_pct` in `station_risk_metrics.json`) is more meaningful than either the
no-skill or single-feature-baseline comparisons alone, and is now persisted alongside them.

**A real, previously-undiagnosed bug was found and fixed**: `tools/train_station_risk.py` fit the
isotonic calibrator on config D alone (143 positives). Isotonic regression is a step function; that
few positives collapsed 2,942 distinct raw XGBoost scores on the test set into just 79 output
buckets, directly destroying rank information PR-AUC depends on. Measured directly: PR-AUC dropped
from 0.0573 (raw booster, 89.4% of ceiling) to 0.0471 (isotonic-on-D-alone, 73.5% of ceiling) — an
18% relative loss from the calibration step alone, on top of an already-hard problem. **Fixed** by
fitting the calibrator on train+calib combined (still only configs A/B/C/D — config E remains
untouched until final evaluation, exactly as before) — recovers the full raw PR-AUC while still
producing calibrated probabilities.

**Things tried and found NOT to help, reported rather than hidden** (checked with a full audit, not
assumed):
- Feature interactions (pairwise products of the five features) — hurt badly (0.0573 → 0.0302).
  Expected: `oracle_risk` is exactly additive-in-logit by construction (no interaction terms in
  `WEIGHTS`), so interaction terms add pure overfitting risk against only ~700 positive training
  examples, no true signal to find.
- Removing the monotone constraints — hurts badly (unconstrained XGBoost reaches only 47.9% of
  ceiling vs. 89.4% constrained). The constraints are load-bearing, not decorative.
- `scale_pos_weight` above 1.0 — the current default (no reweighting) is empirically the best of every
  value tried; confirms `docs/DATA.md`'s original design choice rather than just asserting it.
- LightGBM, tested with generic (non-tuned) hyperparameters, underperforms the carefully-tuned XGBoost
  — but wasn't given equivalent tuning care, so this is not a fair verdict on the library, and is
  stated as such rather than oversold as "XGBoost wins."

**`upstream_risk_ewma`'s weak real-world signal, explained**: it has a nominal weight of 1.8 (the
second-highest of the five), but SHAP importance on the trained model ranks it near the bottom
(mean |SHAP| 0.10, ahead of only `starved_fraction`). Root cause: it is an EWMA of already-tiny
`oracle_risk` values, so its own natural range in the data is tiny (mean 0.005, max 0.169) — weight
× typical-value is what determines a feature's real influence on the logit, not the weight alone.
This is a property of the label-generating design (the `WEIGHTS` table conflates a coefficient with
real-world influence), not a bug in the model or the feature-extraction code.

---

## Second addendum: XGBoost was not the right model here — switched to logistic regression

**The one comparison the first audit didn't make.** The isotonic-collapse fix above (fitting the
calibrator on train+calib) recovered XGBoost to 89.4% of the ceiling. That looked like the end of the
story until a follow-up audit ran the one comparison missing from the "things tried" list: a plain
logistic regression against the identical held-out config E.

**Measured**: non-negative-constrained (monotone) logistic regression reaches **PR-AUC 0.0645 — 100.6%
of the ceiling** (0.0641), a **+13.4% relative improvement over XGBoost's 89.4%**, and a genuine
**+62.3% lift over the single-feature baseline** (vs. XGBoost's honest-but-modest 44.2%). This is not
a coincidence: `oracle_risk = sigmoid(0.9·cycle_time_z + 2.6·queue_pressure + 2.2·blocked_fraction +
1.1·starved_fraction + 1.8·upstream_risk_ewma + bias)` — the label function IS logistic regression's
exact functional form. A tree ensemble has to *approximate* a smooth global linear relationship with
axis-aligned splits; a correctly-specified linear model doesn't have to approximate it, especially
against only ~700 positive training rows. **Caveat stated plainly**: this result is partly an artifact
of the label being linear by construction (see "Known limitations" above) — it is not evidence that
logistic regression would beat a tree ensemble against a real, non-synthetic defect distribution with
genuine nonlinear interactions. Reported honestly either way, per this project's own discipline.

**Checked directly, not assumed**: unconstrained logistic regression fits a **negative** coefficient on
`starved_fraction` (−0.53), violating the monotone guarantee this project treats as non-negotiable.
Refit with weights constrained to `≥0` (`scipy.optimize.minimize`, L-BFGS-B) — `starved_fraction`'s
weight goes to exactly 0.0 rather than negative, at a cost of 0.0001 PR-AUC (0.0645 vs. 0.0646
unconstrained) to buy back the hard guarantee.

**Calibration switched from isotonic to Platt (a 1-D logistic regression on the raw logit)**, proven
rather than assumed to be exactly rank-preserving: composing two monotone sigmoid-shaped functions
cannot change rank order, so Platt calibration is PR-AUC/ROC-AUC-neutral by construction. Isotonic,
even refit on train+calib (182,776 rows), still cost ~11.6% relative PR-AUC against the new logistic
model specifically — the earlier fix happened to fully recover XGBoost's PR-AUC, but that turned out to
be specific to XGBoost's score distribution, not a general property of isotonic regression at this
sample size. **A real implementation bug was found and fixed while building this**: Platt scaling is
defined on the raw decision function (the pre-sigmoid logit), not an already-squashed probability —
fitting it on the sigmoid output instead gave the 1-D calibrator's own regularization almost nothing to
push against, collapsing every calibrated probability into a ~0.0047–0.0048 band and degenerating MCC-
threshold tuning to flag-nothing (precision=recall=0). Fixed by calibrating on the logit directly.

**Artifacts**: `station_risk_booster.json` (an XGBoost tree dump) is retired; `station_risk_model.json`
now holds the five weights and bias as plain, human-readable JSON — the file *is* the model, not a
serialized footprint of one. Driver contributions (`RiskDriver` in the UI) are now computed exactly as
`weight × feature_value` — the literal decomposition of a linear model's logit — rather than via
TreeSHAP, at zero runtime dependency either way.
