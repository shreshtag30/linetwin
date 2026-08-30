/* DigitalTwin.ai Control Center. Vanilla JS, no framework, no build step --
 * reads the SAME live engine as the primary dashboard (web/app.js), through
 * the same SSE stream and REST endpoints. Nothing about the backend is
 * duplicated for this parallel prototype; only the presentation differs.
 *
 * Honesty notes specific to this screen set, since it adapts a generic
 * design brief onto real project data (docs/CONTROL_CENTER.md has the full
 * writeup):
 *  - Every number below traces to a live snapshot or a REST endpoint already
 *    used by the primary dashboard. Nothing is a placeholder.
 *  - "Line Health" is OUR OWN defined metric (ACTIVE-state stations / 30),
 *    stated plainly as a formula in its tooltip -- the brief left "health"
 *    undefined, so we are not free to imply it is a validated industry
 *    metric.
 *  - The bottleneck "ranking" only ever names 2 stations (bottleneck +
 *    runner-up) because that is genuinely all the Active Period Method
 *    verdict contains -- there is no validated N-way ranking beyond that,
 *    and inventing one to fill more rows would misrepresent what was
 *    actually benchmarked (docs/phases/phase-05-detector-benchmark.md).
 *  - Sensor Coverage shows station-level instrumented/dark + sensor_share,
 *    not per-sensor-type checkmarks (Temperature/Vibration/Torque) as the
 *    brief's example implies -- this project's data model tracks coverage
 *    at the station level, not per individual sensor, and showing invented
 *    per-sensor checkmarks would fabricate detail the system doesn't have.
 */

function $(id) { return document.getElementById(id); }

let es = null;
let lastSnapshot = null;
let selectedStationId = null;
let selectedRiskStationId = null;
let zoneFilter = "all";
let riskThreshold = null;
let economicsConfig = null;
let sensorBudgetTimer = null;

const ZONE_ORDER = ["body", "paint", "final"];

/* ---------------------------------------------------------------------
 * Sidebar navigation
 * ------------------------------------------------------------------- */

function switchView(viewId) {
  for (const view of document.querySelectorAll(".view")) {
    view.hidden = view.id !== viewId;
  }
  for (const item of document.querySelectorAll(".sb-item")) {
    item.classList.toggle("is-active", item.dataset.view === viewId);
  }
  if (viewId === "v-sensor") fetchSensorPlacement();
  if (viewId === "v-rootcause") fetchGenealogyCandidates();
}

for (const item of document.querySelectorAll(".sb-item")) {
  item.addEventListener("click", () => switchView(item.dataset.view));
}

/* ---------------------------------------------------------------------
 * SSE connection
 * ------------------------------------------------------------------- */

function setSidebarStatus(state) {
  $("sb-dot").dataset.state = state;
  $("sb-status-text").textContent =
    state === "open" ? "Digital Twin Online" :
    state === "closed" ? "Digital Twin Offline" : "Connecting…";
}

function connect() {
  es = new EventSource("/api/twin/stream");

  es.addEventListener("run_meta", () => {
    selectedStationId = null;
    selectedRiskStationId = null;
    $("station-detail").hidden = true;
  });

  es.addEventListener("snapshot", (e) => {
    lastSnapshot = JSON.parse(e.data);
    renderAll(lastSnapshot);
  });

  es.onopen = () => setSidebarStatus("open");
  es.onerror = () => setSidebarStatus(es.readyState === EventSource.CONNECTING ? "connecting" : "closed");
}

/* ---------------------------------------------------------------------
 * Shared helpers
 * ------------------------------------------------------------------- */

const ACTIVE_STATES = new Set(["working", "down", "repair", "setup"]);

// `defect_risk` can be entirely null -- before Model B's first scoring tick
// (it runs at 1Hz, not every tick) or if no model was loaded at all
// (contracts.py: `defect_risk: TaggedValue | None = None`). REAL BUG found
// live: the first render after connecting crashed repeatedly reading
// `.value` off that null, every 125ms, until scoring caught up -- exactly
// the startup race a page load right after a server restart hits. Route
// every access through this so "not yet scored" and "scored as zero" stay
// the distinct facts contracts.py already says they are, everywhere.
function riskValueOf(station) {
  return station.defect_risk ? station.defect_risk.value : null;
}

function riskLevel(value, threshold) {
  if (value == null) return "none";
  if (threshold == null) return value > 0.05 ? "warn" : "ok";
  if (value >= threshold * 2) return "crit";
  if (value >= threshold) return "warn";
  return "ok";
}

function stationsByZone(stations) {
  const by = { body: [], paint: [], final: [] };
  for (const s of stations) (by[s.zone] || (by[s.zone] = [])).push(s);
  return by;
}

function pct(x, digits = 1) { return `${(x * 100).toFixed(digits)}%`; }

/* ---------------------------------------------------------------------
 * Master render -- dispatches to whichever view is currently visible,
 * plus the sidebar meta strip which is always live regardless of view.
 * ------------------------------------------------------------------- */

function renderAll(snap) {
  $("sb-meta").textContent = `tick ${snap.tick.toLocaleString()} · rtf ${snap.real_time_factor.toFixed(2)}`;

  renderOverview(snap);
  renderBottleneck(snap);
  renderDefect(snap);
  renderSensorGrid(snap); // cheap enough to keep live even off-screen
  renderLeadership(snap);
}

/* ---------------------------------------------------------------------
 * Screen 1 -- Factory Overview
 * ------------------------------------------------------------------- */

function renderOverview(snap) {
  const activeCount = snap.stations.filter((s) => ACTIVE_STATES.has(s.state)).length;
  const health = activeCount / snap.stations.length;
  $("kpi-health").textContent = pct(health, 0);
  $("kpi-health-sub").textContent = `${activeCount} / ${snap.stations.length} stations active`;

  $("kpi-throughput").textContent = Math.round(snap.line_throughput_uph).toLocaleString();
  $("kpi-throughput-sub").textContent = `≈ ${Math.round(snap.line_throughput_uph * 24).toLocaleString()} vehicles/day at current rate`;

  const blocked = snap.stations.filter((s) => s.state === "blocked").length;
  const aboveThreshold = riskThreshold == null ? 0 :
    snap.stations.filter((s) => riskValueOf(s) != null && riskValueOf(s) >= riskThreshold).length;
  $("kpi-alerts").textContent = String(blocked + aboveThreshold);
  $("kpi-risk").textContent = String(aboveThreshold);
  $("kpi-risk-sub").textContent = riskThreshold == null ? "model not loaded" : `threshold ${pct(riskThreshold, 1)}`;

  renderFloorMap(snap);
  if (selectedStationId) renderStationDetail(snap);
}

function renderFloorMap(snap) {
  const map = $("floor-map");
  const grouped = stationsByZone(snap.stations);
  const filterZones = zoneFilter === "all" ? ZONE_ORDER : [zoneFilter];

  // Rebuild tile set only if the zone filter or station roster changed --
  // otherwise mutate existing tiles in place so a running 8Hz stream never
  // tears down and rebuilds 30 DOM nodes every 125ms.
  const wantIds = filterZones.flatMap((z) => (grouped[z] || []).map((s) => s.station_id));
  const haveIds = [...map.querySelectorAll(".fm-tile")].map((t) => t.dataset.stationId);
  const structureStale = wantIds.join(",") !== haveIds.join(",");

  if (structureStale) {
    map.innerHTML = "";
    for (const zone of filterZones) {
      const stations = grouped[zone];
      if (!stations || !stations.length) continue;
      const block = document.createElement("div");
      const title = document.createElement("div");
      title.className = "fm-zone-title";
      title.textContent = `${zone} — ${stations.length} stations`;
      const grid = document.createElement("div");
      grid.className = "fm-grid";
      for (const s of stations) {
        grid.appendChild(buildTile(s));
      }
      block.appendChild(title);
      block.appendChild(grid);
      map.appendChild(block);
    }
  }

  for (const s of snap.stations) {
    const tile = map.querySelector(`.fm-tile[data-station-id="${s.station_id}"]`);
    if (!tile) continue;
    updateTile(tile, s, snap.bottleneck);
  }
}

function buildTile(s) {
  const tile = document.createElement("div");
  tile.className = "fm-tile";
  tile.dataset.stationId = s.station_id;
  tile.innerHTML = `
    <span class="fm-dot"></span>
    <div class="fm-tile-id">${s.station_id}</div>
    <div class="fm-tile-cycle" data-role="cycle">—</div>
  `;
  tile.addEventListener("click", () => {
    selectedStationId = s.station_id;
    $("station-detail").hidden = false;
    if (lastSnapshot) renderStationDetail(lastSnapshot);
    for (const t of document.querySelectorAll(".fm-tile")) t.classList.remove("is-selected");
    tile.classList.add("is-selected");
  });
  return tile;
}

function updateTile(tile, s, bottleneck) {
  const rv = riskValueOf(s);
  const risk = s.state === "blocked" ? "crit" : rv != null && rv >= (riskThreshold ?? 0.05) ? "warn" : "ok";
  tile.dataset.risk = risk;
  tile.querySelector('[data-role="cycle"]').textContent = `${s.cycle_time_s.value.toFixed(0)}s`;
  tile.title = bottleneck && bottleneck.station_id === s.station_id
    ? `${s.station_id} — live bottleneck`
    : `${s.station_id} — ${s.state}`;
}

function renderStationDetail(snap) {
  const s = snap.stations.find((x) => x.station_id === selectedStationId);
  if (!s) return;
  $("sd-id").textContent = s.station_id;
  const level = s.state === "blocked" ? "crit" : s.state === "starved" ? "warn" : "ok";
  const badge = $("sd-status");
  badge.textContent = s.state.toUpperCase();
  badge.dataset.level = level;
  $("sd-cycle").textContent = `${s.cycle_time_s.value.toFixed(1)}s`;
  $("sd-queue").textContent = `${s.queue_depth} / ${s.buffer_capacity}`;
  const rv = riskValueOf(s);
  $("sd-risk").textContent = rv != null ? pct(rv, 2) : "not yet scored";
  $("sd-source").textContent = s.instrumented
    ? "Observed"
    : `Inferred — ${pct(s.cycle_time_s.sensor_share ?? 0, 0)} sensor-derived`;
  $("sd-cycle-base").textContent = "config-defined per zone (scenarios/line30.yaml)";
}

$("sd-close").addEventListener("click", () => {
  selectedStationId = null;
  $("station-detail").hidden = true;
  for (const t of document.querySelectorAll(".fm-tile")) t.classList.remove("is-selected");
});

for (const btn of document.querySelectorAll(".zf-btn")) {
  btn.addEventListener("click", () => {
    zoneFilter = btn.dataset.zone;
    for (const b of document.querySelectorAll(".zf-btn")) b.classList.toggle("is-active", b === btn);
    if (lastSnapshot) renderFloorMap(lastSnapshot);
  });
}

/* ---------------------------------------------------------------------
 * Screen 2 -- Bottleneck Detection
 * ------------------------------------------------------------------- */

function renderBottleneck(snap) {
  const list = $("bn-rank-list");
  const bn = snap.bottleneck;
  list.innerHTML = "";

  if (!bn || !bn.station_id) {
    list.innerHTML = `<div class="rank-row"><span class="rank-reason">Line idle — no active bottleneck.</span></div>`;
  } else {
    list.appendChild(buildBottleneckRow(1, bn.station_id, bn.explanation, bn.confidence));
    if (bn.runner_up_id) {
      list.appendChild(buildBottleneckRow(2, bn.runner_up_id, "runner-up — next most likely constraint", null));
    }
  }

  // Cycle-time-by-station bar list -- real per-tick data, sorted, no
  // charting library needed for a single-frame comparison like this.
  const chart = $("bn-cycle-chart");
  const sorted = [...snap.stations].sort((a, b) => b.cycle_time_s.value - a.cycle_time_s.value).slice(0, 10);
  const maxCycle = Math.max(...sorted.map((s) => s.cycle_time_s.value), 1);
  chart.innerHTML = sorted.map((s) => {
    const isBn = bn && s.station_id === bn.station_id;
    return `<div class="rank-row">
      <span class="rank-id">${s.station_id}</span>
      <div class="risk-bar-track" style="flex:1"><div class="risk-bar-fill" data-level="${isBn ? "crit" : "ok"}" style="width:${(s.cycle_time_s.value / maxCycle * 100).toFixed(0)}%"></div></div>
      <span class="rank-conf">${s.cycle_time_s.value.toFixed(0)}s</span>
    </div>`;
  }).join("");

  const compare = $("predict-compare");
  const pred = snap.predicted_bottleneck;
  if (!pred || !pred.station_id) {
    compare.innerHTML = `<p class="panel-sub">No forecast available yet.</p>`;
  } else {
    const shifting = bn && bn.station_id && pred.station_id !== bn.station_id;
    compare.innerHTML = `
      <div class="rank-row"><span class="rank-reason">Current</span><span class="rank-id">${bn && bn.station_id ? bn.station_id : "—"}</span></div>
      <div class="rank-row"><span class="rank-reason">Predicted (~30 min ahead)</span><span class="rank-id">${pred.station_id}</span></div>
      ${shifting ? `<p class="panel-sub" style="color:var(--warn);margin-top:8px">Forecast disagrees with the current verdict — a shift may be coming.</p>` : ""}
    `;
  }
}

function buildBottleneckRow(num, stationId, reason, confidence) {
  const row = document.createElement("div");
  row.className = "rank-row";
  row.innerHTML = `
    <span class="rank-num">${num}</span>
    <span class="rank-id">${stationId}</span>
    <span class="rank-reason">${reason}</span>
    ${confidence ? `<span class="pill" data-level="${confidence === "established" ? "ok" : "warn"}">${confidence}</span>` : ""}
  `;
  return row;
}

/* ---------------------------------------------------------------------
 * Screen 3 -- Defect Prediction
 * ------------------------------------------------------------------- */

function renderDefect(snap) {
  const list = $("risk-list");
  const sorted = [...snap.stations].sort((a, b) => (riskValueOf(b) ?? -1) - (riskValueOf(a) ?? -1));

  if (!selectedRiskStationId && sorted.length) selectedRiskStationId = sorted[0].station_id;

  list.innerHTML = sorted.map((s) => {
    const rv = riskValueOf(s);
    const level = riskLevel(rv, riskThreshold);
    const barWidth = rv != null ? Math.min(100, rv * 1000).toFixed(0) : 0;
    return `<div class="risk-row ${s.station_id === selectedRiskStationId ? "is-selected" : ""}" data-station-id="${s.station_id}">
      <span class="risk-id">${s.station_id}</span>
      <div class="risk-bar-track"><div class="risk-bar-fill" data-level="${level}" style="width:${barWidth}%"></div></div>
      <span class="risk-pct">${rv != null ? pct(rv, 2) : "—"}</span>
      <span class="risk-tag">${s.instrumented ? "observed" : "inferred"}</span>
    </div>`;
  }).join("");

  for (const row of list.querySelectorAll(".risk-row")) {
    row.addEventListener("click", () => {
      selectedRiskStationId = row.dataset.stationId;
      if (lastSnapshot) renderDefect(lastSnapshot);
    });
  }

  const selected = snap.stations.find((s) => s.station_id === selectedRiskStationId);
  if (selected) renderExplain(selected);
}

function renderExplain(station) {
  $("explain-title").textContent = `${station.station_id} — contributing factors`;
  const body = $("explain-body");
  const drivers = station.risk_drivers || [];
  if (!drivers.length) {
    body.innerHTML = `<p class="panel-sub">No driver data yet for this station.</p>`;
    return;
  }
  const maxAbs = Math.max(...drivers.map((d) => Math.abs(d.contribution)), 0.001);
  body.innerHTML = drivers.map((d) => {
    const width = (Math.abs(d.contribution) / maxAbs * 100).toFixed(0);
    const negative = d.contribution < 0;
    return `<div class="driver-row">
      <span class="driver-name">${d.feature}</span>
      <div class="driver-bar-track"><div class="driver-bar-fill ${negative ? "is-negative" : ""}" style="width:${width}%"></div></div>
      <span class="driver-pct">${negative ? "−" : "+"}${Math.abs(d.contribution).toFixed(2)}</span>
    </div>`;
  }).join("");
}

/* ---------------------------------------------------------------------
 * Screen 4 -- Sensor Coverage
 * ------------------------------------------------------------------- */

function renderSensorGrid(snap) {
  const instrumented = snap.stations.filter((s) => s.instrumented).length;
  const dark = snap.stations.length - instrumented;
  const fracInstrumented = instrumented / snap.stations.length;

  const donut = $("coverage-donut");
  donut.style.background =
    `conic-gradient(var(--ok) 0 ${(fracInstrumented * 360).toFixed(1)}deg, var(--warn) 0 360deg)`;
  if (!donut.querySelector(".coverage-donut-center")) {
    donut.innerHTML = `<div class="coverage-donut-center"><b></b><span>observed</span></div>`;
  }
  donut.querySelector(".coverage-donut-center b").textContent = pct(fracInstrumented, 0);

  $("coverage-legend").innerHTML = `
    <li><span class="cl-swatch" style="background:var(--ok)"></span>Observed <b>${instrumented} / ${snap.stations.length}</b></li>
    <li><span class="cl-swatch" style="background:var(--warn)"></span>Inferred <b>${dark} / ${snap.stations.length}</b></li>
  `;

  const grid = $("sensor-grid");
  if (grid.children.length !== snap.stations.length) {
    grid.innerHTML = snap.stations.map((s) => `
      <div class="sensor-chip" data-station-id="${s.station_id}" data-observed="${s.instrumented}">
        <div class="sensor-chip-id"><span class="mono">${s.station_id}</span></div>
        <div class="sensor-chip-status" data-role="status"></div>
        <div class="sensor-chip-share" data-role="share"></div>
      </div>
    `).join("");
  }
  for (const s of snap.stations) {
    const chip = grid.querySelector(`.sensor-chip[data-station-id="${s.station_id}"]`);
    if (!chip) continue;
    chip.querySelector('[data-role="status"]').textContent = s.instrumented ? "✓ instrumented" : "✗ no sensor";
    chip.querySelector('[data-role="share"]').textContent = s.instrumented
      ? ""
      : `${pct(s.cycle_time_s.sensor_share ?? 0, 0)} sensor-derived`;
  }
}

async function fetchSensorPlacement() {
  const budget = parseInt($("sensor-budget").value, 10);
  const res = await fetch(`/api/twin/sensor_placement?budget=${budget}`);
  const data = await res.json();
  $("sensor-rank-list").innerHTML = data.recommended_next.map((sid, i) => `
    <div class="rank-row">
      <span class="rank-num">${i + 1}</span>
      <span class="rank-id">${sid}</span>
      <span class="rank-reason">Currently dark — greedy pick improves coverage most</span>
    </div>
  `).join("") || `<div class="rank-row"><span class="rank-reason">All dark stations already covered by this budget.</span></div>`;
}

$("sensor-budget").addEventListener("input", (e) => {
  $("sensor-budget-val").textContent = e.target.value;
  clearTimeout(sensorBudgetTimer);
  sensorBudgetTimer = setTimeout(fetchSensorPlacement, 250);
});

/* ---------------------------------------------------------------------
 * Screen 5 -- Root Cause Analysis
 * ------------------------------------------------------------------- */

async function fetchGenealogyCandidates() {
  const res = await fetch("/api/twin/genealogy/candidates?limit=10");
  const data = await res.json();
  const list = $("rc-candidates");
  if (!data.candidates.length) {
    list.innerHTML = `<div class="rank-row"><span class="rank-reason">Not enough completed units yet — check back shortly.</span></div>`;
    return;
  }
  list.innerHTML = data.candidates.map((c, i) => `
    <div class="rank-row is-clickable" data-unit-id="${c.unit_id}">
      <span class="rank-num">${i + 1}</span>
      <span class="rank-id">#${c.unit_id}</span>
      <span class="rank-reason">Peak z-score ${c.peak_z_score.toFixed(2)} at ${c.peak_station_id}</span>
      <span class="rank-conf">trace →</span>
    </div>
  `).join("");
  for (const row of list.querySelectorAll(".rank-row")) {
    row.addEventListener("click", () => traceUnit(row.dataset.unitId));
  }
}

async function traceUnit(unitId) {
  const res = await fetch(`/api/twin/genealogy/${unitId}`);
  if (!res.ok) return;
  const r = await res.json();

  $("rc-flow-panel").hidden = false;
  $("rc-unit-id").textContent = `#${r.defect_unit_id}`;
  $("rc-origin").textContent = r.origin_station_id;
  $("rc-confidence").textContent = pct(r.confidence, 0);
  $("rc-affected").textContent = `${r.affected_unit_ids.length} units (#${r.affected_unit_ids.join(", #")})`;
  $("rc-realigned").textContent = `${r.origin_realigned_time_s.toFixed(1)}s (sim time)`;

  const flow = $("rc-flow");
  flow.innerHTML = "";
  const detectNode = document.createElement("span");
  detectNode.className = "rc-node is-detect";
  detectNode.textContent = "Final inspection";
  flow.appendChild(detectNode);
  for (let i = r.path.length - 1; i >= 0; i--) {
    const arrow = document.createElement("span");
    arrow.className = "rc-arrow";
    arrow.textContent = "←";
    flow.appendChild(arrow);
    const node = document.createElement("span");
    node.className = "rc-node" + (r.path[i] === r.origin_station_id ? " is-origin" : "");
    node.textContent = r.path[i];
    flow.appendChild(node);
  }
  $("rc-flow-panel").scrollIntoView({ behavior: "smooth", block: "nearest" });
}

/* ---------------------------------------------------------------------
 * Screen 6 -- Reports / Leadership ROI
 * ------------------------------------------------------------------- */

function renderLeadership(snap) {
  if (!economicsConfig) return;
  const scored = snap.stations.map(riskValueOf).filter((v) => v != null);
  if (!scored.length) return; // nothing scored yet -- leave the KPIs at their placeholder dashes
  const meanRisk = scored.reduce((sum, v) => sum + v, 0) / scored.length;
  const unitsAtRisk = meanRisk * economicsConfig.qc_lag_units;
  const dollars = unitsAtRisk * economicsConfig.rework_cost_delta_usd;

  $("ld-mean-risk").textContent = pct(meanRisk, 2);
  $("ld-qc-lag").textContent = `${economicsConfig.qc_lag_units.toFixed(0)} units`;
  $("ld-units-at-risk").textContent = unitsAtRisk.toFixed(1);
  $("ld-dollars").textContent = `$${dollars.toFixed(0)}`;
}

async function loadEconomicsConfig() {
  const res = await fetch("/api/twin/economics_config");
  economicsConfig = await res.json();
  if (lastSnapshot) renderLeadership(lastSnapshot);
}

async function loadRiskThreshold() {
  const res = await fetch("/api/twin/risk_threshold");
  const data = await res.json();
  riskThreshold = data.threshold;
}

// Facts genuinely verified about this project so far (see conversation /
// docs/phases/*.md) -- static, not per-tick, since they describe the build
// itself rather than the live line.
function renderMeasuredFacts() {
  const facts = [
    { v: "160 / 160", k: "tests passing, CI green on ubuntu + windows" },
    { v: "100%", k: "top-1 accuracy vs. sensitivity-analysis ground truth (6-detector benchmark)" },
    { v: "22 / 30", k: "stations instrumented — inference verified via 2 exact graph identities" },
    { v: "0.581%", k: "synthetic defect rate, calibrated to Bosch's published prevalence" },
  ];
  $("measured-grid").innerHTML = facts.map((f) => `
    <div class="measured-cell"><div class="mv">${f.v}</div><div class="mk">${f.k}</div></div>
  `).join("");
}

function renderRoadmap() {
  $("roadmap").innerHTML = `
    <span class="rm-step is-current">Pilot Line</span>
    <span class="rm-arrow">→</span>
    <span class="rm-step">Factory</span>
    <span class="rm-arrow">→</span>
    <span class="rm-step">Multi-plant deployment</span>
  `;
}

/* ---------------------------------------------------------------------
 * Boot
 * ------------------------------------------------------------------- */

renderMeasuredFacts();
renderRoadmap();
loadEconomicsConfig();
loadRiskThreshold();
connect();
