"""Ground truth by sensitivity analysis.

Possible only because this project owns the simulator: perturb one station's
mean cycle time and measure the effect on line throughput, holding every other
random stream fixed via Common Random Numbers (rng.py). Roser (2001) and
Roser & Nakano's published MSE table (docs/CITATIONS.md) validate the Active
Period Method against exactly this kind of sensitivity-derived ground truth --
but they had to use someone else's line. This is the same measurement, run on
our own line, against our own detectors (Phase 5).

Ground-truth definition (Kuo & Lim's sensitivity criterion): the bottleneck is
the station whose perturbation produces the largest |delta throughput /
delta cycle_time|.

Because CRN makes a same-seed (baseline, perturbed) pair an EXACT paired
comparison -- verified empirically: two same-seed, same-config runs produce
identical throughput to full float precision -- the only source of variation
across replications is the *other* random streams (upstream/downstream
variability, variant assignment). Replicating across many seeds and
bootstrapping a confidence interval on the resulting sensitivity samples is
therefore a measurement of how much genuine uncertainty exists in "is this
really the bottleneck", not an artifact of comparing two different runs.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import simpy

from twin.sim.line import Line, LineConfig

DEFAULT_DELTA_FRAC = 0.15
DEFAULT_DURATION_S = 20_000.0
# Below this, upstream pipeline-fill transients bias any cross-station
# comparison (see docs/phases/phase-03-simulation-core.md addendum #4, and
# tests/test_line.py::test_variant_mix_can_genuinely_shift_the_bottleneck).
_MIN_SAFE_DURATION_S = 15_000.0


@dataclass(frozen=True)
class SensitivityResult:
    station_id: str
    delta_frac: float
    n_replications: int
    samples: list[float] = field(repr=False)
    mean_sensitivity: float
    ci_low: float
    ci_high: float


def _throughput_uph(config: LineConfig, seed: int, duration: float, sink_id: str) -> float:
    if seed != config.seed:
        config = _with_seed(config, seed)
    env = simpy.Environment()
    line = Line(env, config)
    env.run(until=duration)
    return line.stations[sink_id].units_completed / duration * 3600.0


def _with_seed(config: LineConfig, seed: int) -> LineConfig:
    """A copy of config with a different seed. Everything else -- topology,
    cycle-time parameters, variant mix -- stays identical, which is what makes
    the (baseline, perturbed) pair at a fixed seed a genuine CRN comparison.
    """
    import copy

    new_config = copy.deepcopy(config)
    new_config.seed = seed
    return new_config


def _perturbed(config: LineConfig, station_id: str, delta_frac: float) -> LineConfig:
    import copy

    new_config = copy.deepcopy(config)
    new_config.base_cycle_time_of[station_id] *= 1.0 + delta_frac
    return new_config


def _bootstrap_ci(
    samples: list[float], n_boot: int = 2000, alpha: float = 0.05
) -> tuple[float, float]:
    rng = np.random.default_rng(0)  # fixed seed: the CI itself must be reproducible
    arr = np.asarray(samples)
    boot_means = np.array(
        [rng.choice(arr, size=len(arr), replace=True).mean() for _ in range(n_boot)]
    )
    lo = float(np.quantile(boot_means, alpha / 2))
    hi = float(np.quantile(boot_means, 1 - alpha / 2))
    return lo, hi


def measure_sensitivity(
    config: LineConfig,
    station_id: str,
    *,
    delta_frac: float = DEFAULT_DELTA_FRAC,
    seeds: list[int] | None = None,
    duration: float = DEFAULT_DURATION_S,
    sink_id: str | None = None,
) -> SensitivityResult:
    """Perturb `station_id`'s mean cycle time by `delta_frac`, replicate across
    `seeds`, and return the sensitivity coefficient with a bootstrap CI.
    """
    if duration < _MIN_SAFE_DURATION_S:
        raise ValueError(
            f"duration={duration} is below the verified-safe {_MIN_SAFE_DURATION_S}s -- "
            "shorter runs bias cross-station comparisons via pipeline-fill transients"
        )
    seeds = seeds if seeds is not None else list(range(1, 21))
    sink_id = sink_id or config.station_ids[-1]

    delta_cycle_time_s = config.base_cycle_time_of[station_id] * delta_frac
    perturbed_config = _perturbed(config, station_id, delta_frac)

    samples: list[float] = []
    for seed in seeds:
        baseline_uph = _throughput_uph(config, seed, duration, sink_id)
        perturbed_uph = _throughput_uph(perturbed_config, seed, duration, sink_id)
        sensitivity = (perturbed_uph - baseline_uph) / delta_cycle_time_s
        samples.append(sensitivity)

    mean_sensitivity = float(np.mean(samples))
    ci_low, ci_high = _bootstrap_ci(samples)

    return SensitivityResult(
        station_id=station_id,
        delta_frac=delta_frac,
        n_replications=len(seeds),
        samples=samples,
        mean_sensitivity=mean_sensitivity,
        ci_low=ci_low,
        ci_high=ci_high,
    )


def measure_all_stations(
    config: LineConfig,
    *,
    delta_frac: float = DEFAULT_DELTA_FRAC,
    seeds: list[int] | None = None,
    duration: float = DEFAULT_DURATION_S,
    station_ids: list[str] | None = None,
) -> list[SensitivityResult]:
    """`station_ids` restricts which stations are measured -- useful for a fast
    spot-check in tests. Omit it (or pass all 30) for a real ground-truth run,
    since the ranking is only meaningful once every candidate has been tried.
    """
    targets = station_ids if station_ids is not None else config.station_ids
    return [
        measure_sensitivity(config, sid, delta_frac=delta_frac, seeds=seeds, duration=duration)
        for sid in targets
    ]


def ground_truth_station(results: list[SensitivityResult]) -> str:
    """The station with the largest |mean sensitivity| -- Kuo & Lim's criterion."""
    return max(results, key=lambda r: abs(r.mean_sensitivity)).station_id


def shifting_trace(
    config: LineConfig,
    variant_weight_sweep: list[dict[str, float]],
    *,
    delta_frac: float = DEFAULT_DELTA_FRAC,
    seeds: list[int] | None = None,
    duration: float = DEFAULT_DURATION_S,
    station_ids: list[str] | None = None,
) -> list[tuple[dict[str, float], str]]:
    """For each variant-weight point in the sweep, return the ground-truth
    station at that point. Demonstrates a genuinely shifting bottleneck driven
    by variant mix, not just a single static ranking.

    `station_ids` restricts the candidate set per point (see
    measure_all_stations) -- a real run should cover all 30; a targeted set is
    fine for a fast test that already knows which two stations to compare.
    """
    import copy

    trace: list[tuple[dict[str, float], str]] = []
    for weights in variant_weight_sweep:
        swept_config = copy.deepcopy(config)
        for v in swept_config.variants:
            v["weight"] = weights[v["id"]]
        results = measure_all_stations(
            swept_config,
            delta_frac=delta_frac,
            seeds=seeds,
            duration=duration,
            station_ids=station_ids,
        )
        trace.append((weights, ground_truth_station(results)))
    return trace


__all__ = [
    "SensitivityResult",
    "ground_truth_station",
    "measure_all_stations",
    "measure_sensitivity",
    "shifting_trace",
]
