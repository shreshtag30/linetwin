# Solution Design — the parts that are design, not code

Three areas of the Round 2 brief are answered by **design** in this project, not by running code:
low-cost retrofit sensing, OT/PLC integration, and the maintenance-window rollout. This document
exists because an audit found all three asserted in passing — a sentence in a traceability matrix, a
docstring, a UI label — with nothing behind them. Asserting a rollout plan that does not exist is the
same class of error as asserting a metric that was never computed.

**Everything here is labelled for what it is.** Where a number would need a vendor quote or a plant's
own data, it says so instead of inventing one. Nothing in this document is implemented in the
prototype; `docs/REQUIREMENTS.md` marks these rows **Design only** for exactly that reason.

---

## 1. Instrumentation tiers and low-cost retrofit sensing

### What the twin actually needs, per layer

This is the load-bearing table, and it is deliberately not "more sensors are better". Each analytical
layer has a different minimum, and two of the three run on signals most automotive lines already
emit.

| Layer | Minimum signal | Already present on a typical line? | Recoverable by inference if missing? |
|---|---|---|---|
| Flow / bottleneck detection | One completion event per station per unit: a timestamp saying "a unit left here" | **Yes** — a photo-eye, proximity switch, or PLC completion bit. Starved/blocked is *derived* from adjacent buffer occupancy, not separately sensed | Not needed — assumed present everywhere |
| Defect genealogy | The same completion log, plus one defect flag from final inspection | Flow half yes; the inspection flag is the one genuinely new requirement | Not applicable — it realigns existing records, it does not infer missing ones |
| Cycle-time trend at a dark station | Neighbouring stations' completion timestamps | Yes, if neighbours are instrumented | **Yes** — this is what the graph layer does, measured at 20–32% better than a zone-base prior (`docs/phases/degradation_curve.csv`) |
| Per-unit quality / defect risk | Station-level process signals (torque, vibration, temperature) tied to a unit id | **Often not** — this is where the gap genuinely bites | **No.** Stated plainly: harmonic extension recovers a *trend*, never whether a *specific unit* was defective. A missing quality signal is not recoverable by graph propagation |

The last row is the honest boundary of this whole approach, and it is why the tiering below puts
quality sensing in the expensive tier rather than pretending inference substitutes for it.

### Retrofit tiers

Ordered by cost per station, cheapest first. **Costs are ordinal, not quoted** — a real business case
needs vendor quotes and the plant's own installation labour rates, neither of which this project has.
What is stated is the *relative* ordering and the *installation constraint*, which are the parts that
drive the rollout plan.

**Tier 0 — Read what already exists. No hardware, no window.**
Most stations already produce a completion signal into a PLC that a historian or OPC-UA server can
expose. This is a configuration and integration exercise, not an installation: no station is touched,
no wiring changes, nothing enters the control path. Tier 0 alone lights up bottleneck detection,
genealogy, and the graph inference layer — i.e. most of this prototype.

**Tier 1 — Non-invasive, clamp-on, no control-path contact.**
Sensors that mount beside or around equipment without being wired into it:
- **Clamp-on current transformer** on a drive feed — motor current is a strong proxy for load and
  for cycle boundaries, and a CT clips around an existing conductor.
- **Adhesive-mounted accelerometer** for vibration — sticks to a housing.
- **Infrared spot / thermal patch** for surface temperature.

These share the property that matters for the rollout: they attach to the *outside* of equipment and
report to their own gateway, so they neither modify PLC logic nor sit in the control loop. They are
an order of magnitude cheaper per point than tier 2 and are the natural candidates for the placement
ranking the Leadership tab produces.

**Tier 2 — In-line process instrumentation.**
Torque transducers on fastening spindles, in-line dimensional gauging, per-unit test stands. These
are what genuinely close the *quality* gap — the row above that inference cannot cover. They are
substantially more expensive, usually require mechanical integration, and are the only tier that
plausibly needs a station rebuilt rather than a sensor added.

### What the prototype does and does not do here

It **does** rank which currently-dark station would most improve evidence coverage if instrumented
next (`graph/placement.py`, exposed at `/api/twin/sensor_placement`). It **does not** recommend
*which sensor* to fit, or cost it. The ranking answers "where", and "what" is a separate decision
this prototype does not make. That separation is deliberate and is stated in the UI.

---

## 2. Integration with legacy PLCs and OT data

### The architectural commitment, which *is* in the code

`src/twin/sources.py` defines `TelemetrySource`: an abstract source of `Snapshot` objects with
exactly two methods, `frames()` and `close()`. **There is no `write` method, and none should ever be
added.** Every analytical layer downstream consumes that interface and nothing else —
`tests/test_source_agnostic.py` runs the full analytics path against a fixture-backed source with
`simpy` never imported, proving the analytics do not depend on the simulator.

That is a genuine, testable property: the twin is read-only by construction, not by policy. It is
also the *entire* extent of what is implemented. There is no OPC-UA client and no historian adapter
in this repository, and `docs/REQUIREMENTS.md` rows A3/B5 say so.

### How a real adapter would attach

A third implementation of the same ABC, alongside the simulator and `ReplaySource`:

1. **Source of record: the historian, not the PLC**, wherever one exists. Historians are built to be
   read by many clients; PLCs are not, and every additional poll competes with scan time. Reading the
   historian removes the operational-risk argument almost entirely.
2. **Where only a PLC exists**, an OPC-UA server fronting it, subscribing rather than polling
   (report-by-exception), with a negotiated publishing interval. The twin's tick is 7.5 sim-seconds;
   it does not need sub-second OT data and should not ask for it.
3. **Network position: read-only, one-way, through the OT/IT boundary.** The adapter belongs on the
   IT side of a DMZ, pulling from a historian replica or through a data diode / unidirectional
   gateway where the plant's security posture requires it. Nothing in the twin needs a path back
   into the OT network, which is what makes that topology available at all.
4. **Tag mapping is per-site configuration**, not code: a station id ↔ tag-path table, plus units and
   scaling. This is the single largest per-site integration cost and it is not glamorous.
5. **Clock skew is a real problem and is not solved here.** Legacy controllers may emit no timestamp
   at all, or an unsynchronised one. Genealogy's transfer-delay realignment assumes a coherent time
   base across stations. A real deployment needs either PTP/NTP discipline on the tags or an
   ingest-time correction, and that correction's error would have to be carried into
   `TaggedValue.staleness_s` rather than hidden.

### What would have to be built

An adapter implementing `frames()`, a tag-mapping config, a store-and-forward buffer for connection
loss, and timestamp normalisation. The architectural seam exists and is tested; the adapter does not.
Describing that as "integration" would be an overclaim, which is why this section says what it says.

---

## 3. Rollout against maintenance windows

The brief's constraint: production pauses for instrumentation only during a small number of scheduled
maintenance windows per year. The design consequence is that **the phases are ordered by how much
line downtime they consume, not by how interesting they are.**

### Phase 1 — Zero windows

Tier 0 only. Stand up the read-only tap against signals that already exist, run the twin in shadow
beside the line, and change nothing physical. Delivers bottleneck detection, defect genealogy over
existing completion logs, and inferred cycle-time trends at dark stations.

The point of Phase 1 is not its feature list. It is that **the predictions can be scored against
real outcomes for a full production period before anyone is asked to trust them** — which is the
brief's own requirement that predictive claims be validated against real outcomes over time, and the
thing this prototype most conspicuously cannot do on synthetic data.

### Phase 2 — One window, tier 1 sensors, placement-ranked

Fit non-invasive tier 1 sensors at the stations the placement ranking scores highest, sized to what
one window can physically absorb. The binding constraint is installation hours per window, not
budget — which is precisely what the Leadership tab's budget slider does *not* currently model. That
slider is a count with no cost or install time behind it; making it a real planning tool means
attaching per-sensor install hours and a window length, and that is not built.

### Phase 3 — Targeted tier 2, only where Phase 1 evidence justifies it

In-line quality instrumentation is the expensive tier, so it goes last and only at stations where
genealogy has repeatedly attributed real defects during Phases 1–2. This inverts the usual order
deliberately: instrument where the evidence points, rather than instrumenting broadly and looking for
signal afterwards.

### Honest gap

None of this is modelled in code. There is no window-length parameter, no install-hour cost per
sensor type, and no scheduler that packs the placement ranking into windows. The placement ranking
answers "which station next"; turning that into "which stations fit in the March window" is a real
piece of work that has not been done, and the "Budget this window" label currently promises more than
it delivers.

---

## 4. Scaling beyond one line

What is genuinely configuration today: station count, zones, cycle-time distributions and CVs, buffer
capacities, the dark-station set, variant mix, condition-drift parameters, breakdown profile, and the
sensor-gap operator weights — all in `scenarios/line30.yaml`.

What is **not**, stated because the README used to imply otherwise: **topology**. The line is a
hardcoded serial chain (`sim/line.py`) and the inference graph is a hardcoded path
(`graph/inference.py`). Parallel stations, rework loops, merges and sub-assembly feeders — the actual
layout variation the brief names — would require code, not a new YAML file. The inference layer's
maths generalises to an arbitrary graph without modification; the *construction* of that graph is
what is hardcoded. That is a bounded, well-understood piece of work, and naming it is more useful
than claiming it is already done.
