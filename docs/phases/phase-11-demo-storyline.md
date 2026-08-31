# Phase 11 — Demo storyline, one dashboard, schema 0.2.0

Consolidation and reframing pass. The submission now ships exactly one dashboard, ordered as
the demo's own causal loop rather than as a catalogue of panels.

## Schema change: 0.1.0 → 0.2.0

`contracts.py` requires that a field change be recorded here rather than smuggled in.

**Added:** `StationSnapshot.active_period_elapsed_s: float | None` — seconds the station's
*current* active period has been running, `None` when it is not currently active. Additive and
optional, so a replayed fixture or a real OT tap that cannot supply it still validates.

**Why it was necessary.** The dashboard gained a panel answering the first question anyone asks
of a bottleneck verdict: *why this station and not the slowest one?* The first implementation
answered it from `time_in_state`, computing a cumulative active share since run start. That is
a **different quantity from the one the detector ranks on**, and it was caught by checking the
live payload rather than by reading the code:

```
fresh run, ~40 s in
constraint: S04
S01  active 94%  cycle 49.9s
S02  active 86%  cycle 54.4s
S03  active 75%  cycle 48.8s
S04  active 65%  cycle 54.7s   <-- the named constraint, ranked FOURTH
```

The momentary Active Period rule (`diagnostic/bottleneck.py`) ranks stations by whichever
*currently active* station's active period **started earliest** — not by cumulative share.
During line fill an upstream station has been active longer cumulatively simply because it
started working sooner, so an explanation built on `time_in_state` ranks the constraint fourth
and visibly contradicts the verdict it claims to explain.

A panel that looks like an explanation but ranks on the wrong variable is worse than no panel:
it is the "presenting an estimate as a measurement" failure mode this project exists to avoid,
wearing the costume of rigour. Putting the real decision variable on the wire was the only
honest fix.

After the change, against the same live engine:

```
constraint: S17
S17  run 238s   cycle 68.5s   <-- constraint, ranked FIRST
S11  run 211s   cycle 56.5s
S03  run 206s   cycle 52.3s
```

`fixtures/replay_30x60.json` was regenerated so `test_fixture_matches_contract.py`'s
`schema_version` assertion holds against 0.2.0.

## Dashboard changes

- **One UI.** The `/control-center` mount was removed (`docs/CONTROL_CENTER.md` retains the
  design record). Two visual languages read as two unfinished prototypes.
- **Live-proof strip.** Connection lamp, tick, seq, sim clock, RTF, lag, plus Kill / Resume /
  Restart, on every tab above the fold — a recorded demo cannot scroll, and Beat 3 of
  `VIDEO_SCRIPT.md` needs the freeze visible in one frame. Killing the stream now shows an
  explicit frozen banner rather than only dimming.
- **Floor Supervisor reordered** into the causal loop: the line → why this station is the
  constraint → perturb it → watch it respond.
- **Demo mode**, off by default, following `DEMO_SCRIPT.md`'s own running order so the on-screen
  walkthrough and the spoken script cannot drift apart.
- **Removed** the cycle-time-by-station ranking. It sorted on the variable the detector does not
  use and invited the wrong conclusion.

## Real bugs found

- `updateBottleneckPage`'s early return guarded on a removed element, which would have silently
  killed the Plant Manager forecast panel as well.
- `.card-head` lacked `flex-wrap: wrap`, so every card's note was pushed to the far right while
  its heading wrapped to two lines.
- `.grid { align-items: start }` caused the dead space beneath the shorter column of every
  two-column row.
- `uplotThemeColors()` still read `--ink-soft`, renamed to `--ink-2`, silently reintroducing the
  illegible dark-mode chart axes fixed in 246e5aa.
- The old dashboard claimed active-period methods "generalize best overall (90%)". The project's
  own `phase-05` record says Busy Ratio leads at 93.3%. The Method view now states both.
