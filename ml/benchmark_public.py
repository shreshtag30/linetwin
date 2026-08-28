#!/usr/bin/env python3
"""Model A: the offline benchmark, on real public data.

Proves the modelling capability is real -- real data, real metrics, and one
DELIBERATE failure case (resample-then-cross-validate) reproduced on purpose,
because publishing a failure run is cheaper credibility than an unmeasured
claim. NEVER imported by src/twin/ (tests/test_server_import_hygiene.py
enforces this) -- Model B (the live scorer) uses none of pandas/ucimlrepo/
imbalanced-learn and trains on entirely separate, synthetic data
(docs/DATA.md), not this dataset.

Dataset: AI4I 2020 Predictive Maintenance (UCI id 601), CC BY 4.0, 10,000
rows. Label column `Machine failure`: 339 positives (3.39%). The five
individual failure-mode columns (TWF+HDF+PWF+OSF+RNF) sum to 373, not 339 --
a known inconsistency in the source data (some rows have more than one
failure mode flagged, or `Machine failure` is not a strict OR of the five),
stated here rather than silently reconciled. See docs/CITATIONS.md.
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass

import numpy as np
import pandas as pd
import xgboost as xgb
from imblearn.over_sampling import SMOTE
from sklearn.calibration import CalibratedClassifierCV
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    average_precision_score,
    brier_score_loss,
    matthews_corrcoef,
    roc_auc_score,
)
from sklearn.model_selection import StratifiedKFold, train_test_split
from sklearn.preprocessing import StandardScaler
from ucimlrepo import fetch_ucirepo

RANDOM_STATE = 42
LABEL_COLUMN = "Machine failure"


@dataclass(frozen=True)
class Metrics:
    name: str
    pr_auc: float
    roc_auc: float
    brier: float
    mcc_at_threshold: float
    threshold: float
    note: str = ""


def load_data() -> tuple[pd.DataFrame, pd.Series]:
    dataset = fetch_ucirepo(id=601)
    X = dataset.data.features.copy()
    y = dataset.data.targets[LABEL_COLUMN].copy()

    failure_mode_sum = dataset.data.targets[["TWF", "HDF", "PWF", "OSF", "RNF"]].sum().sum()
    n_positive = int(y.sum())
    print(
        f"Loaded {len(X)} rows. {LABEL_COLUMN}: {n_positive} positives "
        f"({n_positive / len(y) * 100:.2f}%). Failure-mode columns sum to "
        f"{failure_mode_sum} (see module docstring for why this isn't {n_positive})."
    )

    X = pd.get_dummies(X, columns=["Type"], drop_first=True)
    return X, y


def best_threshold_for_mcc(y_true: np.ndarray, y_prob: np.ndarray) -> tuple[float, float]:
    """Sweeps thresholds and returns (threshold, mcc) at the best one. MCC is
    never reported without the threshold it was computed at (docs/DATA.md's
    sibling discipline, carried into this module too).
    """
    best_t, best_mcc = 0.5, -1.0
    for t in np.linspace(0.01, 0.99, 99):
        mcc = matthews_corrcoef(y_true, (y_prob >= t).astype(int))
        if mcc > best_mcc:
            best_t, best_mcc = float(t), float(mcc)
    return best_t, best_mcc


def evaluate(name: str, y_true: np.ndarray, y_prob: np.ndarray, note: str = "") -> Metrics:
    pr_auc = average_precision_score(y_true, y_prob)
    roc_auc = roc_auc_score(y_true, y_prob)
    brier = brier_score_loss(y_true, y_prob)
    threshold, mcc = best_threshold_for_mcc(y_true, y_prob)
    return Metrics(name, pr_auc, roc_auc, brier, mcc, threshold, note)


def run_always_zero(y_test: np.ndarray) -> Metrics:
    y_prob = np.zeros(len(y_test), dtype=float)
    # MCC for a constant classifier is undefined (0/0: both marginal
    # variances are zero). scikit-learn returns 0.0 and raises a real
    # warning for it -- caught here and asserted on, not assumed, so this
    # module's own claim about sklearn's behavior is verified at run time.
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        mcc = matthews_corrcoef(y_test, y_prob.astype(int))
    warned = any(issubclass(w.category, UserWarning) for w in caught)
    if warned:
        suffix = " with a warning (verified)"
    else:
        suffix = " -- no warning raised, contrary to expectation"
    prefix = "MCC is undefined (0/0) for a constant classifier; sklearn returns 0.0"
    note = prefix + suffix
    return Metrics(
        "always_zero",
        pr_auc=average_precision_score(y_test, y_prob),
        roc_auc=0.5,  # undefined for a constant predictor; 0.5 stated as the conventional filler
        brier=brier_score_loss(y_test, y_prob),
        mcc_at_threshold=mcc,
        threshold=float("nan"),
        note=note,
    )


def run_logistic_floor(
    X_train: pd.DataFrame, y_train: pd.Series, X_test: pd.DataFrame, y_test: pd.Series
) -> Metrics:
    scaler = StandardScaler()
    X_train_s = scaler.fit_transform(X_train)
    X_test_s = scaler.transform(X_test)
    clf = LogisticRegression(max_iter=1000, class_weight="balanced", random_state=RANDOM_STATE)
    clf.fit(X_train_s, y_train)
    y_prob = clf.predict_proba(X_test_s)[:, 1]
    return evaluate("logistic_floor", y_test.to_numpy(), y_prob)


def run_xgboost_uncalibrated(
    X_train: pd.DataFrame, y_train: pd.Series, X_test: pd.DataFrame, y_test: pd.Series
) -> Metrics:
    scale_pos_weight = (y_train == 0).sum() / max((y_train == 1).sum(), 1)
    clf = xgb.XGBClassifier(
        n_estimators=200,
        max_depth=4,
        scale_pos_weight=scale_pos_weight,
        random_state=RANDOM_STATE,
        eval_metric="logloss",
    )
    clf.fit(X_train, y_train)
    y_prob = clf.predict_proba(X_test)[:, 1]
    return evaluate(
        "xgboost_uncalibrated_scale_pos_weight",
        y_test.to_numpy(),
        y_prob,
        note=f"scale_pos_weight={scale_pos_weight:.1f}",
    )


def run_xgboost_calibrated(
    X_train: pd.DataFrame, y_train: pd.Series, X_test: pd.DataFrame, y_test: pd.Series
) -> Metrics:
    base = xgb.XGBClassifier(
        n_estimators=200, max_depth=4, random_state=RANDOM_STATE, eval_metric="logloss"
    )
    clf = CalibratedClassifierCV(base, method="isotonic", cv=3)
    clf.fit(X_train, y_train)
    y_prob = clf.predict_proba(X_test)[:, 1]
    return evaluate("xgboost_calibrated_isotonic", y_test.to_numpy(), y_prob)


def run_smote_failure_case(
    X_train: pd.DataFrame, y_train: pd.Series, X_test: pd.DataFrame, y_test: pd.Series
) -> tuple[Metrics, Metrics]:
    """The deliberate failure: apply SMOTE to the FULL training set BEFORE
    cross-validating, so synthetic minority-class neighbors leak between CV
    folds. Reports the (inflated) CV score AND the true held-out score, so
    the collapse between them is the published result -- not a single
    misleading number.
    """
    smote = SMOTE(random_state=RANDOM_STATE)
    X_resampled, y_resampled = smote.fit_resample(X_train, y_train)

    # WRONG (the deliberate mistake): cross-validate AFTER resampling.
    cv_scores = []
    skf = StratifiedKFold(n_splits=5, shuffle=True, random_state=RANDOM_STATE)
    X_resampled_arr = np.asarray(X_resampled)
    y_resampled_arr = np.asarray(y_resampled)
    for train_idx, val_idx in skf.split(X_resampled_arr, y_resampled_arr):
        clf = xgb.XGBClassifier(
            n_estimators=200, max_depth=4, random_state=RANDOM_STATE, eval_metric="logloss"
        )
        clf.fit(X_resampled_arr[train_idx], y_resampled_arr[train_idx])
        y_prob = clf.predict_proba(X_resampled_arr[val_idx])[:, 1]
        cv_scores.append(average_precision_score(y_resampled_arr[val_idx], y_prob))

    inflated = Metrics(
        "smote_then_cv_INFLATED",
        pr_auc=float(np.mean(cv_scores)),
        roc_auc=float("nan"),
        brier=float("nan"),
        mcc_at_threshold=float("nan"),
        threshold=float("nan"),
        note="WRONG METHODOLOGY, published on purpose: SMOTE applied before CV, "
        "synthetic neighbors leak across folds",
    )

    # The true score: train on the resampled data, evaluate on the REAL,
    # untouched, never-resampled held-out test set.
    clf = xgb.XGBClassifier(
        n_estimators=200, max_depth=4, random_state=RANDOM_STATE, eval_metric="logloss"
    )
    clf.fit(X_resampled, y_resampled)
    y_prob = clf.predict_proba(X_test)[:, 1]
    true_held_out = evaluate(
        "smote_then_real_holdout",
        y_test.to_numpy(),
        y_prob,
        note="same model, scored against the real untouched test set -- the collapse is the point",
    )

    return inflated, true_held_out


def print_table(metrics: list[Metrics]) -> None:
    print()
    print(f"{'model':38} {'PR-AUC':>8} {'ROC-AUC':>8} {'Brier':>8} {'MCC@t':>8} {'t':>6}  note")
    print("-" * 120)
    for m in metrics:
        pr = f"{m.pr_auc:.4f}" if not np.isnan(m.pr_auc) else "n/a"
        roc = f"{m.roc_auc:.4f}" if not np.isnan(m.roc_auc) else "n/a"
        brier = f"{m.brier:.4f}" if not np.isnan(m.brier) else "n/a"
        mcc = f"{m.mcc_at_threshold:.4f}" if not np.isnan(m.mcc_at_threshold) else "n/a"
        t = f"{m.threshold:.2f}" if not np.isnan(m.threshold) else "n/a"
        print(f"{m.name:38} {pr:>8} {roc:>8} {brier:>8} {mcc:>8} {t:>6}  {m.note}")
    print()
    print(
        "Accuracy appears nowhere above, and appears exactly once in this entire "
        "codebase: in this sentence, explaining why it is never reported. At "
        "3.39% prevalence, predicting all-zero scores >96% accuracy while "
        "catching zero failures -- accuracy is the wrong metric for this problem."
    )


def main() -> None:
    X, y = load_data()
    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.3, stratify=y, random_state=RANDOM_STATE
    )

    results = [
        run_always_zero(y_test.to_numpy()),
        run_logistic_floor(X_train, y_train, X_test, y_test),
        run_xgboost_uncalibrated(X_train, y_train, X_test, y_test),
        run_xgboost_calibrated(X_train, y_train, X_test, y_test),
    ]
    inflated, true_held_out = run_smote_failure_case(X_train, y_train, X_test, y_test)
    results += [inflated, true_held_out]

    print_table(results)

    print()
    print(
        f"SMOTE-then-CV collapse: inflated CV PR-AUC={inflated.pr_auc:.4f} vs. "
        f"true held-out PR-AUC={true_held_out.pr_auc:.4f} "
        f"({(inflated.pr_auc - true_held_out.pr_auc) / inflated.pr_auc * 100:.1f}% overstatement)"
    )


if __name__ == "__main__":
    main()
