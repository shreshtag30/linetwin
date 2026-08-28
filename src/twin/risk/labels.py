"""The synthetic label-generating process. See docs/DATA.md for the full
rationale -- written before this module, not after, and before any metric.

`oracle_risk` and `BIAS` are offline-only: they exist to generate training
labels, never called by the live scorer, which predicts risk FROM the five
features alone, exactly as a real deployment would have to.
"""

from __future__ import annotations

import math

# Synthetic, hand-chosen so every direction is positive (monotone by
# construction) and congestion (queue_pressure, blocked_fraction) dominates,
# since this discrete-event line has no physical wear model to weight
# instead. See docs/DATA.md for why each direction was chosen.
WEIGHTS = {
    "cycle_time_z": 0.9,
    "queue_pressure": 2.6,
    "blocked_fraction": 2.2,
    "starved_fraction": 1.1,
    "upstream_risk_ewma": 1.8,
}

# The one calibrated (not hand-chosen) constant: solved by
# tools/calibrate_label_bias.py as a fixed point (the feature distribution
# depends on bias through the recursive upstream_risk_ewma term, and bias is
# solved from that same distribution) so that mean oracle_risk over a long
# baseline run lands at Bosch's cited ~0.58% prevalence (docs/CITATIONS.md).
# Recorded here as the converged result (3 iterations) of that calibration,
# not re-derived on every import. Re-solved once already, after a rolling-
# window sampling bug in features.py (fixed, see that module's docstring)
# was found via this project's own tests and corrected.
BIAS = -8.3743


def oracle_risk(features: dict[str, float], *, bias: float = BIAS) -> float:
    raw_score = bias + sum(WEIGHTS[name] * features[name] for name in WEIGHTS)
    return 1.0 / (1.0 + math.exp(-raw_score))


__all__ = ["BIAS", "WEIGHTS", "oracle_risk"]
