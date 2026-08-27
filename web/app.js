/* LineTwin floor-supervisor dashboard. Vanilla JS, no framework, no build
 * step -- one EventSource against /api/twin/stream, DOM updates driven
 * directly off each parsed snapshot. Vendored uPlot (web/vendor/uplot) for
 * the two trend charts.
 */

const ZONE_ORDER = ["body", "paint", "final"];
const HISTORY_LEN = 120; // ~15s of ticks at 8Hz -- enough to see a trend, not a full run

let es = null;
let killed = false;
let stationOrder = [];
let throughputChart = null;
let wipChart = null;
const history = { ticks: [], throughput: [], wip: [] };

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
  const tagLabel = ct.missingness !== "present" ? ct.missingness : ct.source;
  const sourceTag = `<span class="confidence-pill" data-source="${tagLabel}">${tagLabel}</span>`;

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

connect();
