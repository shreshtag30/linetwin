# Demo Script — Running LineTwin Live

A practical walkthrough for demoing the dashboard to a judge or teammate. This is different from
`docs/VIDEO_SCRIPT.md` (the three falsifiability beats for a recorded proof video) — this is what to
actually click through and say in a live, in-person or video-call demo.

## 1. Start the server

```bash
uv sync --all-extras
uv run python tools/run_server.py
```

Wait for `Uvicorn running on http://127.0.0.1:8000` in the terminal, then open
`http://127.0.0.1:8000/` in a real browser. Give it 10–15 seconds before saying anything — the charts
need a few ticks of history before they show a trend instead of a flat empty line.

If something looks stale (styling not updating, a button not responding), it's almost always a
browser cache issue — hard-refresh (Cmd+Shift+R / Ctrl+Shift+R) before assuming it's broken.

## 2. Live Overview — the default landing page

This is the Floor Supervisor's page and the one to open on. Point out, in order:

- **KPI row** at the top — throughput, WIP, cycle time, blocked %, starved %, mean defect risk. All
  live numbers computed from the running simulation, not placeholders.
- **Floor map** — 30 stations across Body / Paint / Final zones. Green = working, grey = starved
  (waiting on the station before it), red = blocked (can't hand off to the station after it), orange
  = down/repair/setup. Click any station to populate the detail bar below the map.
- **Bottleneck card** — names the current constraint station, a confidence label
  (*provisional*/*established*, never a fabricated precision), and a plain-language explanation of
  why. This updates live as the constraint shifts.
- **Data Coverage donut** — 22 of 30 stations (73%) carry real sensors; the other 8 are inferred via
  graph propagation from their neighbors. This ratio is a fixed property of the scenario's sensor
  layout, not a live metric — it will always read 73% because that's how many stations are actually
  wired, the same way a factory's sensor installation doesn't change tick to tick. What *is* live is
  each dark station's own inferred value and its confidence.

## 3. The signature move: apply a live perturbation

This is the one interaction that changes the real simulation, and the best thing to let a judge
drive themselves.

1. Scroll to **Quick Control**. Pick any station from the dropdown and drag the multiplier slider
   (0.1×–10×).
2. Click **Apply**. Watch the acknowledgement text confirm `Applied N× to SXX at tick T`, then watch
   the bottleneck card and charts respond within ~2 seconds.
3. **Switching stations remembers what you last applied to each one** — dial S15 to 5.9× and apply,
   switch to S18 (shows the neutral 1.0× default, since you haven't touched it), switch back to S15
   and it recalls 5.9×. This mirrors how an operator actually works the panel: one station at a time,
   without losing track of what's already been dialed elsewhere.

### To make a station visibly BLOCK on demand (rather than waiting)

Blocking is real but state-dependent: at buffer_capacity=3, a given station only accumulates ~5–9%
blocked time over several minutes, so an instantaneous snapshot most often shows zero currently-
blocked stations — that's expected, not a bug. To force one into view immediately: apply a large
multiplier (e.g. 5×) to a station a few positions downstream of one you're watching. The upstream
station's outgoing buffer fills within seconds and it turns red (blocked) while the slowed station
itself turns starved-adjacent upstream and busy downstream — a live cascade, not a scripted one.

## 4. Tour the remaining pages (sidebar)

- **Stations** — every station in one list with the same legend as the floor map, for when a judge
  wants to scan all 30 at once instead of the zone-grouped map view.
- **Bottleneck** — the full explanation of the Active Period Method, its benchmark result (90% top-1
  across three distinct engineered-bottleneck scenarios, honestly reported including where a
  competing method occasionally edges it out on a single scenario), and the rolling-horizon forecast
  comparing the current bottleneck to a ~30-simulated-minute-ahead prediction.
- **Quality & Defects** — live per-station defect-risk ranking, click one for its top contributing
  factors (explicitly labeled *associative, not causal*), and a genealogy tracer: pick a flagged unit
  to walk its path back through the line to a likely origin station with a calibrated confidence.
- **Trends** — the Plant Manager's view: bottleneck frequency this session, a rolling-horizon
  predicted-downtime card, longer-window throughput/WIP charts, and a shifting-bottleneck timeline.
- **Alerts** — every bottleneck-shift alert logged this session; the topbar bell badge jumps here.
- **Reports** — the Leadership view: an ROI estimate with its formula shown in the open
  (`units_at_risk = mean(defect_risk) × qc_lag_units`), and Phase 9's greedy sensor-placement
  recommendation for where the next sensor budget buys the most.
- **Settings** — theme, connection diagnostics, and the project's positioning statement (Digital
  Shadow / Digital Twin Prototype / Predictive maturity, cited).

The **role button** in the topbar (next to the bell) cycles Floor Supervisor → Plant Manager →
Leadership, jumping straight to each persona's primary page — a fast way to show all three
perspectives without manually clicking through the sidebar.

## 5. Prove it's really live (optional, borrows from the video script)

- **Kill / Resume** (Settings page, or the topbar buttons): kill the stream, watch every number
  freeze and the connection lamp turn red; resume, watch it pick back up.
- **Restart**: resets the simulation from tick zero with a fresh run ID.
- For the strongest proof, see `docs/VIDEO_SCRIPT.md` Beat 2 — open DevTools → Network → EventStream
  next to a `curl -N http://127.0.0.1:8000/api/twin/stream` in a terminal and show the same tick
  numbers advancing in both, live.

## 6. Closing point

Every number on this dashboard is either a direct simulation measurement, a value computed by a
named model (bottleneck detector, defect-risk scorer, or graph-based sensor-gap inference) and
labeled as such, or an estimate built from an explicit, visible formula. Nothing is hand-typed to
look plausible — see `docs/CITATIONS.md` for the full discipline behind that claim.
