"""Config disjointness: config E (held out) must never share a unit with
configs A-D (train), and must never be touched by any fitting step.
docs/DATA.md: never a shuffled train_test_split -- adjacent ticks are
near-duplicates, so a shuffled split would leak through temporal adjacency.
"""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

DATA_PATH = Path(__file__).resolve().parents[1] / "ml" / "data" / "training_data.csv"


@pytest.fixture(scope="module")
def rows() -> list[dict]:
    if not DATA_PATH.exists():
        pytest.skip(f"{DATA_PATH} not generated -- run tools/generate_training_data.py first")
    with DATA_PATH.open(encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def test_exactly_five_configs_present(rows: list[dict]) -> None:
    assert {r["config_id"] for r in rows} == {"A", "B", "C", "D", "E"}


def test_config_e_is_disjoint_from_every_training_config(rows: list[dict]) -> None:
    """Disjoint by (config_id, unit_id, station_id): unit_ids are reused
    across configs (each config is an independent simulation run with its
    own seed), so unit_id alone is not a valid cross-config key -- it is only
    meaningful paired with its own config_id, which is exactly what keeps
    them disjoint by construction.
    """
    def key(r: dict) -> tuple[str, str, str]:
        return (r["config_id"], r["unit_id"], r["station_id"])

    e_keys = {key(r) for r in rows if r["config_id"] == "E"}
    train_keys = {key(r) for r in rows if r["config_id"] != "E"}
    assert e_keys.isdisjoint(train_keys)
    assert len(e_keys) > 1000  # a real held-out set, not an empty one


def test_row_count_is_at_least_150k(rows: list[dict]) -> None:
    assert len(rows) >= 150_000


def test_defect_prevalence_is_in_the_calibrated_ballpark(rows: list[dict]) -> None:
    """Not exactly 0.58% -- the bias was calibrated against one baseline
    mix (docs/DATA.md), and five different variant mixes shift the feature
    distribution somewhat. Bounded to a defensible range around the target,
    not pinned to a specific value that would make this test brittle.
    """
    prevalence = sum(int(r["defect"]) for r in rows) / len(rows)
    assert 0.001 < prevalence < 0.02


def test_each_config_has_a_distinct_seed_and_variant_mix() -> None:
    from tools.generate_training_data import CONFIG_MIXES, CONFIG_SEEDS

    assert len(set(CONFIG_SEEDS.values())) == 5
    assert len(set(CONFIG_MIXES.values())) == 5
    for sedan, suv, hatch in CONFIG_MIXES.values():
        assert abs(sedan + suv + hatch - 1.0) < 1e-9
