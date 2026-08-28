/* LineTwin floor-supervisor dashboard. Vanilla JS, no framework, no build
 * step -- one EventSource against /api/twin/stream, DOM updates driven
 * directly off each parsed snapshot. Vendored uPlot (web/vendor/uplot) for
 * the two trend charts.
 */

const ZONE_ORDER = ["body", "paint", "final"];
const HISTORY_LEN = 120; // ~15s of ticks at 8Hz -- enough to see a trend, not a full run
const PM_HISTORY_LEN = 600; // longer window for the plant-manager view -- a shift-length trend, not a live tick
const PM_TIMELINE_MAX = 50;

let es = null;
let killed = false;
let stationOrder = [];
let throughputChart = null;
let wipChart = null;
let pmThroughputChart = null;
let pmWipChart = null;
const history = { ticks: [], throughput: [], wip: [] };
const pmHistory = { ticks: [], throughput: [], wip: [] };

// Plant-manager rolling state -- accumulated client-side from the same
// stream the floor-supervisor view reads, per this project's "one
// EventSource, no framework" discipline (app.js's own module docstring).
// Resets on run_meta (a restart is a new session, not a continuation).
let pmBottleneckCounts = {}; // station_id -> ticks observed as the live bottleneck
let pmObservedTicks = 0;
let pmLastBottleneckId = undefined; // undefined = not yet initialized this session
const pmTimeline = []; // [{tick, from, to}], newest first

// Leadership view state.
let economicsConfig = null; // {qc_lag_units, rework_cost_delta_usd}, fetched once
let ldBudgetFetchTimer = null;

function $(id) { return document.getElementById(id); }

function setLamp(state) {
  $("lamp").dataset.state = state;
  $("v-readystate").textContent = state;
}

function connect() {
  es = new EventSource("/api/twin/stream");

  es.addEventListener("run_meta", (e) => {
    const meta = JSON.parse(e.data);
    $("run-meta-footer").textContent =
      `run ${meta.run_id.slice(0, 8)} · seed ${meta.seed} · ${meta.station_count} stations (${meta.instrumented_count} instrumented)`;
    // A restart resets the engine's own counters, so the client's rolling
    // history must reset too, or the chart would show a false discontinuity
    // as a "drop" rather than a fresh run.
    history.ticks = [];
    history.throughput = [];
    history.wip = [];
    pmHistory.ticks = [];
    pmHistory.throughput = [];
    pmHistory.wip = [];
    pmBottleneckCounts = {};
    pmObservedTicks = 0;
    pmLastBottleneckId = undefined;
    pmTimeline.length = 0;
    renderFrequency();
    renderTimeline();
  });

  es.addEventListener("snapshot", (e) => {
    if (killed) return; // a stale in-flight event arriving after Kill must not un-freeze the UI
    const snap = JSON.parse(e.data);
    renderSnapshot(snap);
  });

  es.onopen = () => setLamp("open");
  es.onerror = () => {
    // EventSource auto-reconnects on transient network errors; readyState
    // reflects CONNECTING (0) while it retries, CLOSED (2) only after we
    // explicitly close it ourselves (Kill).
    setLamp(es.readyState === EventSource.CLOSED ? "closed" : "connecting");
  };
}

function renderSnapshot(snap) {
  $("v-tick").textContent = snap.tick;
  $("v-seq").textContent = snap.seq;
  $("v-simtime").textContent = snap.sim_time_s.toFixed(1);
  $("v-rtf").textContent = snap.real_time_factor.toFixed(3);
  $("v-lag").textContent = Math.round(snap.lag_s * 1000);

  renderBottleneck(snap.bottleneck);
  renderStations(snap.stations, snap.bottleneck);
  pushHistory(snap);
  drawCharts();

  updatePlantManager(snap);
  updateLeadership(snap);

  if (stationOrder.length === 0) {
    stationOrder = snap.stations.map((s) => s.station_id);
    populateStationSelect(stationOrder);
  }
}

function renderBottleneck(bn) {
  if (!bn || !bn.station_id) {
    $("bn-station").textContent = "–";
    $("bn-confidence").textContent = "none";
    $("bn-confidence").dataset.level = "none";
    $("bn-runnerup").textContent = "–";
    $("bn-explanation").textContent = "No active bottleneck this tick.";
    $("bn-mode").innerHTML = "";
    return;
  }
  $("bn-station").textContent = bn.station_id;
  $("bn-confidence").textContent = bn.confidence;
  $("bn-confidence").dataset.level = bn.confidence;
  $("bn-runnerup").textContent = bn.runner_up_id || "–";
  $("bn-explanation").textContent = bn.explanation || "";

  const modeEl = $("bn-mode");
  modeEl.innerHTML = "";
  for (const [state, frac] of Object.entries(bn.mode_decomposition || {})) {
    if (frac <= 0) continue;
    const span = document.createElement("span");
    span.textContent = `${state} ${Math.round(frac * 100)}%`;
    modeEl.appendChild(span);
  }
}

function renderStations(stations, bottleneck) {
  const byZone = {};
  for (const st of stations) {
    (byZone[st.zone] ||= []).push(st);
  }

  const zonesEl = $("zones");
  zonesEl.innerHTML = "";
  const bottleneckId = bottleneck ? bottleneck.station_id : null;

  for (const zone of ZONE_ORDER) {
    const stations = byZone[zone];
    if (!stations) continue;

    const block = document.createElement("div");
    block.className = "zone-block";

    const title = document.createElement("div");
    title.className = "zone-title";
    title.textContent = `${zone} (${stations.length})`;
    block.appendChild(title);

    const grid = document.createElement("div");
    grid.className = "station-grid";

    for (const st of stations) {
      grid.appendChild(renderStationCard(st, st.station_id === bottleneckId));
    }
    block.appendChild(grid);
    zonesEl.appendChild(block);
  }
}

function renderStationCard(st, isBottleneck) {
  const card = document.createElement("div");
  card.className = "station-card" + (isBottleneck ? " is-bottleneck" : "");
  card.dataset.state = st.state;

  const ct = st.cycle_time_s;
  const ctText = ct.value !== null ? `${ct.value.toFixed(1)}s` : "—";
  // `missingness` is authoritative when the value isn't present -- REAL BUG,
  // caught by looking at the live dashboard (Phase 7): rendering `ct.source`
  // unconditionally showed a green "observed" pill on S07 (a genuinely dark
  // station with value=null, missingness="missing"), which is exactly the
  // failure mode -- presenting an estimate/absence as a measurement -- this
  // project's provenance tagging exists to prevent. `source` is only a
  // meaningful thing to show once `missingness === "present"`.
  let tagLabel;
  if (ct.missingness !== "present") {
    tagLabel = ct.missingness;
  } else if (ct.source === "inferred" && ct.sensor_share !== null) {
    // The committed pill copy (docs/DECISIONS.md): the exact evidence-
    // attribution percentage from the harmonic extension's partition of
    // unity, not a decorative label -- zero tuning parameters behind it.
    tagLabel = `inferred — ${Math.round(ct.sensor_share * 100)}%`;
  } else {
    tagLabel = ct.source;
  }
  const sourceTag = `<span class="confidence-pill" data-source="${ct.source}">${tagLabel}</span>`;

  card.innerHTML = `
    <div class="st-id">${st.station_id}</div>
    <div class="st-state">${st.state}</div>
    <div class="st-metric"><span>queue</span><span>${st.queue_depth}/${st.buffer_capacity}</span></div>
    <div class="st-metric"><span>cycle</span><span>${ctText}${sourceTag}</span></div>
  `;
  return card;
}

function pushHistory(snap) {
  history.ticks.push(snap.tick);
  history.throughput.push(snap.line_throughput_uph);
  history.wip.push(snap.wip);
  if (history.ticks.length > HISTORY_LEN) {
    history.ticks.shift();
    history.throughput.shift();
    history.wip.shift();
  }
}

function drawCharts() {
  const throughputData = [history.ticks, history.throughput];
  const wipData = [history.ticks, history.wip];

  if (!throughputChart) {
    throughputChart = new uPlot(
      { width: 380, height: 160, series: [{}, { stroke: "#A100FF", width: 2 }], scales: { x: { time: false } } },
      throughputData,
      $("chart-throughput")
    );
  } else {
    throughputChart.setData(throughputData);
  }

  if (!wipChart) {
    wipChart = new uPlot(
      { width: 380, height: 160, series: [{}, { stroke: "#E8590C", width: 2 }], scales: { x: { time: false } } },
      wipData,
      $("chart-wip")
    );
  } else {
    wipChart.setData(wipData);
  }
}

function populateStationSelect(ids) {
  const sel = $("ctl-station");
  sel.innerHTML = "";
  for (const id of ids) {
    const opt = document.createElement("option");
    opt.value = id;
    opt.textContent = id;
    sel.appendChild(opt);
  }
}

async function applyControl() {
  const station_id = $("ctl-station").value;
  const cycle_time_multiplier = parseFloat($("ctl-mult").value);
  const ackEl = $("ctl-ack");
  ackEl.dataset.status = "";
  ackEl.textContent = "Applying…";
  try {
    const resp = await fetch("/api/twin/control", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ station_id, cycle_time_multiplier }),
    });
    const body = await resp.json();
    if (resp.ok) {
      ackEl.dataset.status = "ok";
      ackEl.textContent = `Applied ${cycle_time_multiplier}× to ${station_id} at tick ${body.applied_at_tick}.`;
    } else {
      ackEl.dataset.status = "err";
      ackEl.textContent = `Rejected (${resp.status}): ${body.detail || "unknown error"}`;
    }
  } catch (err) {
    ackEl.dataset.status = "err";
    ackEl.textContent = `Request failed: ${err}`;
  }
}

function killStream() {
  killed = true;
  if (es) es.close();
  setLamp("closed");
  document.body.classList.add("stream-killed");
  $("btn-kill").disabled = true;
  $("btn-resume").disabled = false;
}

function resumeStream() {
  killed = false;
  document.body.classList.remove("stream-killed");
  $("btn-kill").disabled = false;
  $("btn-resume").disabled = true;
  connect();
}

/* ---------------------------------------------------------------------
 * Plant-manager view: rolling-window aggregation of the SAME snapshot
 * stream the floor-supervisor view reads -- no second connection, no
 * server-side history buffer (contracts.py's Snapshot is deliberately
 * stateless per tick; the rolling window is a client concern).
 * ------------------------------------------------------------------- */

function updatePlantManager(snap) {
  const bn = snap.bottleneck;
  const currentId = bn && bn.station_id ? bn.station_id : null;

  if (currentId) {
    pmBottleneckCounts[currentId] = (pmBottleneckCounts[currentId] || 0) + 1;
    pmObservedTicks += 1;
  }

  if (pmLastBottleneckId === undefined) {
    pmLastBottleneckId = currentId; // session start -- not a shift, just the initial state
  } else if (currentId !== pmLastBottleneckId) {
    pmTimeline.unshift({ tick: snap.tick, from: pmLastBottleneckId || "–", to: currentId || "–" });
    if (pmTimeline.length > PM_TIMELINE_MAX) pmTimeline.length = PM_TIMELINE_MAX;
    pmLastBottleneckId = currentId;
  }

  renderFrequency();
  renderTimeline();
  renderPredicted(snap.predicted_bottleneck, bn);
  pushPmHistory(snap);
  drawPmCharts();
}

function renderFrequency() {
  const el = $("pm-frequency");
  const entries = Object.entries(pmBottleneckCounts).sort((a, b) => b[1] - a[1]);
  if (entries.length === 0 || pmObservedTicks === 0) {
    el.innerHTML = '<p class="panel-sub">No bottleneck observed yet this session.</p>';
    return;
  }
  el.innerHTML = "";
  for (const [sid, count] of entries.slice(0, 12)) {
    const pct = (count / pmObservedTicks) * 100;
    const row = document.createElement("div");
    row.className = "freq-row";
    row.innerHTML = `
      <span class="freq-id">${sid}</span>
      <span class="freq-bar-track"><span class="freq-bar-fill" style="width:${pct.toFixed(1)}%"></span></span>
      <span class="freq-pct">${pct.toFixed(1)}%</span>
    `;
    el.appendChild(row);
  }
}

function renderPredicted(predicted, current) {
  const stationEl = $("pm-predicted-station");
  const explEl = $("pm-predicted-explanation");
  const noteEl = $("pm-shift-note");

  if (!predicted || !predicted.station_id) {
    stationEl.textContent = "–";
    explEl.textContent = "No forecast available yet — the first forecast completes shortly after startup (background task, ~1x/s).";
    noteEl.textContent = "";
    return;
  }

  stationEl.textContent = predicted.station_id;
  explEl.textContent = predicted.explanation || "";

  const currentId = current && current.station_id ? current.station_id : null;
  if (predicted.station_id !== currentId) {
    noteEl.textContent = `Forecast (next ~30 sim-min): bottleneck may shift from ${currentId || "none"} to ${predicted.station_id}.`;
  } else {
    noteEl.textContent = `Forecast agrees with the current live bottleneck — no shift predicted.`;
  }
}

function renderTimeline() {
  const el = $("pm-timeline");
  if (pmTimeline.length === 0) {
    el.innerHTML = '<p class="panel-sub">No bottleneck change observed yet this session.</p>';
    return;
  }
  el.innerHTML = "";
  for (const entry of pmTimeline) {
    const row = document.createElement("div");
    row.className = "timeline-row";
    row.innerHTML = `<span class="t-tick">tick ${entry.tick}</span><span>${entry.from} → ${entry.to}</span>`;
    el.appendChild(row);
  }
}

function pushPmHistory(snap) {
  pmHistory.ticks.push(snap.tick);
  pmHistory.throughput.push(snap.line_throughput_uph);
  pmHistory.wip.push(snap.wip);
  if (pmHistory.ticks.length > PM_HISTORY_LEN) {
    pmHistory.ticks.shift();
    pmHistory.throughput.shift();
    pmHistory.wip.shift();
  }
}

function drawPmCharts() {
  const throughputData = [pmHistory.ticks, pmHistory.throughput];
  const wipData = [pmHistory.ticks, pmHistory.wip];

  if (!pmThroughputChart) {
    pmThroughputChart = new uPlot(
      { width: 380, height: 160, series: [{}, { stroke: "#A100FF", width: 2 }], scales: { x: { time: false } } },
      throughputData,
      $("pm-chart-throughput")
    );
  } else {
    pmThroughputChart.setData(throughputData);
  }

  if (!pmWipChart) {
    pmWipChart = new uPlot(
      { width: 380, height: 160, series: [{}, { stroke: "#E8590C", width: 2 }], scales: { x: { time: false } } },
      wipData,
      $("pm-chart-wip")
    );
  } else {
    pmWipChart.setData(wipData);
  }
}

/* ---------------------------------------------------------------------
 * Leadership view: ROI estimate (src/twin/economics.py, fetched once
 * since its constants are static per-process) + Phase 9's sensor
 * placement ranking (fetched on demand -- it depends on the current
 * budget and each dark station's live sensor_share).
 * ------------------------------------------------------------------- */

async function loadEconomicsConfig() {
  try {
    const resp = await fetch("/api/twin/economics_config");
    economicsConfig = await resp.json();
    $("ld-qc-lag").textContent = economicsConfig.qc_lag_units.toFixed(0);
  } catch {
    // Leadership view degrades to "–" placeholders; the rest of the app
    // (floor supervisor, control) does not depend on this endpoint.
  }
}

function updateLeadership(snap) {
  const risks = snap.stations
    .map((s) => (s.defect_risk ? s.defect_risk.value : null))
    .filter((v) => v !== null && v !== undefined);
  const meanRisk = risks.length ? risks.reduce((a, b) => a + b, 0) / risks.length : null;

  $("ld-mean-risk").textContent = meanRisk === null ? "no model" : meanRisk.toFixed(4);

  if (economicsConfig && meanRisk !== null) {
    const unitsAtRisk = meanRisk * economicsConfig.qc_lag_units;
    const dollars = unitsAtRisk * economicsConfig.rework_cost_delta_usd;
    $("ld-units-at-risk").textContent = unitsAtRisk.toFixed(2);
    $("ld-dollars").textContent = `$${dollars.toFixed(0)}`;
  }

  const instrumented = snap.stations.filter((s) => s.instrumented).length;
  $("ld-station-count").textContent = snap.stations.length;
  $("ld-instrumented-count").textContent = instrumented;
  $("ld-dark-count").textContent = snap.stations.length - instrumented;
}

async function fetchSensorPlacement() {
  const budget = parseInt($("ld-budget").value, 10);
  const el = $("ld-recommend");
  try {
    const resp = await fetch(`/api/twin/sensor_placement?budget=${budget}`);
    const body = await resp.json();
    el.innerHTML = "";
    body.recommended_next.forEach((sid, i) => {
      const row = document.createElement("div");
      row.className = "freq-row";
      row.innerHTML = `<span class="freq-id">#${i + 1}</span><span>${sid}</span>`;
      el.appendChild(row);
    });
    if (body.recommended_next.length === 0) {
      el.innerHTML = '<p class="panel-sub">Every station is already instrumented.</p>';
    }
  } catch {
    el.innerHTML = '<p class="panel-sub">Could not load a recommendation.</p>';
  }
}

/* ---------------------------------------------------------------------
 * Persona tabs
 * ------------------------------------------------------------------- */

function switchView(viewId) {
  for (const view of document.querySelectorAll(".persona-view")) {
    view.hidden = view.id !== viewId;
  }
  for (const tab of document.querySelectorAll(".persona-tab")) {
    tab.classList.toggle("is-active", tab.dataset.view === viewId);
  }
}

async function restartEngine() {
  await fetch("/api/twin/restart", { method: "POST" });
  // The engine only emits a fresh run_meta to a NEWLY opened stream
  // connection (see api/sse.py) -- an already-open EventSource would just
  // see tick counters drop back to 1 with no explicit "this is a new run"
  // signal. Reconnecting here is what actually picks up the new run_meta.
  if (es) es.close();
  connect();
}

$("ctl-mult").addEventListener("input", (e) => {
  $("ctl-mult-val").textContent = `${parseFloat(e.target.value).toFixed(1)}×`;
});
$("ctl-apply").addEventListener("click", applyControl);
$("btn-kill").addEventListener("click", killStream);
$("btn-resume").addEventListener("click", resumeStream);
$("btn-restart").addEventListener("click", restartEngine);

for (const tab of document.querySelectorAll(".persona-tab")) {
  tab.addEventListener("click", () => switchView(tab.dataset.view));
}

$("ld-budget").addEventListener("input", (e) => {
  $("ld-budget-val").textContent = e.target.value;
  // Debounced -- a slider drag fires many input events per second, and
  // each one is a real HTTP request server-side (Phase 9's greedy
  // placement re-solves per pick, not free).
  clearTimeout(ldBudgetFetchTimer);
  ldBudgetFetchTimer = setTimeout(fetchSensorPlacement, 250);
});

loadEconomicsConfig();
fetchSensorPlacement();
connect();
