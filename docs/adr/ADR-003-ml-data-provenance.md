# ADR-003 — ML Data Provenance: Two Models, Seam Stated Loudly

**Status:** Accepted (Phase 8)

---

## Context

The brief asks for defect-risk prediction. There is no public per-station, per-unit assembly-line
risk dataset with the exact feature set this project needs (queue pressure, upstream risk EWMA,
micro-stoppage rate — all derived from live simulation state). There *is* a real public dataset
adjacent to the domain (AI4I 2020, machine-failure prediction, CC BY 4.0) and a real published
prevalence figure from automotive manufacturing (Bosch, ~0.58% defect rate, one number only, never
downloaded — its features are anonymized with no released semantics). Conflating "trained on real
data" and "trained on our own simulator's labels" would be exactly the kind of unstated seam this
project's honesty discipline exists to prevent.

## Decision

**Two separate models, never conflated, with the seam stated in the UI and the README, not just
in a code comment:**

- **Model A** (`ml/benchmark_public.py`) trains and evaluates *only* on AI4I 2020, a real dataset,
  and is never imported by the server (`tests/test_server_import_hygiene.py` enforces this via the
  `ml` optional-dependency group, which pandas/ucimlrepo/matplotlib/imbalanced-learn live in).
  Its purpose is to demonstrate competent ML methodology against a real benchmark — logistic floor,
  calibrated XGBoost, an uncalibrated `scale_pos_weight` variant, always-predict-zero, and a
  **deliberately reproduced SMOTE failure case** (resample-then-cross-validate collapse: 0.9988
  inflated PR-AUC vs 0.7455 real, both reported).
- **Model B** (`src/twin/risk/scorer.py`) trains and evaluates *only* on this project's own
  simulated labels (`oracle_risk`, documented in `docs/DATA.md` *before* any metric was computed —
  the single most load-bearing sequencing decision in this ADR, since publishing the label recipe
  after seeing results would make every subsequent number unfalsifiable) and is the one actually
  wired into the live twin.

## Why monotone constraints, isotonic calibration kept separate, and a held-out *configuration*

`monotone_constraints=(1,1,1,1,1)` on all five features, `max_bin=512`, `max_delta_step=1`, no
resampling, no `scale_pos_weight` — deliberately not the techniques Model A's SMOTE case shows
failing. Isotonic calibration is a **separate object**, not baked into the booster, specifically so
`pred_contribs=True` still yields exact TreeSHAP values for the top-2 driver display without adding
a `shap` dependency.

The test split is a **held-out line configuration (config E)**, never a shuffled row split.
Adjacent simulation ticks are near-duplicates of each other; a shuffled split would let the model
memorize a configuration it was also evaluated on, inflating every reported metric. Every metric in
the payload and the phase record carries `"evaluated_on": "config E (UNSEEN)"` for exactly this
reason — not decoration, an assertion checked by `test_no_config_leak.py`'s disjointness check.

## The honest-lift gate

Before committing to reporting Model B's numbers as the headline result, its PR-AUC is compared
against a single-feature `cycle_time_z` logistic baseline. Monotone constraints on synthetic labels
very plausibly collapse into a re-parameterization of that one feature — a real risk, stated in
advance rather than discovered after the fact would look convenient. Measured lift: +44.7%, clearing
the pre-committed ~10% bar. Had it not cleared, the decision, made in advance, was to publish the
comparison anyway and reframe Model B as "a calibrated combiner whose marginal value over the
single-signal baseline is X%" — a small honest lift beats an unmeasured claim.

## Consequences

- Accuracy appears exactly once in the entire repository — the sentence explaining why it is never
  reported (Bosch's ~0.58% base rate makes always-predict-zero score ~99.4% "accuracy").
- Every `RiskDriver` in the wire contract carries `relation: "associative"` as a `Literal`, not a
  free-text label — the type system itself prevents a future change from silently dropping the
  associative-not-causal qualifier.
- `docs/DATA.md` (the label recipe) and `docs/CITATIONS.md` (Bosch's one figure, AI4I's real column
  sums, the sklearn MCC-warning correction) are the two documents that must be updated together if
  this model is ever retrained — the ADR does not repeat their content, it points at them.
