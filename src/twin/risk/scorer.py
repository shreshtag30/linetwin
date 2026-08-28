"""Model B: the live station-risk scorer.

Loads the artifacts `tools/train_station_risk.py` produced (a monotone
XGBoost booster, an isotonic calibrator, a tuned MCC threshold) and scores
live feature vectors from the running twin. No pandas, no ucimlrepo, no
`ml.*` import -- only xgboost and scikit-learn, both core runtime
dependencies already (tests/test_server_import_hygiene.py enforces the
boundary this module stays on the correct side of).

`pred_contribs=True` gives exact TreeSHAP contributions in C++, at no extra
runtime dependency (no `shap` package) -- the original design commitment this
project inherited. The top-2 contributors by absolute magnitude are surfaced
as `RiskDriver`s, always tagged associative, never causal: the brief itself
notes these causes are hard to isolate from data alone.
"""

from __future__ import annotations

import pickle
from pathlib import Path

import numpy as np
import xgboost as xgb
from sklearn.isotonic import IsotonicRegression

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
        booster_path = models_dir / "station_risk_booster.json"
        calibrator_path = models_dir / "station_risk_calibrator.pkl"
        threshold_path = models_dir / "station_risk_threshold.txt"

        if not (booster_path.exists() and calibrator_path.exists() and threshold_path.exists()):
            raise ModelNotTrainedError(
                f"Model B artifacts not found under {models_dir}. Run "
                "tools/generate_training_data.py then tools/train_station_risk.py first."
            )

        self._booster = xgb.Booster()
        self._booster.load_model(str(booster_path))

        with calibrator_path.open("rb") as fh:
            self._calibrator: IsotonicRegression = pickle.load(fh)

        self.threshold = float(threshold_path.read_text().strip())

    def score(self, features: dict[str, float]) -> tuple[TaggedValue, list[RiskDriver]]:
        row = np.array([[features[name] for name in FEATURE_NAMES]])
        dm = xgb.DMatrix(row, feature_names=FEATURE_NAMES)

        raw_score = self._booster.predict(dm)[0]
        calibrated = float(self._calibrator.predict([raw_score])[0])

        contribs = self._booster.predict(dm, pred_contribs=True)[0]
        # Last column is the base/bias term (TreeSHAP convention), not a
        # feature -- excluded from driver ranking.
        feature_contribs = contribs[: len(FEATURE_NAMES)]
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
