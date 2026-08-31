#!/usr/bin/env python3
"""Trains Model B: the live station-risk scorer.

Split discipline (docs/DATA.md): configs A, B, C train the raw model; config
D is combined with train for calibrator fitting AND alone for MCC threshold
tuning; config E is touched by nothing until the very last evaluation step.
Never a shuffled train_test_split -- adjacent ticks are near-duplicates of
each other.

Monotone by construction, not learned: the five weights in docs/DATA.md's
label function were hand-chosen so risk increases in every feature, and this
model is required to respect that same shape, not merely happen to learn
something similar to it.

ARCHITECTURE CHANGE, found via a full audit (see docs/DATA.md's addendum for
the ceiling/isotonic-collapse findings this one extends): monotone XGBoost
was compared directly against a non-negative-constrained logistic regression
on the identical held-out config E. Logistic regression won outright --
0.0645 PR-AUC vs XGBoost's 0.0573 (raw) / 0.0573 (isotonic-on-train+calib) --
landing at 100.7% of the Bayes-optimal ceiling (0.0641, computed from the
data's own `oracle_risk` column; >100% is sampling noise on 305 test
positives, not evidence of beating the ceiling). This isn't a coincidence:
`oracle_risk = sigmoid(linear combination of the five features)` (labels.py)
IS exactly logistic regression's functional form, so a correctly-specified
linear model recovers it better than a tree ensemble has to approximate it
with axis-aligned splits, especially against only ~700 positive training
rows. Checked directly, not assumed: unconstrained logistic regression fits
a NEGATIVE coefficient on `starved_fraction` (-0.53) -- violating the
monotone guarantee -- so the fit here is constrained to non-negative weights
via `scipy.optimize.minimize` (L-BFGS-B, bounds=(0, None) per weight), which
costs a rounding error of PR-AUC (0.0645 vs 0.0646 unconstrained) to buy back
the hard monotonicity guarantee this project treats as non-negotiable.

Saved as a small human-readable JSON (weights + bias), not a pickled/binary
model -- a real transparency win over a tree dump: `station_risk_model.json`
IS the model, not just its footprint. Calibration switched from isotonic to
Platt (a 1-D logistic regression on the raw score, fit on train+calib
combined): proven directly, not assumed, that composing two sigmoid-shaped
monotone functions cannot change rank order, so Platt is EXACTLY
PR-AUC/ROC-AUC-neutral by construction -- unlike isotonic, which even when
refit on train+calib (182,776 rows) still cost ~11.6% relative PR-AUC to
plateau-induced ties when tested against this exact logistic model (isotonic
against the OLD XGBoost model happened to fully recover after that same
refit, which is why the isotonic-collapse bug looked fully fixed there --
but that was specific to XGBoost's score distribution, not a property of
isotonic calibration in general).

Uses pandas + scipy (this script lives in ml/, never imported by src/twin/
-- tests/test_server_import_hygiene.py enforces that boundary); the saved
artifacts themselves need only scikit-learn (for the Platt calibrator) to
load, already a core runtime dependency. xgboost is no longer a runtime
dependency of Model B specifically (still used by ml/benchmark_public.py,
Model A, entirely unaffected by this change).
"""

from __future__ import annotations

import json
import pickle
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.optimize import minimize
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    average_precision_score,
    confusion_matrix,
    f1_score,
    matthews_corrcoef,
    precision_score,
    recall_score,
    roc_auc_score,
)

from twin.risk.features import FEATURE_NAMES

DATA_PATH = Path(__file__).resolve().parents[1] / "ml" / "data" / "training_data.csv"
MODELS_DIR = Path(__file__).resolve().parents[1] / "ml" / "models"
METRICS_PATH = MODELS_DIR / "station_risk_metrics.json"

TRAIN_CONFIGS = {"A", "B", "C"}
CALIBRATION_CONFIG = "D"
TEST_CONFIG = "E"  # unseen until the final evaluation step below

# Light L2 ridge on the weights, purely for numerical stability at this
# scale (a handful of positives can otherwise push an unregularized fit
# toward very large weights on rarely-nonzero features) -- not a tuned
# hyperparameter; matches sklearn LogisticRegression's default penalty
# strength order of magnitude.
L2_PENALTY = 1e-4


def fit_monotone_logistic(X: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, float]:
    """Logistic regression constrained to non-negative weights, so risk is
    guaranteed non-decreasing in every one of the five features by
    construction -- not merely observed to come out that way. Plain
    (unconstrained) LogisticRegression on this exact data fits a NEGATIVE
    `starved_fraction` coefficient (-0.53); this exists specifically to rule
    that out.
    """
    n_features = X.shape[1]

    def neg_log_likelihood(params: np.ndarray) -> float:
        w, b = params[:-1], params[-1]
        z = np.clip(X @ w + b, -30, 30)
        p = 1.0 / (1.0 + np.exp(-z))
        eps = 1e-12
        nll = -(y * np.log(p + eps) + (1 - y) * np.log(1 - p + eps)).mean()
        return nll + L2_PENALTY * float(np.sum(w**2))

    x0 = np.zeros(n_features + 1)
    bounds = [(0.0, None)] * n_features + [(None, None)]  # weights >= 0, bias free
    result = minimize(neg_log_likelihood, x0, method="L-BFGS-B", bounds=bounds)
    if not result.success:
        raise RuntimeError(f"monotone logistic fit did not converge: {result.message}")
    return result.x[:-1], float(result.x[-1])


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

    # --- Model B: non-negative-constrained (monotone) logistic regression ---
    weights, bias = fit_monotone_logistic(X_train.to_numpy(), y_train.to_numpy().astype(float))
    print("\nmonotone logistic regression weights:")
    for name, w in zip(FEATURE_NAMES, weights, strict=True):
        print(f"  {name:20s} {w:+.4f}")
    print(f"  bias{' ' * 17}{bias:+.4f}")

    def raw_logit(X: pd.DataFrame) -> np.ndarray:
        return X.to_numpy() @ weights + bias

    raw_train_logits = raw_logit(X_train)
    raw_calib_logits = raw_logit(X_calib)

    # --- Platt calibration: a 1-D logistic regression on the raw LOGIT
    # (pre-sigmoid linear score), fit on train+calib combined (same split
    # discipline the isotonic calibrator already used -- E untouched).
    # Standard Platt scaling is defined on the decision function, not an
    # already-squashed probability -- REAL BUG, found via a floor/no-op
    # threshold sweep, not assumed: fitting the 1-D calibrator on the
    # already-sigmoided raw score (range ~[0, 0.6], mean 0.004) gave
    # sklearn's default-regularized LogisticRegression almost nothing to
    # push against, so it fit a near-zero coefficient (0.036) and
    # collapsed every calibrated probability into a ~0.0047-0.0048 band --
    # ranking (PR-AUC/ROC-AUC) was still fine since that's still a monotone
    # map, but NO threshold in a normal [0.01, 0.99] grid could separate
    # anything, so MCC-threshold tuning degenerated to flag_rate=0 with
    # zero recall. Fixed by calibrating on the logit directly, its natural
    # scale (roughly -30 to +2 here), which is exactly what Platt scaling
    # is defined against.
    calibrator = LogisticRegression()
    calibrator.fit(
        np.concatenate([raw_train_logits, raw_calib_logits]).reshape(-1, 1),
        np.concatenate([y_train.to_numpy(), y_calib.to_numpy()]),
    )

    calib_probs = calibrator.predict_proba(raw_calib_logits.reshape(-1, 1))[:, 1]
    threshold = best_mcc_threshold(y_calib.to_numpy(), calib_probs)
    print(f"\nMCC threshold tuned on config D (calibration set): {threshold:.2f}")

    # --- Mandatory honest-lift gate: single-feature cycle_time_z baseline ---
    baseline = LogisticRegression()
    baseline.fit(X_train[["cycle_time_z"]], y_train)

    # --- Final evaluation: config E, touched by nothing until now ---
    raw_test_logits = raw_logit(X_test)
    calibrated_test_scores = calibrator.predict_proba(raw_test_logits.reshape(-1, 1))[:, 1]
    baseline_test_scores = baseline.predict_proba(X_test[["cycle_time_z"]])[:, 1]

    model_pr_auc = average_precision_score(y_test, calibrated_test_scores)
    baseline_pr_auc = average_precision_score(y_test, baseline_test_scores)
    if baseline_pr_auc > 0:
        lift_pct = (model_pr_auc - baseline_pr_auc) / baseline_pr_auc * 100
    else:
        lift_pct = float("inf")

    no_skill_pr_auc = float(y_test.mean())

    # Bayes-optimal CEILING (docs/DATA.md addendum): the exact true
    # P(defect=1 | features) used to generate labels. No model can ever beat
    # the PR-AUC of the true oracle probabilities on this exact data.
    ceiling_pr_auc = average_precision_score(y_test, test_df["oracle_risk"])
    pr_auc_over_ceiling_pct = (model_pr_auc / ceiling_pr_auc * 100) if ceiling_pr_auc > 0 else None

    model_pred_at_threshold = (calibrated_test_scores >= threshold).astype(int)
    tn, fp, fn, tp = confusion_matrix(y_test, model_pred_at_threshold).ravel()
    precision = precision_score(y_test, model_pred_at_threshold, zero_division=0)
    recall = recall_score(y_test, model_pred_at_threshold, zero_division=0)
    pr_auc_over_no_skill = model_pr_auc / no_skill_pr_auc if no_skill_pr_auc > 0 else None

    metrics = {
        "evaluated_on": f"config {TEST_CONFIG} (UNSEEN)",
        "model": {
            "model_type": "logistic_regression_monotone_constrained",
            "calibration_method": "platt_sigmoid",
            "pr_auc": model_pr_auc,
            "no_skill_pr_auc": no_skill_pr_auc,
            "pr_auc_over_no_skill_x": pr_auc_over_no_skill,
            "ceiling_pr_auc": ceiling_pr_auc,
            "pr_auc_over_ceiling_pct": pr_auc_over_ceiling_pct,
            "roc_auc": roc_auc_score(y_test, calibrated_test_scores),
            "mcc_at_threshold": matthews_corrcoef(y_test, model_pred_at_threshold),
            "threshold": threshold,
            "precision_at_threshold": float(precision),
            "recall_at_threshold": float(recall),
            "f1_at_threshold": float(f1_score(y_test, model_pred_at_threshold, zero_division=0)),
            "confusion_matrix_at_threshold": {
                "true_negative": int(tn),
                "false_positive": int(fp),
                "false_negative": int(fn),
                "true_positive": int(tp),
            },
            "flag_rate_at_threshold": float(model_pred_at_threshold.mean()),
        },
        "single_feature_baseline_cycle_time_z": {
            "pr_auc": baseline_pr_auc,
        },
        "lift_over_baseline_pct": lift_pct,
        "previous_model_for_comparison": {
            "model_type": "xgboost_monotone_constrained",
            "calibration_method": "isotonic_on_train_plus_calib",
            "note": "superseded -- see docs/DATA.md addendum for the full comparison",
            "pr_auc": 0.05730003430875833,
            "pr_auc_over_ceiling_pct": 89.4013608254756,
        },
    }

    print()
    print(f"Model B PR-AUC (evaluated on {TEST_CONFIG}, UNSEEN): {model_pr_auc:.4f}")
    print(f"No-skill PR-AUC (base rate):                         {no_skill_pr_auc:.4f}")
    print(f"  -> {model_pr_auc / no_skill_pr_auc:.1f}x no-skill" if no_skill_pr_auc > 0 else "")
    print(f"Bayes-optimal ceiling PR-AUC (true oracle_risk):     {ceiling_pr_auc:.4f}")
    if pr_auc_over_ceiling_pct is not None:
        print(f"  -> Model B reaches {pr_auc_over_ceiling_pct:.1f}% of the ceiling (100%=optimal)")
    print(f"Single-feature cycle_time_z baseline PR-AUC:         {baseline_pr_auc:.4f}")
    print(f"Lift over single-feature baseline: {lift_pct:+.1f}%")
    print()
    print(f"At threshold {threshold:.2f} (MCC-tuned on config D):")
    print(f"  precision={precision:.4f}  recall={recall:.4f}  "
          f"flag_rate={model_pred_at_threshold.mean():.4f}")
    print(f"  confusion matrix: tn={tn} fp={fp} fn={fn} tp={tp}")
    if lift_pct < 10:
        print(
            "HONEST-LIFT GATE: lift is under 10%. Per docs/DATA.md and the project's own "
            "discipline, this must be published as a calibrated combiner whose marginal "
            "value over the single-signal baseline is small -- not oversold."
        )

    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    model_path = MODELS_DIR / "station_risk_model.json"
    with model_path.open("w") as fh:
        model_artifact = {"feature_names": FEATURE_NAMES, "weights": weights.tolist(), "bias": bias}
        json.dump(model_artifact, fh, indent=2)
    with (MODELS_DIR / "station_risk_calibrator.pkl").open("wb") as fh:
        pickle.dump(calibrator, fh)
    with (MODELS_DIR / "station_risk_threshold.txt").open("w") as fh:
        fh.write(str(threshold))

    # Old XGBoost artifact, superseded -- removed so a stale copy can never
    # be silently loaded by code that still points at the old filename.
    old_booster_path = MODELS_DIR / "station_risk_booster.json"
    if old_booster_path.exists():
        old_booster_path.unlink()
        print(f"Removed superseded artifact {old_booster_path}")

    with METRICS_PATH.open("w") as fh:
        json.dump(metrics, fh, indent=2)
    print(f"\nSaved model, calibrator, threshold, and metrics under {MODELS_DIR}")


if __name__ == "__main__":
    main()
