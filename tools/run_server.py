"""Canonical entrypoint for running LineTwin locally -- the one command the
README's quickstart names. `create_app()` needs a scenario path, so there is
no bare `uvicorn twin.api.routes:create_app --factory` invocation; this
script is that missing wiring.

    uv run python tools/run_server.py

Then open http://127.0.0.1:8000/ .
"""

from __future__ import annotations

import argparse
from pathlib import Path

import uvicorn

from twin.api.routes import create_app

DEFAULT_SCENARIO = Path(__file__).resolve().parents[1] / "scenarios" / "line30.yaml"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", default=str(DEFAULT_SCENARIO))
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--seed", type=int, default=None)
    args = parser.parse_args()

    app = create_app(args.scenario, seed=args.seed)
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
