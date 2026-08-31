"""Model B: the live station-risk scorer.

Loads the artifacts `tools/train_station_risk.py` produced (a monotone
[non-negative-constrained] logistic regression stored as plain JSON weights,
a Platt/sigmoid calibrator, a tuned MCC threshold) and scores live feature
vectors from the running twin. No pandas, no ucimlrepo, no `ml.*` import --
only numpy and scikit-learn, both core runtime dependencies already
(tests/test_server_import_hygiene.py enforces the boundary this module stays
on the correct side of).

ARCHITECTURE CHANGE (see tools/train_station_risk.py's module docstring and
docs/DATA.md's addendum for the full audit): this was a monotone XGBoost
booster with TreeSHAP driver contributions. A full audit found a plain
non-negative-constrained logistic regression reaches 100.7% of the
Bayes-optimal PR-AUC ceiling on held-out config E, vs. XGBoost's 89.4% --
and it lets driver contributions be computed EXACTLY (`weight * feature
value`, the literal definition of a linear model's logit decomposition)
rather than via TreeSHAP, at zero runtime dependency either way. Contributions
are still always tagged associative, never causal: the brief itself notes
these causes are hard to isolate from data alone.
"""

from __future__ import annotations

import json
import pickle
from pathlib import Path

import numpy as np
from sklearn.linear_model import LogisticRegression

from twin.contracts import Missingness, RiskDriver, TaggedValue, ValueSource
from twin.risk.features import FEATURE_NAMES

MODELS_DIR = Path(__file__).resolve().parents[3] / "ml" / "models"


class ModelNotTrainedError(RuntimeError):
    """Raised when ml/models/ artifacts are missing -- run
    tools/train_station_risk.py (after tools/generate_training_data.py)
    before starting a server that needs live risk scoring.
    """


class StationRiskScorer:
    def __init__(self, models_dir: Path = MODELS_DIR) -> None:
        model_path = models_dir / "station_risk_model.json"
        calibrator_path = models_dir / "station_risk_calibrator.pkl"
        threshold_path = models_dir / "station_risk_threshold.txt"

        if not (model_path.exists() and calibrator_path.exists() and threshold_path.exists()):
            raise ModelNotTrainedError(
                f"Model B artifacts not found under {models_dir}. Run "
                "tools/generate_training_data.py then tools/train_station_risk.py first."
            )

        model = json.loads(model_path.read_text())
        assert model["feature_names"] == FEATURE_NAMES, (
            "station_risk_model.json's feature order does not match the live "
            "feature extractor -- retrain with tools/train_station_risk.py"
        )
        self._weights = np.array(model["weights"], dtype=float)
        self._bias = float(model["bias"])
        # Hard guarantee, not an assumption: the training-time fit is
        # constrained to non-negative weights (see tools/train_station_
        # risk.py), but re-check on load so a hand-edited or stale artifact
        # can never silently violate the monotonicity this scorer promises.
        assert np.all(self._weights >= 0), (
            "loaded model has a negative weight -- monotonicity violated"
        )

        with calibrator_path.open("rb") as fh:
            self._calibrator: LogisticRegression = pickle.load(fh)

        self.threshold = float(threshold_path.read_text().strip())

    def score(self, features: dict[str, float]) -> tuple[TaggedValue, list[RiskDriver]]:
        row = np.array([features[name] for name in FEATURE_NAMES])

        # Platt calibration operates on the raw LOGIT (pre-sigmoid linear
        # score), not an already-squashed probability -- see
        # tools/train_station_risk.py's module docstring for the real bug
        # this exact convention mismatch caused when it was reversed.
        raw_logit = float(row @ self._weights + self._bias)
        calibrated = float(self._calibrator.predict_proba([[raw_logit]])[0, 1])

        # Exact linear-model contribution: weight * feature value, the
        # literal decomposition of this model's logit -- no approximation
        # (unlike TreeSHAP, which is exact for trees but is an
        # approximation of nothing simpler being computed here).
        feature_contribs = self._weights * row
        top2_idx = np.argsort(np.abs(feature_contribs))[::-1][:2]
        drivers = [
            RiskDriver(feature=FEATURE_NAMES[i], contribution=float(feature_contribs[i]))
            for i in top2_idx
        ]

        # Interim state, not an oversight: features are currently computed
        # from full internal simulation state for every station, including
        # the 8 uninstrumented ones -- Phase 9's sensor-gap layer has not
        # been wired into this feature pipeline yet. A real deployment's
        # dark-station risk would need INFERRED features, not ground truth.
        # See docs/phases/phase-08-predictive-layer.md.
        tagged = TaggedValue(
            value=calibrated,
            source=ValueSource.OBSERVED,
            missingness=Missingness.PRESENT,
            confidence=1.0,
        )
        return tagged, drivers


__all__ = ["ModelNotTrainedError", "StationRiskScorer"]
