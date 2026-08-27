"""Score each detector against Phase 4's sensitivity-based ground truth.

Methodology, stated plainly because it is an operationalization, not a
verbatim reproduction of Roser & Nakano's original MSE table (docs/CITATIONS.md
already flags the risk of over-claiming fidelity to a method described only in
a secondary summary): for the four continuous-score detectors, both the
detector's per-station scores and the ground truth's |mean_sensitivity| values
are normalized to sum to 1 across all candidate stations, then compared by
mean squared error. This makes MSE comparable across detectors regardless of
each one's native units (a fraction of time, a queue depth, a ratio). Arrow and
Turning Point produce a single named station, not a per-station score in their
original form, so they are evaluated on top-1 accuracy only -- MSE and top-3
are reported as `None` for them, not invented.
"""

from __future__ import annotations

from dataclasses import dataclass

from twin.diagnostic.detectors import DetectorResult
from twin.diagnostic.ground_truth import SensitivityResult


@dataclass(frozen=True)
class DetectorScore:
    name: str
    top_pick: str
    ground_truth_station: str
    top1_correct: bool
    top3_hit: bool | None
    mse: float | None


def _normalize(values: dict[str, float]) -> dict[str, float]:
    total = sum(values.values())
    if total <= 0:
        # Every candidate scored zero (e.g. a station never blocked or
        # starved in this run) -- fall back to a uniform distribution rather
        # than dividing by zero.
        n = len(values)
        return dict.fromkeys(values, 1.0 / n if n else 0.0)
    return {k: v / total for k, v in values.items()}


def evaluate(
    result: DetectorResult, ground_truth: list[SensitivityResult]
) -> DetectorScore:
    gt_by_station = {r.station_id: abs(r.mean_sensitivity) for r in ground_truth}
    gt_ranked = sorted(gt_by_station, key=lambda s: -gt_by_station[s])
    gt_top1 = gt_ranked[0]

    top1_correct = result.top_pick == gt_top1

    if result.scores is None:
        # Arrow / Turning Point: no native per-station score to compare.
        return DetectorScore(
            name=result.name,
            top_pick=result.top_pick,
            ground_truth_station=gt_top1,
            top1_correct=top1_correct,
            top3_hit=None,
            mse=None,
        )

    detector_ranked = sorted(result.scores, key=lambda s: -result.scores[s])
    top3_hit = gt_top1 in set(detector_ranked[:3])

    candidates = set(result.scores) & set(gt_by_station)
    norm_scores = _normalize({s: result.scores[s] for s in candidates})
    norm_gt = _normalize({s: gt_by_station[s] for s in candidates})
    mse = sum((norm_scores[s] - norm_gt[s]) ** 2 for s in candidates) / len(candidates)

    return DetectorScore(
        name=result.name,
        top_pick=result.top_pick,
        ground_truth_station=gt_top1,
        top1_correct=top1_correct,
        top3_hit=top3_hit,
        mse=mse,
    )


def evaluate_all(
    results: list[DetectorResult], ground_truth: list[SensitivityResult]
) -> list[DetectorScore]:
    return [evaluate(r, ground_truth) for r in results]


__all__ = ["DetectorScore", "evaluate", "evaluate_all"]
