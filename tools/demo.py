#!/usr/bin/env python3
"""One command to put LineTwin on camera.

    uv run python tools/demo.py

Does four things a bare `tools/run_server.py` does not:

1.  **Refuses to call itself demo-ready when it isn't.** Committed result
    artefacts (the detector benchmark, the degradation curve, the ground
    truth, Model B) are checked against the scenario and simulation sources
    they were produced from. If the simulation changed after a result was
    measured, the number on the dashboard is stale and must not go on
    camera. This is the check that would have caught the whole class of
    problem this project has had to correct by audit.

2.  **Warms the line before you start recording.** Several panels are
    genuinely empty on a cold start, and it is not a bug -- it is the line
    not having run yet. Measured, not guessed (see MILESTONES below): risk
    alerts cannot fire for about four minutes of real time, because queue
    pressure and blocked fraction have not built up. Narrating an alert
    panel that is still empty is an avoidable way to look broken.

3.  **Reports what is live yet**, staged, so you know when each beat of
    `docs/DEMO_SCRIPT.md` becomes performable.

4.  Opens the browser and prints a cue card keyed to the demo script.

The spoken walkthrough lives in `docs/DEMO_SCRIPT.md`, and the three
falsifiability beats for a recorded proof video in `docs/VIDEO_SCRIPT.md`.
This script does not duplicate either; it gets you to the point where they
are true.
"""

from __future__ import annotations

import argparse
import contextlib
import csv
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCENARIO = ROOT / "scenarios" / "line30.yaml"
MODELS_DIR = ROOT / "ml" / "models"
PHASES = ROOT / "docs" / "phases"

# Sources whose change invalidates a measured result. If any is newer than a
# result artefact, that artefact was measured against different behaviour.
SIM_SOURCES = [
    SCENARIO,
    ROOT / "src" / "twin" / "sim" / "line.py",
    ROOT / "src" / "twin" / "sim" / "station.py",
    ROOT / "src" / "twin" / "sim" / "dists.py",
]

# (tick, what becomes true). Ticks are 0.125 real seconds apart (contracts.py
# REAL_DT), so tick 2000 is about four real minutes.
MILESTONES: list[tuple[int, str]] = [
    (1, "KPIs, the line map, the constraint card and the risk list are populated"),
    (8, "rolling-horizon forecast has completed its first pass"),
    (40, "genealogy candidates fetched (throttled HTTP, not part of the stream)"),
    (350, "a breakdown has probably occurred somewhere (~1 per 42 real seconds)"),
    (600, "constraint movements and the alerts drawer usually have entries"),
    (2000, "risk alerts are reachable -- queue pressure has built up (measured)"),
]

DEFAULT_WARMUP_TICKS = 600


# ---------------------------------------------------------------------------
# Readiness audit
# ---------------------------------------------------------------------------


def _newest(paths: list[Path]) -> tuple[Path | None, float]:
    existing = [p for p in paths if p.exists()]
    if not existing:
        return None, 0.0
    newest = max(existing, key=lambda p: p.stat().st_mtime)
    return newest, newest.stat().st_mtime


def check_readiness() -> list[tuple[str, str]]:
    """Returns (level, message) with level in {ok, warn, stale, fail}."""
    out: list[tuple[str, str]] = []
    newest_src, newest_src_mtime = _newest(SIM_SOURCES)

    # --- Model B -----------------------------------------------------------
    needed = [
        MODELS_DIR / "station_risk_model.json",
        MODELS_DIR / "station_risk_calibrator.pkl",
        MODELS_DIR / "station_risk_threshold.txt",
    ]
    if not all(p.exists() for p in needed):
        out.append(
            (
                "fail",
                "Model B not trained. Run:\n"
                "      uv run python tools/generate_training_data.py\n"
                "      uv run python tools/train_station_risk.py\n"
                "    The dashboard still runs without it and says 'no model loaded' "
                "honestly, but every defect-risk panel will be empty.",
            )
        )
    else:
        metrics_path = MODELS_DIR / "station_risk_metrics.json"
        if metrics_path.exists() and metrics_path.stat().st_mtime < newest_src_mtime:
            out.append(
                (
                    "stale",
                    f"Model B was trained BEFORE {newest_src.name} last changed. Its metrics "
                    "describe a different line. Retrain before recording.",
                )
            )
        elif metrics_path.exists():
            m = json.loads(metrics_path.read_text())["model"]
            out.append(
                (
                    "ok",
                    f"Model B loaded -- PR-AUC {m['pr_auc']:.3f}, "
                    f"precision {m['precision_at_threshold']:.1%} at threshold "
                    f"{m['threshold']}",
                )
            )

    # --- Degradation curve, including its baseline arm ---------------------
    curve = PHASES / "degradation_curve.csv"
    if not curve.exists():
        out.append(("warn", "degradation_curve.csv missing -- the Method tab plot will be empty."))
    else:
        with curve.open(encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
        if not rows or "prior_only_mean_relative_error" not in rows[0]:
            out.append(
                (
                    "stale",
                    "degradation_curve.csv has no baseline arm -- it predates the audit that "
                    "added one. Re-run tools/run_degradation_experiment.py.",
                )
            )
        elif curve.stat().st_mtime < newest_src_mtime:
            out.append(("stale", f"degradation_curve.csv is older than {newest_src.name}."))
        else:
            worst = min(float(r["improvement_over_prior_pct"]) for r in rows)
            best = max(float(r["improvement_over_prior_pct"]) for r in rows)
            level = "ok" if worst > 0 else "warn"
            out.append(
                (
                    level,
                    f"Degradation curve current -- graph layer beats the zone-base baseline by "
                    f"{worst:.0f}-{best:.0f}% across coverage levels",
                )
            )

    # --- Ground truth: does it still name the engineered bottleneck? -------
    gt = PHASES / "ground_truth.csv"
    engineered = _engineered_station()
    if not gt.exists():
        out.append(("warn", "ground_truth.csv missing."))
    else:
        with gt.open(encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
        if rows:
            top = rows[0]["station_id"]
            if gt.stat().st_mtime < newest_src_mtime:
                out.append(("stale", f"ground_truth.csv is older than {newest_src.name}."))
            elif engineered and top != engineered:
                out.append(
                    (
                        "warn",
                        f"Ground truth ranks {top} first, but the scenario engineers "
                        f"{engineered}. Not necessarily wrong -- but do not claim "
                        f"{engineered} is 'the' bottleneck on camera without checking.",
                    )
                )
            else:
                out.append(("ok", f"Ground truth ranks {top} first, matching the scenario"))

    # --- Detector benchmark ------------------------------------------------
    bench = PHASES / "detector_comparison_multiscenario.csv"
    if not bench.exists():
        out.append(("warn", "detector_comparison_multiscenario.csv missing."))
    elif bench.stat().st_mtime < newest_src_mtime:
        out.append(
            (
                "stale",
                f"detector_comparison_multiscenario.csv is older than {newest_src.name}. "
                "The accuracy figures on the Method and Leadership tabs were measured "
                "against a DIFFERENT simulation and must not go on camera. Re-run:\n"
                "      uv run python tools/run_detector_benchmark_multiscenario.py",
            )
        )
    else:
        out.append(("ok", f"Detector benchmark current ({_top1_summary(bench)})"))

    return out


def _engineered_station() -> str | None:
    try:
        for line in SCENARIO.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped.startswith("station_id:"):
                return stripped.split(":", 1)[1].strip()
    except OSError:
        pass
    return None


def _top1_summary(bench: Path) -> str:
    with bench.open(encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    tally: dict[str, list[int]] = {}
    for r in rows:
        hit, total = tally.setdefault(r["detector"], [0, 0])
        tally[r["detector"]] = [hit + (r["top1_correct"].strip().lower() == "true"), total + 1]
    if "active_period" not in tally:
        return "no active_period rows"
    hit, total = tally["active_period"]
    return f"Active Period {100 * hit / total:.1f}% top-1 over {total} trials"


# ---------------------------------------------------------------------------
# Server + warm-up
# ---------------------------------------------------------------------------


def _get_json(url: str, timeout: float = 2.0) -> dict | None:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return json.loads(resp.read())
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError):
        return None


def wait_for_health(base: str, deadline_s: float = 60.0) -> bool:
    end = time.monotonic() + deadline_s
    while time.monotonic() < end:
        if _get_json(f"{base}/healthz") is not None:
            return True
        time.sleep(0.4)
    return False


def warm_up(base: str, target_tick: int) -> None:
    """Blocks until the engine reaches `target_tick`, narrating what becomes
    available on the way. Milestones are measured, not decorative -- see
    MILESTONES and docs/DEMO_SCRIPT.md.
    """
    announced: set[int] = set()
    last_line = ""
    while True:
        state = _get_json(f"{base}/api/twin/state")
        tick = state["tick"] if state else 0

        for at, what in MILESTONES:
            if tick >= at and at not in announced and at <= target_tick:
                announced.add(at)
                print(f"\r  [tick {tick:>5}] {what}".ljust(len(last_line)))
                last_line = ""

        if tick >= target_tick:
            print(f"\r  [tick {tick:>5}] warm-up complete".ljust(len(last_line)))
            return

        pct = min(100, int(100 * tick / target_tick)) if target_tick else 100
        last_line = f"\r  warming up... tick {tick}/{target_tick} ({pct}%)"
        print(last_line, end="", flush=True)
        time.sleep(0.5)


CUE_CARD = """
────────────────────────────────────────────────────────────────────────────
  CUE CARD -- full walkthrough in docs/DEMO_SCRIPT.md
────────────────────────────────────────────────────────────────────────────
  1  Floor Supervisor   the line, then "Why this station" -- the constraint is
                        usually NOT the slowest station, and that panel says so
  2  Perturb the line   pick a station, 5x, Apply. "What this should do" appears
                        immediately; "What actually happened" ~3s later, computed
                        from real snapshots -- including when it was wrong
  3  Plant Manager      constraint residency, forecast, genealogy trace
  4  Leadership         coverage, exposure formula in the open, next sensors
  5  Method             the degradation curve WITH its baseline arm, and Model B's
                        operating point (precision / false alarms per catch)
  6  Kill stream        lamp red + banner + page dims. Resume. Then Restart.

  Colours: green=working  blue=starved  orange=blocked  red=down (blinking)
  Record at ≥1440px wide -- the proof strip scrolls its buttons out of frame below ~1100px.
────────────────────────────────────────────────────────────────────────────
"""


def main() -> int:
    # Windows consoles default to cp1252; opt into UTF-8 when available so a
    # stray non-ASCII character in a message can never crash the launcher.
    with contextlib.suppress(Exception):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument(
        "--warmup-ticks",
        type=int,
        default=DEFAULT_WARMUP_TICKS,
        help=f"ticks to run before opening the browser (default {DEFAULT_WARMUP_TICKS}; "
        "2000 for risk alerts, 0 to skip)",
    )
    parser.add_argument("--check", action="store_true", help="run the readiness audit and exit")
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()

    print("\n== Demo readiness " + "=" * 56)
    results = check_readiness()
    symbols = {"ok": "  ok  ", "warn": " warn ", "stale": "STALE ", "fail": " FAIL "}
    for level, message in results:
        print(f"  [{symbols.get(level, level):^6}] {message}")

    blocking = [m for lvl, m in results if lvl in ("fail", "stale")]
    if blocking:
        print(
            "\n  ^ Fix the STALE/FAIL items above before recording. A stale artefact means a\n"
            "    number on the dashboard was measured against a different simulation."
        )
    if args.check:
        return 1 if blocking else 0

    base = f"http://{args.host}:{args.port}"
    print(f"\n== Starting server on {base} " + "=" * max(0, 40 - len(base)))
    proc = subprocess.Popen(
        [sys.executable, str(ROOT / "tools" / "run_server.py"),
         "--host", args.host, "--port", str(args.port),
         "--log-level", "warning"],
        cwd=ROOT,
    )
    try:
        if not wait_for_health(base):
            print("  server did not become healthy within 60s -- is the port already in use?")
            proc.terminate()
            return 1
        print("  healthy")

        if args.warmup_ticks > 0:
            print(f"\n== Warming the line ({args.warmup_ticks} ticks ~ "
                  f"{args.warmup_ticks * 0.125:.0f}s) " + "=" * 20)
            warm_up(base, args.warmup_ticks)

        if not args.no_browser:
            webbrowser.open(base + "/")
        print(CUE_CARD)
        print(f"  Serving {base}/ -- Ctrl-C to stop.\n")
        proc.wait()
    except KeyboardInterrupt:
        print("\n  stopping server")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
    return 0


if __name__ == "__main__":
    sys.exit(main())
