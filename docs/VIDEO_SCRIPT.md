# Video Script — Three Falsifiability Beats

Three shots, ascending in how hard they are to fake. Each is a single unbroken take — no cuts
inside a beat, since a cut is exactly what would let someone doubt whether the footage was staged.
Screen recording + a visible system clock (menu bar clock is enough) for beats 1 and 3.

## Setup (before recording)

```bash
uv sync --all-extras
uv run python tools/generate_training_data.py
uv run python tools/train_station_risk.py
uv run python tools/run_server.py
```

Open `http://127.0.0.1:8000/` in a real browser window (not the app's own preview pane, for beat 2 —
DevTools needs a real browser). Let it run ~10 seconds before recording starts so the charts already
have a visible trend, not a flat empty line.

---

## Beat 1 — Live perturbation, judge's choice

**What it proves:** the dashboard reacts to a live parameter change in real time, not a scripted
playback.

1. Ask the judge (on camera, or state it yourself if unattended) to name any station ID (S01–S30)
   and any multiplier from 0.1× to 10×.
2. On the Floor Supervisor tab: select that station, drag the slider to that value, click Apply.
3. Keep the system clock visible in frame the whole time.
4. Narrate what should happen: within ~2 seconds, the bottleneck card should update, the WIP chart
   should visibly climb (for a slowdown) or the throughput chart should visibly rise (for a
   speedup), and the acknowledgement text under the button should show "Applied N× to SXX at tick
   T."
5. Let it run 15–20 more seconds so the effect is visually unambiguous, not a single blip.

**Why it's convincing:** the judge picked the input live; nothing about the station or multiplier
was known in advance.

## Beat 2 — Real server push, not a client-side timer

**What it proves:** the stream is genuinely pushed by the server tick loop, not a `setInterval`
faking a live feed in the browser.

1. Open a terminal alongside the browser window.
2. In the terminal: `curl -N http://127.0.0.1:8000/api/twin/stream | grep -o '"tick":[0-9]*' `
   — this prints the raw `tick` field from every frame as it arrives, nothing else.
3. In the browser: open DevTools → Network → find the `stream` request → the EventStream tab. It
   lists every frame with its own tick number.
4. Frame both side by side in one shot: the tick numbers advancing in the terminal must match the
   tick numbers advancing in DevTools, in real time.
5. Hold for ~10 seconds so the matching advance is visible, not a single coincidental frame.

**Why it's convincing:** two independent consumers of the same HTTP connection, one a raw terminal
tool with no rendering logic at all, showing identical numbers — a client-side timer could not
produce a matching raw HTTP stream a `curl` process is reading independently.

## Beat 3 — Kill the process, on camera

**What it proves:** the "live" dashboard is actually driven by the server process, not a recording
or a client-side simulation that would keep running regardless.

1. With the dashboard visibly ticking (vitals bar advancing, charts moving), switch to the terminal
   running `tools/run_server.py`.
2. Press **Ctrl-C** on camera. Narrate that this kills the actual server process.
3. Cut back to the browser (still same unbroken take): every number in the vitals bar should freeze
   at its last value, the connection lamp should turn red, and the charts should stop advancing.
4. Wait a few seconds so the freeze is unambiguous, not a slow frame.
5. Restart: `uv run python tools/run_server.py` again, reload the page. The dashboard should resume
   ticking from a fresh run (new `run_id`, tick counter back near zero).

**Why it's convincing:** nothing about a scripted or recorded demo would freeze in exact sync with
an operator killing an unrelated terminal process — this is the single hardest beat to fake.

---

## After recording

A 1080p legibility pass: play back at actual size and confirm every number named above (tick, the
Apply acknowledgement text, the EventStream tick field) is actually readable, not just present in
frame. Re-shoot any beat where a number is legible only because you already know what it says.
