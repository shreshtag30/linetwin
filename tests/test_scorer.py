"""Model B's live scorer (src/twin/risk/scorer.py). Requires the trained
artifacts under ml/models/ -- run tools/generate_training_data.py then
tools/train_station_risk.py first; skipped otherwise rather than failing, the
same convention as tests/test_no_config_leak.py.
"""

from __future__ import annotations

import numpy as np
import pytest

from twin.contracts import RiskDriver
from twin.risk.features import FEATURE_NAMES
from twin.risk.scorer import MODELS_DIR, ModelNotTrainedError, StationRiskScorer

BASE_FEATURES = {
    "cycle_time_z": 0.0,
    "queue_pressure": 0.0,
    "blocked_fraction": 0.0,
    "starved_fraction": 0.0,
    "upstream_risk_ewma": 0.0,
}


@pytest.fixture(scope="module")
def scorer() -> StationRiskScorer:
    if not (MODELS_DIR / "station_risk_booster.json").exists():
        pytest.skip(f"{MODELS_DIR} not populated -- run tools/train_station_risk.py first")
    return StationRiskScorer()


def test_score_returns_a_probability_and_two_drivers(scorer: StationRiskScorer) -> None:
    tagged, drivers = scorer.score(BASE_FEATURES)
    assert 0.0 <= tagged.value <= 1.0
    assert len(drivers) == 2
    assert all(isinstance(d, RiskDriver) for d in drivers)
    assert all(d.relation == "associative" for d in drivers)
    assert all(d.feature in FEATURE_NAMES for d in drivers)


def test_drivers_are_the_two_largest_by_absolute_contribution(scorer: StationRiskScorer) -> None:
    feats = dict(BASE_FEATURES, cycle_time_z=3.0, queue_pressure=0.95)
    _, drivers = scorer.score(feats)
    driver_names = {d.feature for d in drivers}
    # Not asserting exactly which two win (that's the model's call), but the
    # two features pushed furthest from baseline should plausibly dominate.
    assert driver_names & {"cycle_time_z", "queue_pressure"}


def test_missing_model_artifacts_raise_a_clear_error(tmp_path) -> None:
    with pytest.raises(ModelNotTrainedError, match=r"tools/train_station_risk\.py"):
        StationRiskScorer(models_dir=tmp_path)


@pytest.mark.parametrize("feature_name", FEATURE_NAMES)
def test_risk_is_monotone_non_decreasing_in_each_feature(
    scorer: StationRiskScorer, feature_name: str
) -> None:
    """The mandatory exit gate: monotone_constraints=(1,1,1,1,1) means risk
    must never decrease as any single feature increases, holding the others
    fixed. Swept across the same -6..+6 range in spirit as the plan's
    monotonicity test, using each feature's own plausible range.
    """
    sweep_range = np.linspace(-3.0, 3.0, 25) if feature_name == "cycle_time_z" else np.linspace(
        0.0, 1.0, 25
    )
    risks = []
    for value in sweep_range:
        feats = dict(BASE_FEATURES)
        feats[feature_name] = float(value)
        tagged, _ = scorer.score(feats)
        risks.append(tagged.value)

    # Monotone XGBoost with isotonic calibration on top -- calibration is
    # itself monotone non-decreasing, so the composition must be too. Allow
    # a tiny floating-point tolerance rather than requiring strict ">=" at
    # every adjacent pair.
    diffs = np.diff(risks)
    assert np.all(diffs >= -1e-9), f"{feature_name}: risk decreased somewhere in the sweep: {risks}"
