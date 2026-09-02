# Demo Script — Running LineTwin Live

A practical walkthrough for demoing the dashboard to a judge or teammate. This is different from
`docs/VIDEO_SCRIPT.md` (the three falsifiability beats for a recorded proof video) — this is what to
actually click through and say in a live, in-person or video-call demo.

> **Rewritten against the shipped UI.** The previous version described a retired sidebar dashboard:
> it named a *Live Overview* page, a *Quick Control* panel, a *Settings* page, a topbar *role button*
> that cycled personas, and it had the floor-map colours inverted. None of those exist. The UI is
> **one page with four persona tabs**. Every element named below was checked against
> `web/index.html`.

## 1. Start the server

```bash
uv sync --all-extras
uv run python tools/generate_training_data.py
uv run python tools/train_station_risk.py
uv run python tools/run_server.py
```

The two middle steps train Model B. Skip them and the dashboard still runs — it just shows
"no model loaded" wherever defect risk would be, honestly, rather than blank panels.

Wait for `Uvicorn running on http://127.0.0.1:8000`, then open `http://127.0.0.1:8000/`.
**Give it ~30 seconds before saying anything.** The KPIs, floor map and constraint card populate on
the first tick, but the forecast needs ~1 s, the genealogy panel ~5 s plus completed units, and the
Plant Manager timeline and alert drawer are empty until something actually changes.

If something looks stale, it's almost always a browser cache issue — hard-refresh
(Ctrl+Shift+R / Cmd+Shift+R) before assuming it's broken.

## 2. Floor Supervisor — the default tab

Point out, in order:

- **KPI row** — throughput, WIP, mean cycle, blocked, starved, mean defect risk. Blocked and starved
  are **counts of stations**, not percentages. Blocked commonly reads 0 and the label says
  "none now — expected"; that is a real state of a healthy line, not a dead panel.
- **The line** — 30 stations across Body / Paint / Final. Colours, checked against `styles.css`:
  **green = working**, **blue = starved** (waiting on the station before it), **orange = blocked**
  (can't hand off to the station after it), **red = down/repair** (an unplanned stoppage, and it
  blinks). A dashed ring means no sensor. Click any station to fill the detail bar below.
- **Current constraint** — names the constraint station, a confidence label (*provisional* /
  *established*, never a fabricated precision), and **"Why this station"**: the ranked unbroken
  active-run times beside each station's cycle time. This is the panel that answers the first
  question anyone asks — *why isn't it just the slowest station?* — and usually it isn't.
- **Mode** — how the constraint's active time splits across working / down / repair. A station that
  is constraint-by-downtime reads differently from one that is constraint-by-slow-work.

> **On risk alerts, measured so you don't narrate a panel that stays empty.** The alert fires when a
> station's calibrated risk crosses Model B's own MCC-tuned threshold (0.17). Measured over a
> 15,000 sim-second run: scores do cross it — peak 0.53 on a baseline line, and **0.15–0.25% of all
> station-scores** exceed it — but the line needs to be **warm** first. Over a 14-second run,
> nothing came close (peak 0.0036), because queue pressure and blocked fraction have not built up
> yet. **Budget ~3–4 minutes of runtime before promising an alert on camera**, or open the alerts
> drawer having already let it run. The bell badge counts bottleneck-shift alerts too, which appear
> much sooner.

> **On breakdowns, measured so you can plan the shot.** Unplanned stoppages are deliberately rare —
> **one somewhere on the line roughly every 42 real seconds, each visible for about 2 seconds** as a
> blinking red station (0.18% of station-time). That is a realistic rate, not a demo-friendly one. If
> you want a stoppage on camera, budget a couple of minutes of runtime and watch the floor map, or
> say up front that they are rare by design. Do **not** claim the Mode panel will show a repair share
> on demand — early in a run it will read `working 100%`, which is correct.

## 3. The signature move: apply a live perturbation

The one interaction that changes the real simulation, and the best thing to let a judge drive.

1. In **Perturb the line**, pick any station and drag the multiplier (0.1×–10×).
2. Click **Apply**. The acknowledgement reads `Applied N× to SXX at tick T`, and a full-width
   **"What this should do"** narration appears explaining the mechanism *before* the effect lands.
3. About three seconds later a second block, **"What actually happened"**, is computed from real
   snapshots — including when the prediction did *not* come true. That is deliberate: it is a
   falsifiable claim scored against the line's own behaviour.
4. **Switching stations remembers what you last applied to each one** — dial S15 to 5.9× and apply,
   switch to S18 (neutral 1.0×), switch back to S15 and it recalls 5.9×.

### To make a station visibly BLOCK on demand

Blocking is real but state-dependent — the paint zone runs `buffer_capacity: 3` (body is 6, final
is 5), so an instantaneous snapshot most often shows zero blocked stations. To force one into view:
apply a large multiplier (5×) to a station a few positions **downstream** of the one you're
watching. Its input buffer backs up within seconds and the upstream station turns orange.

## 4. Plant Manager — across the shift

- **Constraint residency** — share of observed ticks each station has been the constraint this
  session. S17 is the engineered bottleneck and dominates, but it does **not** win every instant:
  correlated condition drift means the constraint genuinely moves, which is the point.
- **Forecast** — the live state forked and fast-forwarded ~30 sim-minutes, refreshed about once a
  second. When it disagrees with the live verdict, the panel says a shift may be forming.
- **Constraint movements** — every change logged with the tick it happened at. Empty on a cold
  start; **apply a perturbation first if you want this populated on camera.**
- **Units worth tracing / Genealogy** — pick a unit and walk its path back to a likely origin
  station, with the transfer-delay realignment applied. The head of the chain says
  *Final inspection* only if the unit actually reached the last station, and *Still in progress*
  otherwise.

## 5. Leadership — the case

- **Sensor coverage** — 22 of 30 wired (73%). A fixed property of the scenario's sensor layout, not
  a live metric; a factory's sensor installation doesn't change tick to tick. What *is* live is each
  dark station's inferred value and its confidence.
- **Risk exposure** — the formula is shown in the open, and both constants are stamped
  `synthetic — uncalibrated` on screen. Say plainly that this is an exposure estimate from named
  assumptions, not an audited saving.
- **Next sensors to fit** — greedy ranking of which dark station to instrument next, re-solved after
  each pick. Drag the budget slider and the ranking updates.
- **Measured, not projected** — four numbers, each traceable to a committed artefact.

## 6. Method — how it is proven

The tab to open when someone asks whether any of this is real.

- **Sensor coverage vs inference error** — the degradation curve, plotted with **both arms**: the
  graph layer against the trivial "just use this station's zone base" baseline it has to beat. It
  beats it by 20–32% at every coverage level, and error stays flat as coverage falls from 90% to
  40%. Worth dwelling on: the baseline arm exists because an identity test can prove the maths is
  right while the method is still useless.
- **Model B** — ranking metrics *and* the operating point: precision, recall, flag rate, and false
  alarms per true catch. Show the second row deliberately. The brief warns that false alarms erode
  floor trust, and these are the numbers that decide it.
- **Where it breaks** — read one aloud. It is a stronger move than any of the metrics.

## 7. Prove it's really live

- **Kill stream** → every number freezes, the lamp turns red, a banner says so, the page dims.
  **Resume** → it picks back up.
- **Restart** → tick counter drops to 1 and climbs again, on the same connection, within about half
  a second.
- For the strongest proof see `docs/VIDEO_SCRIPT.md` Beat 2: DevTools → Network → EventStream beside
  a `curl -N http://127.0.0.1:8000/api/twin/stream` in a terminal, same tick numbers advancing in
  both.

## 8. Closing point

Every number on this dashboard is either a direct simulation measurement, a value computed by a
named model (bottleneck detector, defect-risk scorer, graph-based sensor-gap inference) and labelled
as such, or an estimate built from an explicit, visible formula. Nothing is hand-typed to look
plausible — `docs/CITATIONS.md` is the full ledger, including the claims this project has had to
withdraw after auditing itself.
