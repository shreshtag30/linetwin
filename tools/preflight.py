"""One-command readiness check -- runs exactly the steps CI runs, in the
same order (.github/workflows/ci.yml), so "does this pass on a fresh
machine" has one answer instead of five separately-run commands that could
silently drift from what CI actually checks.

    uv run python tools/preflight.py

Exits non-zero on the first failing step, with that step's own output
printed in full -- deliberately not "cleaned up," since preflight is a
diagnostic tool, not a demo.
"""

from __future__ import annotations

import subprocess
import sys
import time

STEPS: list[tuple[str, list[str]]] = [
    ("Lint", ["uv", "run", "ruff", "check", "."]),
    ("Regenerate fixture (determinism check)", ["uv", "run", "python", "tools/gen_fixture.py"]),
    (
        "Generate Model B training data",
        ["uv", "run", "python", "tools/generate_training_data.py"],
    ),
    ("Train Model B", ["uv", "run", "python", "tools/train_station_risk.py"]),
    ("Test", ["uv", "run", "pytest", "-q"]),
]


def main() -> int:
    for name, cmd in STEPS:
        print(f"\n== {name} " + "=" * max(0, 60 - len(name)))
        t0 = time.perf_counter()
        result = subprocess.run(cmd)
        dt = time.perf_counter() - t0
        if result.returncode != 0:
            print(f"\nFAILED: {name} (after {dt:.1f}s, exit {result.returncode})")
            return result.returncode
        print(f"-- {name} ok ({dt:.1f}s)")

    print("\nAll preflight steps passed. Ready for a demo or a fresh-clone check.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
