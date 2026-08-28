# ADR-004 — Sensor Gap: Harmonic Extension, Not a Learned Imputer

**Status:** Accepted (Phase 9)

---

## Context

8 of 30 stations are uninstrumented (dark), matching the brief's "majority well-instrumented, a
meaningful minority reliant on manual checks." A dark station still needs *some* value shown for
its cycle time, or the dashboard has an honest but useless hole in it. The options were: a learned
imputer (e.g., a small regression trained to predict a dark station's value from its neighbors), or
a closed-form graph method with no training step at all.

## Decision

**Harmonic extension over the line's adjacency graph** (Zhu, Ghahramani & Lafferty 2003):
`(D_UU - W_UU + λI) f_U = W_UL f_L + λp_U`, with asymmetric edge weights (`w_down=1.0`,
`w_up=0.35` — a defect or slowdown rides the part forward more than it echoes backward) and a
regularization term `λp_U` pulling toward each dark station's own zone-average prior.

## Why a closed-form graph method, not a learned imputer

A learned imputer would need labeled training data for exactly the thing it doesn't have —
this line's own dark-station ground truth, which by construction does not exist outside the
simulator (and inside the simulator, "ground truth" for a dark station is only available because
it's synthetic, which would make any reported accuracy circular). The harmonic extension needs no
training data at all: it is a linear system solved from the *live* observed values and the graph
structure, every run. Two properties fall out of the algebra rather than being tuned in, and both
are pinned by tests, not just asserted in prose:

- **λ=0, symmetric weights, recovers the classical result exactly**: an interior dark node between
  two labeled neighbors gets exactly their mean (`test_symmetric_path_lambda_zero_gives_the_interior_node_the_exact_mean`).
- **Rows of the influence operator sum to exactly 1** — an exact partition of unity, so
  `sensor_share` (how much of a dark station's inferred value traces to real sensor evidence versus
  the zone prior) is a *computed* quantity with zero tuning parameters, not a hand-picked decay
  curve (`test_constant_field_is_reproduced_exactly_partition_of_unity`). This is what lets the UI
  honestly render "Inferred — 61% sensor-derived" instead of a vaguer confidence label.

`λ > 0` also makes the system strictly diagonally dominant, hence provably nonsingular — the
regularization is not just a modeling choice, it is what guarantees the linear solve always has a
unique answer.

## The degradation curve was measured, not assumed to be graceful

Before this method was described anywhere as "gracefully degrading," the actual curve was run at
100/90/80/70/60/50/40% coverage. It is **not graceful**: error jumps from 0 to ~30% the instant any
station goes dark, then plateaus roughly flat down to 40% coverage — a robustness story once
something is missing, not a smooth decay curve. Reported exactly as measured in the Phase 9 record,
with the caveat that a meaningful share of that ~30% floor is likely inherent single-sample
cycle-time noise (CV up to 0.28) rather than an inference limitation, since the method estimates a
smoothed value, not the exact noisy instantaneous sample it is scored against.

## Consequences

- Zero new dependencies, zero training pipeline, zero risk of the imputer itself needing the same
  honest-lift-gate scrutiny Model B's predictions get (ADR-003) — it is pure linear algebra over a
  known graph.
- `sensor_share` doubles as the confidence value shown in the UI (`engine.py` sets
  `confidence=inferred.sensor_share` directly) — an earlier draft applied an arbitrary
  `0.5 + 0.5 * sensor_share` rescaling that added an unexplained transform on top of an already-exact
  quantity; removed once caught.
- Phase 9's greedy sensor-placement ranking (`graph/placement.py`, cites Krause/Singh/Guestrin's
  (1-1/e) guarantee) is a direct consumer of this same linear system, re-solved after each
  simulated pick — a second real use of the method beyond just filling in a dashboard blank.
