"""Laplacian harmonic extension over uninstrumented ("dark") stations.

`(D_UU - W_UU + lambda*I) f_U = W_UL f_L + lambda*p_U` -- lambda=0 recovers
Zhu, Ghahramani & Lafferty (ICML 2003) exactly; lambda>0 adds a pull toward
each dark station's own prior (its zone's baseline cycle time), so an
isolated cluster of dark stations with no labeled neighbor at all still gets
a sane answer instead of an unsolvable system. Strictly diagonally dominant
whenever lambda>0 or a labeled neighbor exists, since A_ii = (full node
degree) + lambda while the off-diagonal magnitudes in A_UU sum to at most
the degree restricted to unlabeled neighbors only -- provably nonsingular
by construction, not merely assumed.

W is asymmetric (`w_down=1.0`, `w_up=0.35`, scenarios/line30.yaml): an
upstream station's value influences its downstream neighbor strongly
("defects ride the part forward"), the reverse influence is weaker.

`sensor_share` is an EXACT partition of unity with zero tuning parameters --
not a heuristic decay. Proof sketch (verified numerically in this module's
tests, not just asserted): solving the same system with every observed value
and every prior replaced by 1 must return 1 for every dark station (the
system is linear and the coefficients on each dark station's row already sum
to exactly `A_ii`), so the two pieces of that same solve -- the labeled-
neighbor contribution and the prior's contribution -- necessarily sum to 1.
`sensor_share` is exactly the first piece.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class InferenceResult:
    station_id: str
    value: float
    sensor_share: float  # in [0, 1]; 1.0 - sensor_share is the prior's contribution


def _build_path_weights(station_ids: list[str], w_down: float, w_up: float) -> np.ndarray:
    n = len(station_ids)
    W = np.zeros((n, n))
    for i in range(n - 1):
        W[i + 1, i] = w_down  # upstream node i influences downstream node i+1 strongly
        W[i, i + 1] = w_up  # downstream node i+1 weakly influences upstream node i
    return W


def harmonic_extension(
    station_ids: list[str],
    dark_stations: set[str],
    observed_values: dict[str, float],
    prior_values: dict[str, float],
    *,
    w_down: float = 1.0,
    w_up: float = 0.35,
    lam: float = 0.15,
) -> list[InferenceResult]:
    """`observed_values` must cover every station NOT in `dark_stations`.
    `prior_values` must cover every station IN `dark_stations`.
    """
    n = len(station_ids)
    idx = {sid: i for i, sid in enumerate(station_ids)}
    W = _build_path_weights(station_ids, w_down, w_up)
    D = np.diag(W.sum(axis=1))
    A = D - W + lam * np.eye(n)

    dark_idx = [idx[s] for s in station_ids if s in dark_stations]
    light_idx = [idx[s] for s in station_ids if s not in dark_stations]
    dark_ids_ordered = [station_ids[i] for i in dark_idx]

    A_UU = A[np.ix_(dark_idx, dark_idx)]
    W_UL = W[np.ix_(dark_idx, light_idx)]

    f_L = np.array([observed_values[station_ids[i]] for i in light_idx])
    p_U = np.array([prior_values[station_ids[i]] for i in dark_idx])

    rhs = W_UL @ f_L + lam * p_U
    f_U = np.linalg.solve(A_UU, rhs)

    # Exact partition of unity: same system, f_L -> all ones, prior term
    # dropped. See module docstring for why this equals the labeled-neighbor
    # share of the solution exactly, with zero tuning parameters.
    sensor_share = np.linalg.solve(A_UU, W_UL @ np.ones(len(light_idx)))

    return [
        InferenceResult(station_id=sid, value=float(f_U[k]), sensor_share=float(sensor_share[k]))
        for k, sid in enumerate(dark_ids_ordered)
    ]


__all__ = ["InferenceResult", "harmonic_extension"]
