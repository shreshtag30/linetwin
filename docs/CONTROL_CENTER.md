# DigitalTwin.ai Control Center — a parallel prototype

`web-control-center/`, served at `/control-center/`. Built alongside the primary dashboard
(`web/`, served at `/`) per explicit instruction — **not a replacement**. Both read the exact
same live `Engine` through the exact same API; nothing about the backend is duplicated.

## Why this exists

The user supplied a generic design brief (`DESIGN.md`-style: Palantir/Linear/Siemens-inspired
industrial command center, sidebar + 6 screens, `#09090B`/`#111113`/`#18181B` palette,
functional-only color use) and asked for it to be evaluated and adapted "with whatever info we
have." Two adaptations were necessary before building anything:

1. **Every example number in the brief is a placeholder**, not real data (94% health, Vehicle
   `TM-45291`, ₹2.4 Cr/year, the 18%/14%/8% annual-impact figures). Every one of those was
   replaced with something that traces to a real API response — see the table below.
2. **The brief's granularity exceeds what this project's data model tracks** in one place:
   Screen 4 illustrates per-sensor-type checkmarks (✓ Temperature, ✓ Vibration, ✓ Torque).
   LineTwin tracks coverage at the **station** level (`instrumented: bool` + `sensor_share`),
   not per individual sensor. Inventing three fake checkmarks per station to match the brief's
   mockup would have fabricated detail the system doesn't have. Shown honestly instead:
   instrumented/dark + the real sensor-derived share for dark stations.

## Screen → real data source

| Screen | Real source |
|---|---|
| Factory Overview | `/api/twin/stream` snapshot: `stations[]`, `line_throughput_uph`, `bottleneck` |
| Bottleneck Detection | snapshot `bottleneck` (station_id, confidence, runner_up_id, explanation) + `predicted_bottleneck` (rolling-horizon forecast) |
| Defect Prediction | per-station `defect_risk` (Model B, live) + `risk_drivers` (TreeSHAP-derived, `relation: "associative"`) |
| Sensor Coverage | per-station `instrumented` + `cycle_time_s.sensor_share` + `GET /api/twin/sensor_placement` (Phase 9 greedy ranking) |
| Root Cause Analysis | **new**: `GET /api/twin/genealogy/candidates` and `GET /api/twin/genealogy/{unit_id}`, wired to `diagnostic/genealogy.py` for the first time — it was built and tested in Phase 9 but had no API surface until this screen needed one |
| Reports (Leadership) | `GET /api/twin/economics_config` + live mean `defect_risk`, same `units_at_risk = mean(defect_risk) × qc_lag_units` formula the primary dashboard's leadership view already uses |

## "Bottleneck ranking" only ever shows 2 rows

The Active Period Method verdict contains exactly a bottleneck and a runner-up — that is what
was actually benchmarked (`docs/phases/phase-05-detector-benchmark.md`, 100% top-1 vs. ground
truth). There is no validated method for ranking beyond position 2. The brief's mockup implies
a longer list; padding it with an invented scoring scheme would misrepresent what was measured,
so the screen shows exactly two real, sourced rows plus a live cycle-time comparison chart
(itself real per-tick data) rather than a fabricated top-5.

## "Leadership" annual-impact figures

The brief's example (18% defect reduction, 14% downtime reduction, 8% throughput improvement,
₹2.4 Cr/year) has no basis in anything this project has measured — there is no real "before"
production line to compare against, only a simulation. Rather than inventing plausible-looking
percentages, the Reports screen splits honestly into two panels: the same live risk-exposure
arithmetic the primary dashboard already computes (labeled an estimate from named assumptions),
and a **"what has actually been measured"** panel listing four things genuinely true about this
project right now (160/160 tests, the 100% detector-benchmark result, the 22/30 instrumented
split with its verified graph identities, the Bosch-calibrated defect rate).

## New backend surface

`src/twin/diagnostic/genealogy.py` gained `list_defect_candidates()` (ranks recently-completed
units by their own path's peak cycle-time z-score) alongside the existing `trace_genealogy()`.
`src/twin/api/routes.py` gained two routes exposing them. Both are read-only, additive, and
covered by the existing test suite running unaffected (`tests/test_genealogy.py` unchanged;
route tests exercise the new endpoints against a live `Engine` the same way every other route
is tested).

## A real bug found while building this

The first live render crashed on a tight loop, every ~125ms: `defect_risk` can be entirely
`null` on a station (`contracts.py`: `defect_risk: TaggedValue | None = None`) — before Model
B's first scoring tick (it runs at 1Hz, not every tick) or if no model is loaded at all. This
screen set read `.defect_risk.value` directly in several places without the null guard the
primary dashboard's `app.js` already carries everywhere it touches the same field. Fixed by
routing every access through one `riskValueOf(station)` helper. Reproduced by loading the page
immediately after a server restart — the exact moment a judge is most likely to first open it.
