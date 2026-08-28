"""Shared feature extraction for Model B -- used identically offline (training
data generation) and online (the live scorer), so a live prediction is never
silently computed differently from what the model was trained on.

Deliberately does NOT modify `Station`'s state machine at all -- that code is
this project's most delicate (`sim/station.py`'s own docstring). Everything
here is external, periodic sampling of already-public Station attributes
(`time_in_state`, `last_cycle_time_s`), the same non-invasive pattern already
proven safe in Phase 5's queue-depth monitor (`diagnostic/run_stats.py`).
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from twin.contracts import StationState

WINDOW_S = 300.0  # docs/DATA.md


@dataclass
class _WelfordStats:
    """Online mean/variance (Welford's algorithm) -- no need to keep every
    historical cycle time in memory to compute a running z-score.
    """

    n: int = 0
    mean: float = 0.0
    m2: float = 0.0

    def update(self, x: float) -> None:
        self.n += 1
        delta = x - self.mean
        self.mean += delta / self.n
        self.m2 += delta * (x - self.mean)

    @property
    def std(self) -> float:
        return (self.m2 / (self.n - 1)) ** 0.5 if self.n >= 2 else 1.0


class FeatureExtractor:
    """Maintains rolling per-station statistics for all five Model B features
    (docs/DATA.md), sampled once per tick.
    """

    def __init__(
        self,
        station_ids: list[str],
        buffer_capacity_of: dict[str, int],
        sample_dt: float,
        *,
        window_s: float = WINDOW_S,
        risk_ewma_alpha: float = 0.3,
    ) -> None:
        self._station_ids = station_ids
        self._buffer_capacity_of = buffer_capacity_of
        self._sample_dt = sample_dt
        self._window_s = window_s
        self._risk_ewma_alpha = risk_ewma_alpha
        max_samples = max(1, round(window_s / sample_dt))

        # REAL BUG, found and fixed while writing this module's own tests
        # (Phase 8): an earlier version of this class sampled
        # `Station.time_in_state` cumulative TOTALS and diffed consecutive
        # readings to approximate "time spent in state X during this
        # interval." That is wrong, because `time_in_state` is only updated
        # AT a state transition (sim/station.py's `_set_state`) -- a station
        # that stays STARVED for many consecutive samples shows delta=0 each
        # time, then dumps its ENTIRE accumulated STARVED duration into a
        # single sample the instant it finally transitions out. Measured
        # directly: `starved_fraction` came out as 4.59 (should be bounded
        # to [0, 1]) for a station that stayed starved for ~160s straight.
        # Fixed by sampling `Station.state` directly at each tick and taking
        # the fraction of samples in the window that were BLOCKED/STARVED --
        # a presence estimator immune to how long any one state lasts.
        self._blocked_hist: dict[str, deque[bool]] = {
            sid: deque(maxlen=max_samples) for sid in station_ids
        }
        self._starved_hist: dict[str, deque[bool]] = {
            sid: deque(maxlen=max_samples) for sid in station_ids
        }
        self._cycle_stats: dict[str, _WelfordStats] = {sid: _WelfordStats() for sid in station_ids}
        self._last_seen_cycle_time: dict[str, float | None] = dict.fromkeys(station_ids)
        self.risk_ewma: dict[str, float] = dict.fromkeys(station_ids, 0.0)

    def sample_tick(self, stations: dict) -> None:
        """Call once per tick with the live {station_id: Station} mapping."""
        for sid in self._station_ids:
            st = stations[sid]
            self._blocked_hist[sid].append(st.state == StationState.BLOCKED)
            self._starved_hist[sid].append(st.state == StationState.STARVED)

            last_ct = st.last_cycle_time_s
            if last_ct is not None and last_ct != self._last_seen_cycle_time[sid]:
                self._cycle_stats[sid].update(last_ct)
                self._last_seen_cycle_time[sid] = last_ct

    def update_risk_ewma(self, station_id: str, latest_risk: float) -> None:
        """Called immediately after computing a station's risk (offline or
        online), before moving to the next station in line order -- so the
        NEXT station's `upstream_risk_ewma` feature reflects it.
        """
        prev = self.risk_ewma[station_id]
        alpha = self._risk_ewma_alpha
        self.risk_ewma[station_id] = alpha * latest_risk + (1 - alpha) * prev

    def features_for(
        self, station_id: str, station, upstream_station_id: str | None
    ) -> dict[str, float]:
        n_samples = len(self._blocked_hist[station_id])
        blocked_fraction = (sum(self._blocked_hist[station_id]) / n_samples) if n_samples else 0.0
        starved_fraction = (sum(self._starved_hist[station_id]) / n_samples) if n_samples else 0.0

        stats = self._cycle_stats[station_id]
        cycle_time_z = 0.0
        if station.last_cycle_time_s is not None and stats.n >= 2:
            cycle_time_z = (station.last_cycle_time_s - stats.mean) / stats.std

        capacity = self._buffer_capacity_of[station_id]
        queue_pressure = (len(station.in_buf.items) / capacity) if capacity > 0 else 0.0

        upstream_risk_ewma = self.risk_ewma[upstream_station_id] if upstream_station_id else 0.0

        return {
            "cycle_time_z": cycle_time_z,
            "queue_pressure": queue_pressure,
            "blocked_fraction": blocked_fraction,
            "starved_fraction": starved_fraction,
            "upstream_risk_ewma": upstream_risk_ewma,
        }


FEATURE_NAMES = [
    "cycle_time_z",
    "queue_pressure",
    "blocked_fraction",
    "starved_fraction",
    "upstream_risk_ewma",
]

__all__ = ["FEATURE_NAMES", "WINDOW_S", "FeatureExtractor"]
