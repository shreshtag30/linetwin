"""Enforces the boundary pyproject.toml's `ml` extras group comments on:
pandas/ucimlrepo/matplotlib/imbalanced-learn are for the offline benchmark
(Model A) only, and must never be reachable by importing `twin` -- the server
package should run with only its core runtime dependencies installed.
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC_TWIN = Path(__file__).resolve().parents[1] / "src" / "twin"

FORBIDDEN_TOP_LEVEL_IMPORTS = {"pandas", "ucimlrepo", "matplotlib", "imblearn", "ml"}


def _top_level_imports(py_file: Path) -> set[str]:
    tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split(".")[0])
    return names


def test_no_server_module_imports_an_ml_only_or_benchmark_only_package() -> None:
    violations: dict[str, set[str]] = {}
    for py_file in SRC_TWIN.rglob("*.py"):
        found = _top_level_imports(py_file) & FORBIDDEN_TOP_LEVEL_IMPORTS
        if found:
            violations[str(py_file.relative_to(SRC_TWIN.parents[1]))] = found

    assert not violations, (
        f"src/twin/ must never import ml-benchmark-only packages, found: {violations}"
    )
