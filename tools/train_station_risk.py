#!/usr/bin/env python3
"""Trains Model B: the live station-risk scorer.

Split discipline (docs/DATA.md): configs A, B, C train the raw model; config
D calibrates it AND tunes the MCC threshold; config E is touched by nothing
until the very last evaluation step. Never a shuffled train_test_split --
adjacent ticks are near-duplicates of each other.

Monotone by construction (`monotone_constraints`), not learned: the five
weights in docs/DATA.md's label function were hand-chosen so risk increases
in every feature, and this model is required to respect that same shape,
not merely happen to learn something similar to it.

Saves the trained booster + calibrator under ml/models/ for
src/twin/risk/scorer.py to load at inference time. Uses pandas (this script
lives in ml/, never imported by src/twin/ -- tests/test_server_import_
hygiene.py enforces that boundary); the saved artifacts themselves need only
xgboost + scikit-learn to load, both already core runtime dependencies.
"""

from __future__ import annotations

import pickle
from pathlib import Path

import numpy as np
import pandas as pd
import xgboost as xgb
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, matthews_corrcoef, roc_auc_score

from twin.risk.features import FEATURE_NAMES

DATA_PATH = Path(__file__).resolve().parents[1] / "ml" / "data" / "training_data.csv"
MODELS_DIR = Path(__file__).resolve().parents[1] / "ml" / "models"
METRICS_PATH = MODELS_DIR / "station_risk_metrics.json"

TRAIN_CONFIGS = {"A", "B", "C"}
CALIBRATION_CONFIG = "D"
TEST_CONFIG = "E"  # unseen until the final evaluation step below


def best_mcc_threshold(y_true: np.ndarray, y_prob: np.ndarray) -> float:
    best_t, best_mcc = 0.5, -1.0
    for t in np.linspace(0.01, 0.99, 99):
        mcc = matthews_corrcoef(y_true, (y_prob >= t).astype(int))
        if mcc > best_mcc:
            best_t, best_mcc = float(t), float(mcc)
    return best_t


def main() -> None:
    df = pd.read_csv(DATA_PATH)
    train_df = df[df["config_id"].isin(TRAIN_CONFIGS)]
    calib_df = df[df["config_id"] == CALIBRATION_CONFIG]
    test_df = df[df["config_id"] == TEST_CONFIG]

    assert set(train_df["config_id"]).isdisjoint({CALIBRATION_CONFIG, TEST_CONFIG})
    assert set(calib_df["config_id"]) == {CALIBRATION_CONFIG}
    assert set(test_df["config_id"]) == {TEST_CONFIG}

    X_train, y_train = train_df[FEATURE_NAMES], train_df["defect"]
    X_calib, y_calib = calib_df[FEATURE_NAMES], calib_df["defect"]
    X_test, y_test = test_df[FEATURE_NAMES], test_df["defect"]

    print(f"train (A,B,C): {len(X_train)} rows, {y_train.mean() * 100:.3f}% positive")
    print(f"calib (D):     {len(X_calib)} rows, {y_calib.mean() * 100:.3f}% positive")
    print(f"test (E, UNSEEN): {len(X_test)} rows, {y_test.mean() * 100:.3f}% positive")

    # --- Model B: monotone XGBoost, no resampling, no scale_pos_weight ---
    # (docs/DATA.md; imbalance handled via max_delta_step, not resampling)
    booster_params = {
        "max_depth": 4,
        "max_bin": 512,
        "max_delta_step": 1,
        "monotone_constraints": "(1,1,1,1,1)",
        "objective": "binary:logistic",
        "eval_metric": "aucpr",
    }
    dtrain = xgb.DMatrix(X_train, label=y_train, feature_names=FEATURE_NAMES)
    booster = xgb.train(booster_params, dtrain, num_boost_round=150)

    dcalib = xgb.DMatrix(X_calib, feature_names=FEATURE_NAMES)
    raw_calib_scores = booster.predict(dcalib)

    # Isotonic calibration as a SEPARATE object (not baked into the booster),
    # so booster.predict(dm, pred_contribs=True) still returns exact TreeSHAP
    # against the raw, uncalibrated model -- calibration and explanation stay
    # decoupled.
    calibrator = IsotonicRegression(out_of_bounds="clip")
    calibrator.fit(raw_calib_scores, y_calib)

    threshold = best_mcc_threshold(y_calib.to_numpy(), calibrator.predict(raw_calib_scores))
    print(f"MCC threshold tuned on config D (calibration set): {threshold:.2f}")

    # --- Mandatory honest-lift gate: single-feature cycle_time_z baseline ---
    baseline = LogisticRegression()
    baseline.fit(X_train[["cycle_time_z"]], y_train)

    # --- Final evaluation: config E, touched by nothing until now ---
    dtest = xgb.DMatrix(X_test, feature_names=FEATURE_NAMES)
    raw_test_scores = booster.predict(dtest)
    calibrated_test_scores = calibrator.predict(raw_test_scores)
    baseline_test_scores = baseline.predict_proba(X_test[["cycle_time_z"]])[:, 1]

    model_pr_auc = average_precision_score(y_test, calibrated_test_scores)
    baseline_pr_auc = average_precision_score(y_test, baseline_test_scores)
    if baseline_pr_auc > 0:
        lift_pct = (model_pr_auc - baseline_pr_auc) / baseline_pr_auc * 100
    else:
        lift_pct = float("inf")

    model_pred_at_threshold = (calibrated_test_scores >= threshold).astype(int)
    metrics = {
        "evaluated_on": f"config {TEST_CONFIG} (UNSEEN)",
        "model": {
            "pr_auc": model_pr_auc,
            "roc_auc": roc_auc_score(y_test, calibrated_test_scores),
            "mcc_at_threshold": matthews_corrcoef(y_test, model_pred_at_threshold),
            "threshold": threshold,
        },
        "single_feature_baseline_cycle_time_z": {
            "pr_auc": baseline_pr_auc,
        },
        "lift_over_baseline_pct": lift_pct,
    }

    print()
    print(f"Model B PR-AUC (evaluated on {TEST_CONFIG}, UNSEEN): {model_pr_auc:.4f}")
    print(f"Single-feature cycle_time_z baseline PR-AUC:         {baseline_pr_auc:.4f}")
    print(f"Lift over baseline: {lift_pct:+.1f}%")
    if lift_pct < 10:
        print(
            "HONEST-LIFT GATE: lift is under 10%. Per docs/DATA.md and the project's own "
            "discipline, this must be published as a calibrated combiner whose marginal "
            "value over the single-signal baseline is small -- not oversold."
        )

    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    booster.save_model(str(MODELS_DIR / "station_risk_booster.json"))
    with (MODELS_DIR / "station_risk_calibrator.pkl").open("wb") as fh:
        pickle.dump(calibrator, fh)
    with (MODELS_DIR / "station_risk_threshold.txt").open("w") as fh:
        fh.write(str(threshold))

    import json

    with METRICS_PATH.open("w") as fh:
        json.dump(metrics, fh, indent=2)
    print(f"\nSaved model, calibrator, threshold, and metrics under {MODELS_DIR}")


if __name__ == "__main__":
    main()
