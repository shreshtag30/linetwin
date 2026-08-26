"""Phase 2 exit gate: the generated fixture conforms to the frozen contract."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from twin.contracts import SCHEMA_VERSION, SIM_DT, Snapshot, StationState

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "replay_30x60.json"


@pytest.fixture(scope="module")
def raw() -> dict:
    with FIXTURE.open(encoding="utf-8") as fh:
        return json.load(fh)


def test_fixture_exists_and_has_sixty_snapshots(raw: dict) -> None:
    assert len(raw["snapshots"]) == 60


def test_every_snapshot_validates_against_the_frozen_contract(raw: dict) -> None:
    parsed = [Snapshot.model_validate(s) for s in raw["snapshots"]]
    assert len(parsed) == 60
    assert all(s.schema_version == SCHEMA_VERSION for s in parsed)


def test_tick_is_strictly_monotonic(raw: dict) -> None:
    ticks = [s["tick"] for s in raw["snapshots"]]
    assert ticks == sorted(ticks)
    assert len(set(ticks)) == len(ticks)


def test_sim_time_equals_tick_times_sim_dt_exactly(raw: dict) -> None:
    for s in raw["snapshots"]:
        assert s["sim_time_s"] == pytest.approx(s["tick"] * SIM_DT, abs=1e-9)


def test_thirty_stations_with_the_planned_instrumentation_split(raw: dict) -> None:
    first = raw["snapshots"][0]
    assert len(first["stations"]) == 30
    instrumented = sum(1 for st in first["stations"] if st["instrumented"])
    assert instrumented == 22, "docs/DECISIONS.md commits to 22 of 30 instrumented"
    assert 30 - instrumented == 8


def test_dark_stations_are_tagged_inferred_not_observed(raw: dict) -> None:
    for st in raw["snapshots"][0]["stations"]:
        expected_source = "inferred" if not st["instrumented"] else "observed"
        assert st["cycle_time_s"]["source"] == expected_source


def test_dark_station_confidence_is_never_asserted_as_certain(raw: dict) -> None:
    for st in raw["snapshots"][0]["stations"]:
        if not st["instrumented"]:
            assert st["cycle_time_s"]["confidence"] < 1.0
            assert st["cycle_time_s"]["sensor_share"] is not None


def test_down_classifies_as_active_and_starved_as_inactive() -> None:
    # The single most load-bearing fact in the whole diagnostic layer.
    assert StationState.DOWN.is_active is True
    assert StationState.STARVED.is_active is False
    assert StationState.BLOCKED.is_active is False
    assert StationState.WORKING.is_active is True
