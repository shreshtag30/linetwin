"""Per-station random number generation.

One `numpy.random.SeedSequence(seed)`, spawned into one independent child per
station, each driving its own `Generator(PCG64(child))`. Never stdlib `random`,
never one Generator shared across stations.

This buys two things: bit-reproducibility for the same seed (needed so a demo
video is exactly repeatable), and -- more importantly for Phase 4 -- a genuine
Common Random Numbers paired comparison. Perturbing station S17's cycle time and
re-running with the SAME per-station seeds means every OTHER station's random
draws are identical between the baseline and perturbed runs, so the measured
throughput difference is attributable to the perturbation alone, not to noise
from an independently-reseeded run.
"""

from __future__ import annotations

import numpy as np


def make_station_generators(
    seed: int, station_ids: list[str]
) -> dict[str, np.random.Generator]:
    """One independent, reproducible Generator per station id, in a stable order."""
    seq = np.random.SeedSequence(seed)
    children = seq.spawn(len(station_ids))
    return {
        sid: np.random.Generator(np.random.PCG64(child))
        for sid, child in zip(station_ids, children, strict=True)
    }


__all__ = ["make_station_generators"]
