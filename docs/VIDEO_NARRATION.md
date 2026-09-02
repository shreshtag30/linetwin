# Video Narration — word-for-word, ~3 minutes

Read this aloud. Bracketed lines are actions, not speech. Timings are cumulative.

**Before you hit record:** `uv run python tools/demo.py` — it audits readiness, warms the line, and
prints a cue card. Record at ≥1440 px wide.

> ✅ **All numbers below are current and verified**, including the detector accuracies — the
> benchmark has been regenerated against the shipped simulation (30 scenario×seed trials, all three
> scenarios' ground truth independently re-verified at 60 seeds).

---

## 0:00 — Open (15s)

> "This is LineTwin — a digital twin of a thirty-station vehicle assembly line, running live right
> now. Body, paint, final assembly. Twenty-two of those stations have sensors. Eight are dark — no
> instrumentation at all — because that's what real lines actually look like.
>
> I want to show you three things: that it's genuinely live, that it explains itself, and that the
> numbers on it are ones we can defend."

## 0:15 — The line and the constraint (30s)

*[Floor Supervisor tab. Point at the floor map.]*

> "Green is working. Blue is starved — waiting on the station before it. Orange is blocked — can't
> hand off to the station after it. Red is an unplanned stoppage.
>
> The constraint right now is this station. And here's the part I care about —"

*[Point at "Why this station".]*

> "— it is usually **not** the slowest station on the line. This panel shows why it wins anyway:
> the longest unbroken active run. That's what the Active Period Method actually ranks on. Showing
> you cycle time alone would show you the variable that doesn't decide the verdict."

## 0:45 — The signature move: perturb it (40s)

*[Perturb the line → pick a station → 5× → Apply.]*

> "Anyone can pick the station and the number. I'll slow this one down five times."

*[Narration block appears.]*

> "Before anything happens, it tells you what it *expects* — its outgoing buffer drains slower than
> its incoming one fills, so the station before it should start showing blocked."

*[Wait ~3 seconds for the second block.]*

> "And then, a few seconds later, computed from real snapshots — what actually happened. Including
> when it's wrong. That's a falsifiable claim, scored against the line's own behaviour."

## 1:25 — Plant Manager (25s)

*[Plant Manager tab.]*

> "Across the shift: which stations have been the constraint and for how long. A rolling forecast
> that forks the live state and runs it thirty simulated minutes forward.
>
> And genealogy — a defect found at final inspection, walked backward through that unit's own event
> log to a likely origin station, with the conveyor transfer delay corrected for. That's the problem
> the brief actually names: by the time you find it, forty more units already carry it."

## 1:50 — Leadership (20s)

*[Leadership tab.]*

> "Seventy-three percent sensor coverage. The exposure figure has its formula written on the screen
> and both of its constants stamped *synthetic, uncalibrated* — because they are. This is an
> estimate from named assumptions, not an audited saving, and we say so on the slide rather than in
> a footnote."

## 2:10 — Method: the two numbers that matter (35s)

*[Method tab. Scroll to the degradation curve.]*

> "This is the one I'd want to be asked about. Eight stations are dark, so we infer their cycle-time
> trend from their neighbours using a graph equation.
>
> The purple line is that inference. The dashed line is the dumbest possible alternative — just
> assume every dark station runs at its zone's baseline. **We plot both**, because a method that's
> mathematically correct can still be useless, and only a baseline can tell you which.
>
> It beats that baseline by twenty to thirty-three percent, at every coverage level from ninety
> percent down to forty."

> "Same discipline on bottleneck detection. We benchmarked seven methods against ground truth we
> computed ourselves. The one we deploy scores eighty-seven percent — and Busy Ratio scores ninety.
> It beats us. That's on the slide, because ranking the methods to flatter the one we shipped would
> have been trivial and worthless."

*[Scroll to Model B's operating point.]*

> "And for defect risk — we don't just show you ranking metrics. This row is what the floor
> actually sees: thirty-nine percent of our flags are real, and we raise about **1.6 false alarms
> per true catch**. Recall is low, deliberately — the brief warns that false alarms destroy trust,
> so the model stays quiet unless the signal is strong."

## 2:45 — Prove it's live (25s)

*[Kill stream.]*

> "Every number freezes. The lamp goes red. It says so."

*[Resume, then Restart.]*

> "Resume — it picks straight back up. Restart — back to tick one, fresh run, on the same open
> connection."

## 3:10 — Close (15s)

> "One last thing. Under Kritzinger's taxonomy this is a Digital **Shadow**, not a full Digital
> Twin — data flows one way, it never writes back to a control system. Calling it a Digital Twin
> would have been the easy claim and the wrong one.
>
> Every limitation we know about is on the Method tab, in writing, including the ones that don't
> flatter us."

---

## Verified numbers you may quote

| Claim | Value | Source |
|---|---|---|
| Stations / instrumented / dark | 30 / 22 / 8 | `scenarios/line30.yaml` |
| Sensor coverage | 73% | live |
| Graph inference vs baseline | **+20–33%** at every coverage level | `degradation_curve.csv` |
| Model B PR-AUC | 0.080 (9.5× no-skill) | `station_risk_metrics.json` |
| ROC-AUC | 0.837 | same |
| Lift over `cycle_time_z` | +77.7% | same |
| Precision at threshold | 39.1% | same |
| False alarms per true catch | ~1.6 | same |
| Ground truth rank 1 | S17, matching the engineered bottleneck | `ground_truth.csv` |
| Active Period top-1 | **86.7%** over 30 scenario×seed trials | `detector_comparison_multiscenario.csv` |
| Busy Ratio top-1 | **90.0%** — beats the method we deploy, and we say so | same |
| Breakdown cadence | ~1 per 42s, visible ~2s | measured |

## Do NOT say

- "CI is green" — the workflow exists but has never run.
- "Calibrated" about the genealogy confidence — it's monotone, not calibrated.
- "1−1/e guarantee" about sensor placement — it's a greedy heuristic, and the code says so.
