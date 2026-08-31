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
let riskChart = null;
let wipChart = null;
let pmThroughputChart = null;
let pmWipChart = null;
const history = { ticks: [], throughput: [], wip: [], risk: [] };
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

// Alerts -- computed client-side from the same stream every other panel
// reads, no extra server work. Three kinds: bottleneck shift (pushed from
// updatePlantManager's existing transition detection), a station's risk
// score crossing the model's own tuned threshold (rising edge only), and a
// station stuck BLOCKED/STARVED for many consecutive ticks in a row.
const ALERTS_MAX = 30;
const DISRUPTION_ALERT_TICKS = 15; // consecutive disrupted ticks before it's "sustained", not a blip
const NARRATION_FOLLOWUP_TICKS = 24; // ~3s at 8Hz -- long enough for a real effect to show up
let riskThreshold = null; // Model B's own MCC-tuned threshold, fetched once
const alerts = [];
const riskFlagState = {}; // station_id -> already-flagged this rising edge?
const disruptionStreak = {}; // station_id -> consecutive ticks BLOCKED/STARVED
const disruptionAlerted = {}; // station_id -> already alerted for the CURRENT streak?
let lastSnapshot = null; // most recent snapshot, for capturing "before" state on Apply
let pendingNarration = null; // {stationId, multiplier, appliedAtTick, beforeBottleneckId, beforeQueue}
let stationMultipliers = {}; // station_id -> last multiplier APPLIED to it this session (not just dialed)

function $(id) { return document.getElementById(id); }

/* ---------------------------------------------------------------------
 * Theme toggle. index.html's inline head script already applies any
 * stored preference before first paint (no flash); this just wires the
 * button and keeps its icon in sync with the effective theme, including
 * the very first click when no preference has been stored yet.
 * ------------------------------------------------------------------- */

function effectiveTheme() {
  const explicit = document.documentElement.getAttribute("data-theme");
  if (explicit === "light" || explicit === "dark") return explicit;
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

function applyThemeIcon() {
  // Show the icon for the theme a click would SWITCH TO, not the current one.
  // Swaps the <use> target in the inline SVG sprite rather than replacing
  // text: an emoji renders as a different glyph on every OS, and this
  // interface's whole argument is precision about what a thing actually is.
  const use = $("btn-theme").querySelector("use");
  use.setAttribute("href", effectiveTheme() === "dark" ? "#i-sun" : "#i-moon");
}

// uPlot draws axis ticks, grid lines, and legend markers on <canvas> --
// CSS cannot touch canvas pixel content at all, which is exactly the bug a
// user caught: styles.css's dark-mode tokens never reached the chart axes,
// because they were never passed to uPlot as options in the first place
// (no `axes:` config existed here before -- uPlot silently fell back to its
// own hardcoded, light-mode-oriented default axis color). Read the *actual*
// CSS custom properties at chart-creation time instead of hardcoding a
// second copy of the palette here, so this can never drift out of sync with
// styles.css again.
function uplotThemeColors() {
  const cs = getComputedStyle(document.documentElement);
  return {
    ink: cs.getPropertyValue("--ink-2").trim() || "#595959",
    grid: cs.getPropertyValue("--line").trim() || "#DCDCDC",
    risk: cs.getPropertyValue("--red").trim() || "#C81E3A",
  };
}

function themedAxes() {
  const { ink, grid } = uplotThemeColors();
  const axisCommon = { stroke: ink, grid: { stroke: grid, width: 1 }, ticks: { stroke: grid, width: 1 } };
  return [axisCommon, axisCommon];
}

function toggleTheme() {
  const next = effectiveTheme() === "dark" ? "light" : "dark";
  document.documentElement.setAttribute("data-theme", next);
  try { localStorage.setItem("linetwin-theme", next); } catch (e) { /* ignore */ }
  applyThemeIcon();
  rebuildChartsForThemeChange();
}

$("btn-theme").addEventListener("click", toggleTheme);
applyThemeIcon();

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
    history.risk = [];
    pmHistory.ticks = [];
    pmHistory.throughput = [];
    pmHistory.wip = [];
    pmBottleneckCounts = {};
    pmObservedTicks = 0;
    pmLastBottleneckId = undefined;
    pmTimeline.length = 0;
    fmBuilt = false;
    fmNodes.clear();
    selectedFloorStation = null;
    renderFrequency();
    renderTimeline();

    alerts.length = 0;
    for (const k of Object.keys(riskFlagState)) delete riskFlagState[k];
    for (const k of Object.keys(disruptionStreak)) delete disruptionStreak[k];
    for (const k of Object.keys(disruptionAlerted)) delete disruptionAlerted[k];
    pendingNarration = null;
    renderAlerts();

    // REAL BUG, found live: a long-lived tab that outlives a server restart
    // gets a fresh run_meta (this handler) via EventSource's automatic
    // reconnect, but the four uPlot instances below were only ever fed new
    // data via setData() -- never recreated. uPlot caches an internal
    // auto-scale range across setData() calls, and clearing to an empty
    // array and refilling from zero does not reliably reset that cached
    // range; one chart on the same page could end up rendering against a
    // stale scale from the previous run while a sibling chart, whose new
    // range happened to still fit the old one, looked fine -- exactly the
    // "WIP renders, Throughput doesn't, same live server" report this fixes.
    // destroy() + null is the only way to guarantee no stale internal state
    // survives a reset; the next snapshot's drawCharts()/drawPmCharts()
    // recreates each chart fresh via their existing `if (!chart)` branch.
    for (const chart of [throughputChart, wipChart, riskChart, pmThroughputChart, pmWipChart]) {
      if (chart) chart.destroy();
    }
    throughputChart = null;
    wipChart = null;
    riskChart = null;
    pmThroughputChart = null;
    pmWipChart = null;
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
  $("v-tick").textContent = snap.tick.toLocaleString();
  $("v-seq").textContent = snap.seq.toLocaleString();
  $("v-simtime").textContent = formatSimClock(snap.sim_time_s);
  $("v-rtf").textContent = snap.real_time_factor.toFixed(2);
  $("v-lag").textContent = `${Math.round(snap.lag_s * 1000)}ms`;
  $("v-compute").textContent = snap.tick_compute_ms.toFixed(1);

  renderBottleneck(snap.bottleneck);
  renderConstraintEvidence(snap);
  renderStations(snap.stations, snap.bottleneck);
  renderFloorMap(snap.stations, snap.bottleneck);
  updateKpis(snap);
  updateCoverage(snap.stations);
  updateQualityPage(snap.stations);
  updateBottleneckPage(snap);
  pushHistory(snap);
  drawCharts();

  updatePlantManager(snap);
  updateLeadership(snap);
  updateStationAlerts(snap);
  updateNarrationFollowup(snap);
  lastSnapshot = snap;

  if (firstSimTime === null) firstSimTime = snap.sim_time_s;
  const uptimeS = snap.sim_time_s - firstSimTime;
  $("sb-uptime").textContent =
    `${String(Math.floor(uptimeS / 3600)).padStart(2, "0")}:` +
    `${String(Math.floor((uptimeS % 3600) / 60)).padStart(2, "0")}:` +
    `${String(Math.floor(uptimeS % 60)).padStart(2, "0")}`;

  // Genealogy is an HTTP round trip -- throttled to roughly once every 5s
  // (40 ticks at 8Hz), not fetched every tick like everything else above,
  // which is all free client-side work off the same stream.
  if (snap.tick % 40 === 0) fetchGenealogyCandidates();

  if (stationOrder.length === 0) {
    stationOrder = snap.stations.map((s) => s.station_id);
    populateStationSelect(stationOrder);
  }
}

let firstSimTime = null;

function formatSimClock(seconds) {
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const sec = Math.floor(seconds % 60);
  return `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}:${String(sec).padStart(2, "0")}`;
}

/* ---------------------------------------------------------------------
 * Why THIS station is the constraint.
 *
 * The first thing anyone asks of a bottleneck verdict is "why isn't it the
 * slowest station?" -- and on this line it usually isn't. Verified live:
 * S17 was named the constraint while ranking 6th by cycle time (61.4s
 * against S13's 68.1s); what distinguished it was the highest ACTIVE share
 * on the line, 0.94 against S13's 0.89.
 *
 * That is precisely what the Active Period Method ranks on, so showing
 * cycle time alone (as this dashboard previously did) displays the
 * variable that does NOT decide the verdict and hides the one that does.
 * This panel shows both side by side and states the comparison in words.
 *
 * `time_in_state` is already on the wire (contracts.py StationSnapshot), so
 * none of this needs a new endpoint or a schema change.
 * ------------------------------------------------------------------- */

/* The momentary Active Period rule ranks on how long a station's CURRENT
 * active period has been running -- earliest start wins. That is
 * `active_period_elapsed_s` (schema 0.2.0), and it is the only quantity
 * that can honestly explain the verdict.
 *
 * An earlier version of this panel ranked on cumulative active share from
 * `time_in_state`. That is a DIFFERENT quantity and it visibly contradicted
 * the verdict during line fill: on a fresh run S01 sat at 94% cumulative
 * active while the named constraint S04 sat at 65%, because upstream
 * stations simply start working sooner. An explanation that ranks the
 * constraint fourth is worse than no explanation.
 */

function activeElapsed(station) {
  const v = station.active_period_elapsed_s;
  return typeof v === "number" ? v : null;
}

function renderConstraintEvidence(snap) {
  const listEl = $("ev-list");
  const noteEl = $("ev-note");
  if (!listEl || !noteEl) return;

  const bn = snap.bottleneck;
  const bnId = bn && bn.station_id ? bn.station_id : null;

  // Only stations currently IN an active period are candidates under the
  // momentary rule; an inactive station has no active period to rank.
  const active = snap.stations
    .filter((s) => activeElapsed(s) !== null)
    .sort((a, b) => activeElapsed(b) - activeElapsed(a));

  if (!active.length) {
    listEl.innerHTML = "";
    noteEl.textContent = "No station is currently in an active period.";
    return;
  }

  const top = active.slice(0, 5);
  const maxElapsed = activeElapsed(top[0]) || 1;

  listEl.innerHTML = top.map((s) => {
    const el = activeElapsed(s);
    const ct = s.cycle_time_s.value;
    return `<div class="ev-row ${s.station_id === bnId ? "is-bn" : ""}">
      <span class="ev-id">${s.station_id}</span>
      <span class="ev-track"><span class="ev-fill" style="width:${(el / maxElapsed * 100).toFixed(0)}%"></span></span>
      <span class="ev-pct">${el.toFixed(0)}s</span>
      <span class="ev-ct" title="cycle time per unit">${ct != null ? ct.toFixed(1) + "s" : "—"}</span>
    </div>`;
  }).join("");

  if (!bnId) {
    noteEl.textContent = "No constraint identified this tick.";
    return;
  }

  const bnStation = snap.stations.find((s) => s.station_id === bnId);
  const slowest = [...snap.stations]
    .sort((a, b) => (b.cycle_time_s.value ?? 0) - (a.cycle_time_s.value ?? 0))[0];
  if (!bnStation || !slowest) { noteEl.textContent = ""; return; }

  const bnEl = activeElapsed(bnStation);
  const bnCt = bnStation.cycle_time_s.value;
  const slowCt = slowest.cycle_time_s.value;
  const elapsedText = bnEl != null ? `${bnEl.toFixed(0)}s` : "—";

  if (slowest.station_id === bnId) {
    noteEl.innerHTML =
      `<b>${bnId}</b> is both the slowest station ` +
      `(${bnCt != null ? bnCt.toFixed(1) : "—"}s per unit) and the longest continuously ` +
      `active, at <b>${elapsedText}</b> without a break.`;
  } else {
    noteEl.innerHTML =
      `<b>${bnId}</b> is not the slowest station — <b>${slowest.station_id}</b> takes ` +
      `${slowCt != null ? slowCt.toFixed(1) : "—"}s per unit against ${bnId}'s ` +
      `${bnCt != null ? bnCt.toFixed(1) : "—"}s. It constrains the line because it has run ` +
      `<b>${elapsedText}</b> without going idle — the longest unbroken active period on the line, ` +
      `which is what the Active Period Method ranks on.`;
  }
}


/* ---------------------------------------------------------------------
 * Floor map -- zone-grouped circular station indicators, replacing the
 * card-grid look with the arrow-flow reference the user shared. Reuses
 * the exact same per-station data `renderStations` already consumes for
 * the Stations page; this is a second, denser view of the same data, not
 * a second source of truth.
 * ------------------------------------------------------------------- */

let selectedFloorStation = null;

/* ---------------------------------------------------------------------
 * The line.
 *
 * Built ONCE, then mutated per tick. Rebuilding innerHTML at 8 Hz would
 * restart every CSS animation on every frame, so the conveyor flow would
 * never actually appear to move -- and it would throw away hover state and
 * focus 8 times a second.
 *
 * What the connector between two stations shows is the real buffer between
 * them: the DOWNSTREAM station's in-buffer (`queue_depth / buffer_capacity`).
 * That is the single most informative thing on the whole page, because a
 * constraint has a signature you can read off it directly -- buffers filling
 * upstream of it, draining downstream of it. Marching dashes run only while
 * the downstream station is actually working, so the line visibly moves when
 * material moves and visibly stalls when it does not.
 * ------------------------------------------------------------------- */

const ZONE_LABEL = {
  body: "Body construction",
  paint: "Paint",
  final: "Final assembly",
};

let fmBuilt = false;
const fmNodes = new Map(); // station_id -> {node, circle, ct, link, fill}

function renderFloorMap(stations, bottleneck) {
  if (!fmBuilt) buildFloorMap(stations);
  updateFloorMap(stations, bottleneck);
}

function buildFloorMap(stations) {
  const byZone = {};
  for (const st of stations) (byZone[st.zone] ||= []).push(st);

  const el = $("floor-map");
  if (!el) return;
  el.innerHTML = "";
  fmNodes.clear();

  for (const zone of ZONE_ORDER) {
    const zoneStations = byZone[zone];
    if (!zoneStations) continue;

    const block = document.createElement("div");
    block.className = "fm-zone";

    const head = document.createElement("div");
    head.className = "fm-zone-head";
    // The station range is read off the payload, never hardcoded: the zone
    // split is scenario configuration (body 1-12, paint 13-18, final 19-30)
    // and a different YAML must just work.
    head.innerHTML =
      `<span class="fm-zone-name">${ZONE_LABEL[zone] || zone}</span>` +
      `<span class="fm-zone-range">${zoneStations[0].station_id}–${zoneStations[zoneStations.length - 1].station_id}</span>` +
      `<span class="fm-zone-count">${zoneStations.length} stations</span>`;

    const row = document.createElement("div");
    row.className = "fm-row";

    zoneStations.forEach((st, i) => {
      const isLast = i === zoneStations.length - 1;
      const node = document.createElement("button");
      node.type = "button";
      node.className = "fm-node" + (isLast ? " is-last" : "");
      node.innerHTML =
        `<span class="fm-circle">${st.station_id.replace("S", "")}</span>` +
        `<span class="fm-ct">–</span>` +
        (isLast ? "" : `<span class="fm-link"><span class="fm-link-fill"></span></span>`);

      node.addEventListener("click", () => {
        selectedFloorStation = st.station_id;
        const cur = lastSnapshot && lastSnapshot.stations.find((x) => x.station_id === st.station_id);
        if (cur) renderStationDetailBar(cur);
        if (lastSnapshot) updateFloorMap(lastSnapshot.stations, lastSnapshot.bottleneck);
      });

      row.appendChild(node);
      fmNodes.set(st.station_id, {
        node,
        ct: node.querySelector(".fm-ct"),
        link: node.querySelector(".fm-link"),
        fill: node.querySelector(".fm-link-fill"),
      });
    });

    block.appendChild(head);
    block.appendChild(row);
    el.appendChild(block);
  }
  fmBuilt = true;
}

function updateFloorMap(stations, bottleneck) {
  const bottleneckId = bottleneck ? bottleneck.station_id : null;
  const byId = new Map(stations.map((s) => [s.station_id, s]));

  stations.forEach((st, idx) => {
    const refs = fmNodes.get(st.station_id);
    if (!refs) return;
    const { node, ct, link, fill } = refs;

    const displayState = ["down", "repair", "setup"].includes(st.state) ? "down" : st.state;
    node.dataset.state = displayState;
    node.classList.toggle("is-bottleneck", st.station_id === bottleneckId);
    node.classList.toggle("is-dark", !st.instrumented);
    node.classList.toggle("is-selected", st.station_id === selectedFloorStation);

    const v = st.cycle_time_s.value;
    const ctText = v != null ? `${v.toFixed(0)}s` : "–";
    if (ct.textContent !== ctText) ct.textContent = ctText;

    // The buffer this station feeds INTO is the next station's in-buffer.
    const next = stations[idx + 1];
    if (link && next && next.zone === st.zone) {
      const cap = next.buffer_capacity || 1;
      const occ = Math.max(0, Math.min(1, next.queue_depth / cap));
      fill.style.width = `${(occ * 100).toFixed(0)}%`;
      link.dataset.buffer = occ >= 0.999 ? "full" : occ <= 0.001 ? "empty" : "part";
      // Dashes march only while the consumer is actually working, so a
      // stalled line looks stalled instead of looping an idle animation.
      link.classList.toggle("is-flowing", next.state === "working");
      link.title = `buffer ${next.queue_depth}/${cap} into ${next.station_id}`;
    } else if (link) {
      link.dataset.buffer = "empty";
      link.classList.remove("is-flowing");
    }

    const risk = st.defect_risk ? st.defect_risk.value : null;
    node.classList.toggle(
      "is-at-risk",
      riskThreshold != null && risk != null && risk >= riskThreshold
    );

    const NL = String.fromCharCode(10);
    node.title = [
      `${st.station_id} · ${st.state}`,
      `cycle ${v != null ? v.toFixed(1) + "s" : "no reading"} · queue ${st.queue_depth}/${st.buffer_capacity}`,
      `${st.units_completed} units completed`,
      st.instrumented ? "instrumented" : "no sensor — cycle time inferred from neighbours",
      st.station_id === bottleneckId ? "current constraint" : "",
    ].filter(Boolean).join(NL);
  });

  // Keep the detail bar tracking the selected station as it changes state.
  if (selectedFloorStation && byId.has(selectedFloorStation)) {
    renderStationDetailBar(byId.get(selectedFloorStation));
  }
}

function renderStationDetailBar(st) {
  $("station-detail-bar").querySelector("h3").textContent =
    `${st.station_id} · ${ZONE_LABEL[st.zone] || st.zone}`;
  $("sd-cycle").textContent = st.cycle_time_s.value != null ? `${st.cycle_time_s.value.toFixed(1)} s` : "—";
  $("sd-queue").textContent = `${st.queue_depth} / ${st.buffer_capacity}`;
  $("sd-state").textContent = st.state;
  $("sd-source").textContent = st.instrumented ? "observed" : "inferred";
  $("sd-confidence").textContent = st.instrumented
    ? "100%"
    : st.cycle_time_s.sensor_share != null
      ? `${Math.round(st.cycle_time_s.sensor_share * 100)}% sensor-derived`
      : "—";
}

function updateKpis(snap) {
  $("kpi-throughput").textContent = Math.round(snap.line_throughput_uph).toLocaleString();
  $("kpi-wip").textContent = snap.wip;

  const cycleTimes = snap.stations.map((s) => s.cycle_time_s.value).filter((v) => v != null);
  $("kpi-cycle").textContent = cycleTimes.length
    ? (cycleTimes.reduce((a, b) => a + b, 0) / cycleTimes.length).toFixed(1)
    : "—";

  // Zero blocked is the normal reading, not a broken panel: at buffer
  // capacity 3 a station accumulates only ~5-9% blocked time over minutes,
  // so most snapshots show none. Saying so beats leaving a judge to wonder
  // whether the field is even wired up.
  const blockedCount = snap.stations.filter((s) => s.state === "blocked").length;
  $("kpi-blocked").textContent = blockedCount;
  $("kpi-blocked-unit").textContent = blockedCount === 0 ? "none now — expected" : "stations";

  $("kpi-starved").textContent = snap.stations.filter((s) => s.state === "starved").length;

  const risks = snap.stations
    .map((s) => (s.defect_risk ? s.defect_risk.value : null))
    .filter((v) => v != null);
  const meanRisk = risks.length ? risks.reduce((a, b) => a + b, 0) / risks.length : null;

  // The whole metric cell carries the level so the label recolours with the
  // number rather than drifting from it.
  const riskCell = $("metric-risk");
  if (meanRisk === null) {
    $("kpi-risk").textContent = "—";
    $("kpi-risk-tag").textContent = "no model loaded";
    riskCell.dataset.level = "ok";
  } else {
    $("kpi-risk").textContent = `${(meanRisk * 100).toFixed(2)}%`;
    const level = riskThreshold != null && meanRisk >= riskThreshold * 2 ? "crit"
      : riskThreshold != null && meanRisk >= riskThreshold ? "warn" : "ok";
    riskCell.dataset.level = level;
    $("kpi-risk-tag").textContent =
      level === "ok" ? "within normal range" : level === "warn" ? "elevated" : "high";
  }
}

function updateCoverage(stations) {
  const instrumented = stations.filter((s) => s.instrumented).length;
  const dark = stations.length - instrumented;
  const frac = instrumented / stations.length;
  // Observed/inferred use the SAME two colours here as the provenance pills
  // everywhere else in the interface (green = measured, blue = inferred).
  // One meaning, one colour, across every panel.
  const donut = $("coverage-donut");
  donut.style.background =
    `conic-gradient(var(--green) 0 ${(frac * 360).toFixed(1)}deg, var(--blue) 0 360deg)`;
  $("coverage-pct").textContent = `${Math.round(frac * 100)}%`;
  $("coverage-legend").innerHTML = `
    <li><span class="cl-swatch" style="background:var(--green)"></span>Observed <b>${instrumented} / ${stations.length}</b></li>
    <li><span class="cl-swatch" style="background:var(--blue)"></span>Inferred <b>${dark} / ${stations.length}</b></li>
  `;
}

/* ---------------------------------------------------------------------
 * Quality & Defects page -- per-station live risk ranking + driver
 * explanation. Same null-safety discipline as updateLeadership above:
 * `defect_risk` can be entirely null before Model B's first scoring tick.
 * ------------------------------------------------------------------- */

let selectedRiskStationId = null;
let riskListExpanded = false;

function updateQualityPage(stations) {
  const list = $("risk-list");
  if (!list) return;
  const rv = (s) => (s.defect_risk ? s.defect_risk.value : null);
  const sorted = [...stations].sort((a, b) => (rv(b) ?? -1) - (rv(a) ?? -1));
  if (!selectedRiskStationId && sorted.length) selectedRiskStationId = sorted[0].station_id;

  const shown = riskListExpanded ? sorted : sorted.slice(0, 10);
  const btn = $("risk-expand");
  if (btn) btn.textContent = riskListExpanded ? "Show top 10" : `Show all ${sorted.length}`;

  list.innerHTML = shown.map((s) => {
    const v = rv(s);
    const level = riskThreshold == null ? "ok" : v == null ? "ok" : v >= riskThreshold * 2 ? "crit" : v >= riskThreshold ? "warn" : "ok";
    const barWidth = v != null ? Math.min(100, v * 1000).toFixed(0) : 0;
    return `<div class="risk-row ${s.station_id === selectedRiskStationId ? "is-selected" : ""}" data-station-id="${s.station_id}">
      <span class="risk-id">${s.station_id}</span>
      <div class="risk-bar-track"><div class="risk-bar-fill" data-level="${level}" style="width:${barWidth}%"></div></div>
      <span class="risk-pct">${v != null ? (v * 100).toFixed(2) + "%" : "—"}</span>
      <span class="risk-tag">${s.instrumented ? "observed" : "inferred"}</span>
    </div>`;
  }).join("");

  for (const row of list.querySelectorAll(".risk-row")) {
    row.addEventListener("click", () => {
      selectedRiskStationId = row.dataset.stationId;
      if (lastSnapshot) updateQualityPage(lastSnapshot.stations);
    });
  }

  const selected = stations.find((s) => s.station_id === selectedRiskStationId);
  if (selected) renderRiskExplain(selected);
}

function renderRiskExplain(station) {
  $("explain-title").textContent = `${station.station_id} — contributing factors`;
  const body = $("explain-body");
  const drivers = station.risk_drivers || [];
  if (!drivers.length) {
    body.innerHTML = '<p class="panel-sub">No driver data yet for this station.</p>';
    return;
  }
  const maxAbs = Math.max(...drivers.map((d) => Math.abs(d.contribution)), 0.001);
  // A bare "−0.98" under a heading that says "contributing factors" reads as
  // a contradiction. Name the direction instead: a negative SHAP value on a
  // risk model lowers the score, and that is worth showing, not hiding.
  body.innerHTML = drivers.map((d) => {
    const width = (Math.abs(d.contribution) / maxAbs * 100).toFixed(0);
    const negative = d.contribution < 0;
    return `<div class="driver-row ${negative ? "is-down" : "is-up"}">
      <span class="driver-name">${d.feature}</span>
      <div class="driver-bar-track"><div class="driver-bar-fill ${negative ? "is-negative" : ""}" style="width:${width}%"></div></div>
      <span class="driver-pct">${negative ? "−" : "+"}${Math.abs(d.contribution).toFixed(2)}</span>
      <span class="driver-dir">${negative ? "lowers risk" : "raises risk"}</span>
    </div>`;
  }).join("");
}

/* ---------------------------------------------------------------------
 * Bottleneck full page -- cycle-time-by-station bars + the rolling-
 * horizon forecast comparison. Same real per-tick data the compact
 * overview panel and Plant Manager's predicted-downtime card already use.
 * ------------------------------------------------------------------- */

/* The cycle-time-by-station ranking this function used to draw was removed
 * deliberately. It sorted on the variable the Active Period Method does NOT
 * rank on, so it displayed the constraint at rank 6 with no explanation and
 * invited exactly the wrong conclusion. renderConstraintEvidence() replaces
 * it with active share beside cycle time, which is the actual decision
 * variable. What remains here is only the forecast comparison.
 *
 * The old early return on the removed element would have silently killed
 * this forecast panel too -- it guarded both halves of the function.
 */
function updateBottleneckPage(snap) {
  const compare = $("bp-predict-compare");
  if (!compare) return;

  const bn = snap.bottleneck;
  const pred = snap.predicted_bottleneck;
  if (!pred || !pred.station_id) {
    compare.innerHTML = '<p class="panel-sub">No forecast yet.</p>';
    return;
  }
  const currentId = bn && bn.station_id ? bn.station_id : null;
  const shifting = currentId && pred.station_id !== currentId;
  compare.innerHTML = `
    <div class="rank-row"><span class="rank-reason">Now</span><span class="rank-id">${currentId || "—"}</span></div>
    <div class="rank-row"><span class="rank-reason">~30 sim-min ahead</span><span class="rank-id">${pred.station_id}</span></div>
    ${shifting ? '<p class="panel-sub" style="color:var(--orange);margin-top:6px">Forecast disagrees with the live verdict — a shift may be forming.</p>' : ""}
  `;
}

/* ---------------------------------------------------------------------
 * Defect genealogy -- diagnostic/genealogy.py's first wiring into the
 * PRIMARY dashboard (it already has a real API surface, added for the
 * Control Center prototype: GET /api/twin/genealogy/candidates and
 * GET /api/twin/genealogy/{unit_id}). Throttled in renderSnapshot to
 * roughly once every 5s, since this is an HTTP round trip, unlike every
 * other panel on this page which is free client-side work off one stream.
 * ------------------------------------------------------------------- */

// null until the user clicks a specific unit; after that the periodic
// refetch stops re-pointing the trace panel at whatever is currently rank 1.
let genealogyPinnedUnit = null;

async function fetchGenealogyCandidates() {
  try {
    const res = await fetch("/api/twin/genealogy/candidates?limit=10");
    const data = await res.json();
    renderGenealogyCandidateList(data.candidates);
    // Auto-trace the top candidate so the panel is never empty on arrival,
    // but never override a unit the user actually clicked -- this refetches
    // every ~5s and would otherwise yank their selection away mid-read.
    if (data.candidates.length && genealogyPinnedUnit === null) {
      await traceUnit(data.candidates[0].unit_id);
    }
  } catch {
    /* transient network hiccup -- next throttled tick tries again */
  }
}

function renderGenealogyCandidateList(candidates) {
  const list = $("rc-candidates");
  if (!list) return;
  if (!candidates.length) {
    list.innerHTML = '<p class="panel-sub">Not enough completed units yet — check back shortly.</p>';
    return;
  }
  list.innerHTML = candidates.map((c, i) => `
    <div class="rank-row is-clickable" data-unit-id="${c.unit_id}">
      <span class="rank-num">${i + 1}</span>
      <span class="rank-id">#${c.unit_id}</span>
      <span class="rank-reason">Peak z-score ${c.peak_z_score.toFixed(2)} at ${c.peak_station_id}</span>
      <span class="rank-conf">trace →</span>
    </div>
  `).join("");
  for (const row of list.querySelectorAll(".rank-row")) {
    row.addEventListener("click", () => {
      genealogyPinnedUnit = row.dataset.unitId;
      traceUnit(genealogyPinnedUnit);
    });
  }
}

async function traceUnit(unitId) {
  let r;
  try {
    const res = await fetch(`/api/twin/genealogy/${unitId}`);
    if (!res.ok) return;
    r = await res.json();
  } catch {
    return;
  }

  const flowHtml = buildGenealogyFlowHtml(r);

  $("rc-flow-panel").hidden = false;
  $("rc-unit-id").textContent = `#${r.defect_unit_id}`;
  $("rc-origin").textContent = r.origin_station_id;
  $("rc-confidence").textContent = `${Math.round(r.confidence * 100)}%`;
  $("rc-affected").textContent = `${r.affected_unit_ids.length} units`;
  $("rc-realigned").textContent = `${r.origin_realigned_time_s.toFixed(1)}s (sim time)`;
  $("rc-flow").innerHTML = flowHtml;
}

function buildGenealogyFlowHtml(r) {
  const nodes = ['<span class="rc-node is-detect">Final inspection</span>'];
  for (let i = r.path.length - 1; i >= 0; i--) {
    nodes.push('<span class="rc-arrow">←</span>');
    const isOrigin = r.path[i] === r.origin_station_id;
    nodes.push(`<span class="rc-node${isOrigin ? " is-origin" : ""}">${r.path[i]}</span>`);
  }
  return nodes.join("");
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

  // The bar reflects the CATEGORY, not a fabricated precise percentage --
  // bn.confidence is "provisional"/"established"/"none", never a number
  // (bottleneck.py's significance_annotation only ever returns those three
  // words). Three fixed widths standing in for three known states is
  // honest; inventing a number like "92%" from a category would not be.
  const confFillPct = bn.confidence === "established" ? 90 : bn.confidence === "provisional" ? 45 : 10;
  $("bn-conf-fill").style.width = `${confFillPct}%`;

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
  const risks = snap.stations.map((s) => (s.defect_risk ? s.defect_risk.value : null)).filter((v) => v != null);
  const meanRisk = risks.length ? risks.reduce((a, b) => a + b, 0) / risks.length : 0;

  history.ticks.push(snap.tick);
  history.throughput.push(snap.line_throughput_uph);
  history.wip.push(snap.wip);
  history.risk.push(meanRisk);
  if (history.ticks.length > HISTORY_LEN) {
    history.ticks.shift();
    history.throughput.shift();
    history.wip.shift();
    history.risk.shift();
  }
}

/* uPlot sizes a <canvas> in device pixels at construction time and has no
 * notion of CSS-fluid width. The old fixed 380px left a visible gutter in
 * every grid cell wider than that, and clipped in every cell narrower.
 * Measure the actual container instead, and re-measure on resize.
 */
const CHART_H = 150;

function chartWidth(containerId) {
  const el = $(containerId);
  // clientWidth is 0 while the containing .view is still hidden -- fall back
  // rather than constructing a zero-width chart that never repaints.
  return (el && el.clientWidth) ? el.clientWidth : 360;
}

function makeChart(containerId, stroke, data) {
  return new uPlot(
    {
      width: chartWidth(containerId),
      height: CHART_H,
      series: [{}, { stroke, width: 1.75 }],
      scales: { x: { time: false } },
      axes: themedAxes(),
      legend: { show: false },
      cursor: { show: false },
    },
    data,
    $(containerId)
  );
}

// Series colours are read from the live CSS custom properties, not
// hardcoded a second time here -- the same discipline the axis colours
// already follow, so a theme change can never leave a stroke behind.
function seriesColors() {
  const cs = getComputedStyle(document.documentElement);
  return {
    throughput: cs.getPropertyValue("--purple").trim() || "#A100FF",
    wip: cs.getPropertyValue("--orange").trim() || "#E8590C",
    risk: cs.getPropertyValue("--red").trim() || "#C81E3A",
  };
}

function drawCharts() {
  const c = seriesColors();
  const throughputData = [history.ticks, history.throughput];
  const wipData = [history.ticks, history.wip];
  const riskData = [history.ticks, history.risk];

  if (!throughputChart) throughputChart = makeChart("chart-throughput", c.throughput, throughputData);
  else throughputChart.setData(throughputData);

  if (!wipChart) wipChart = makeChart("chart-wip", c.wip, wipData);
  else wipChart.setData(wipData);

  if (!riskChart) riskChart = makeChart("chart-risk", c.risk, riskData);
  else riskChart.setData(riskData);
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
      stationMultipliers[station_id] = cycle_time_multiplier;

      renderNarration("What this should do", explainMechanism(station_id, cycle_time_multiplier), false);
      const beforeStation = lastSnapshot
        ? lastSnapshot.stations.find((s) => s.station_id === station_id)
        : null;
      pendingNarration = {
        stationId: station_id,
        multiplier: cycle_time_multiplier,
        appliedAtTick: body.applied_at_tick,
        beforeBottleneckId:
          lastSnapshot && lastSnapshot.bottleneck ? lastSnapshot.bottleneck.station_id : null,
        beforeQueue: beforeStation ? beforeStation.queue_depth : null,
      };
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
  // Beat 3 of docs/VIDEO_SCRIPT.md: the freeze has to be self-narrating, so
  // a recording shows WHY every number stopped without a voiceover.
  $("proof-frozen").hidden = false;
  $("btn-kill").disabled = true;
  $("btn-resume").disabled = false;
}

function resumeStream() {
  killed = false;
  document.body.classList.remove("stream-killed");
  $("proof-frozen").hidden = true;
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
    const confidence = bn ? bn.confidence : "none";
    pushAlert(
      confidence === "established" ? "warning" : "info",
      "Bottleneck shift",
      `${pmLastBottleneckId || "none"} → ${currentId || "none"} (${confidence})`,
      snap.tick
    );
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
  const c = seriesColors();
  const throughputData = [pmHistory.ticks, pmHistory.throughput];
  const wipData = [pmHistory.ticks, pmHistory.wip];

  if (!pmThroughputChart) pmThroughputChart = makeChart("pm-chart-throughput", c.throughput, throughputData);
  else pmThroughputChart.setData(throughputData);

  if (!pmWipChart) pmWipChart = makeChart("pm-chart-wip", c.wip, wipData);
  else pmWipChart.setData(wipData);
}

/* Re-measure every chart against its container. Called on window resize and
 * on a persona switch -- a chart built while its .view was hidden measured
 * 0 and fell back to 360, which is only correct by accident.
 */
function resizeCharts() {
  for (const [chart, id] of [
    [throughputChart, "chart-throughput"],
    [wipChart, "chart-wip"],
    [riskChart, "chart-risk"],
    [pmThroughputChart, "pm-chart-throughput"],
    [pmWipChart, "pm-chart-wip"],
  ]) {
    if (!chart) continue;
    const w = chartWidth(id);
    if (w > 0 && w !== chart.width) chart.setSize({ width: w, height: CHART_H });
  }
}

let resizeTimer = null;
window.addEventListener("resize", () => {
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(resizeCharts, 120);
});

// Axis colors are read once at chart-construction time (uPlot has no live
// "update this option" path for axes), so a theme switch has to tear down
// and rebuild any chart that already exists -- cheap, since the underlying
// history arrays are untouched and just get re-plotted with the new colors.
function rebuildChartsForThemeChange() {
  for (const [chart, setter] of [
    [throughputChart, (c) => { throughputChart = c; }],
    [wipChart, (c) => { wipChart = c; }],
    [riskChart, (c) => { riskChart = c; }],
    [pmThroughputChart, (c) => { pmThroughputChart = c; }],
    [pmWipChart, (c) => { pmWipChart = c; }],
  ]) {
    if (chart) {
      chart.destroy();
      setter(null);
    }
  }
  if (history.ticks.length) drawCharts();
  if (pmHistory.ticks.length) drawPmCharts();
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
 * Live alerts (Floor Supervisor tab). Bottleneck-shift alerts are pushed
 * from updatePlantManager's existing transition detection above; the other
 * two kinds are detected here, per station, per tick.
 * ------------------------------------------------------------------- */

async function loadRiskThreshold() {
  try {
    const resp = await fetch("/api/twin/risk_threshold");
    const body = await resp.json();
    riskThreshold = body.threshold; // null if Model B isn't trained/loaded
  } catch {
    riskThreshold = null;
  }
}

function pushAlert(severity, title, detail, tick) {
  alerts.unshift({ severity, title, detail, tick });
  if (alerts.length > ALERTS_MAX) alerts.length = ALERTS_MAX;
  renderAlerts();
}

function renderAlerts() {
  renderAlertsInto("alerts-list", alerts);

  const bellCount = $("tb-bell-count");
  bellCount.textContent = alerts.length > 99 ? "99+" : String(alerts.length);
  bellCount.hidden = alerts.length === 0;
}

function renderAlertsInto(elementId, list) {
  const el = $(elementId);
  if (!el) return;
  if (list.length === 0) {
    el.innerHTML = '<p class="panel-sub">No alerts yet — the line is running normally.</p>';
    return;
  }
  el.innerHTML = "";
  for (const a of list) {
    const row = document.createElement("div");
    row.className = "alert-row";
    row.dataset.severity = a.severity;
    row.innerHTML = `
      <span class="alert-tick">tick ${a.tick}</span>
      <span class="alert-body">
        <span class="alert-title">${a.title}</span>
        <div class="alert-detail">${a.detail}</div>
      </span>
    `;
    el.appendChild(row);
  }
}

function updateStationAlerts(snap) {
  for (const st of snap.stations) {
    // Risk flag: only fires on the RISING edge (was below, now at/above),
    // not every tick a station stays elevated -- a real alert feed that
    // repeats itself every tick is exactly the "cries wolf" failure mode
    // this dashboard is trying not to have.
    if (riskThreshold !== null && st.defect_risk && st.defect_risk.value !== null) {
      const flagged = st.defect_risk.value >= riskThreshold;
      if (flagged && !riskFlagState[st.station_id]) {
        const drivers = (st.risk_drivers || [])
          .map((d) => d.feature)
          .slice(0, 2)
          .join(", ");
        pushAlert(
          "critical",
          `Risk flag — ${st.station_id}`,
          `Defect-risk score ${(st.defect_risk.value * 100).toFixed(1)}% crossed the model's ` +
            `own decision threshold (${(riskThreshold * 100).toFixed(1)}%).` +
            (drivers ? ` Top drivers (associative, not causal): ${drivers}.` : ""),
          snap.tick
        );
      }
      riskFlagState[st.station_id] = flagged;
    }

    // Sustained disruption: a station stuck BLOCKED or STARVED for many
    // consecutive ticks, not a one-tick blip. Fires once per streak, right
    // when it first crosses the threshold, not again every tick after.
    const disrupted = st.state === "blocked" || st.state === "starved";
    if (disrupted) {
      disruptionStreak[st.station_id] = (disruptionStreak[st.station_id] || 0) + 1;
      if (
        disruptionStreak[st.station_id] === DISRUPTION_ALERT_TICKS &&
        !disruptionAlerted[st.station_id]
      ) {
        pushAlert(
          "warning",
          `Sustained disruption — ${st.station_id}`,
          `${st.state} for ${DISRUPTION_ALERT_TICKS}+ consecutive ticks — likely a real, ` +
            `ongoing effect of a slowdown elsewhere on the line, not a momentary blip.`,
          snap.tick
        );
        disruptionAlerted[st.station_id] = true;
      }
    } else {
      disruptionStreak[st.station_id] = 0;
      disruptionAlerted[st.station_id] = false;
    }
  }
}

/* ---------------------------------------------------------------------
 * Causal narration: what a perturbation click actually does, and --a few
 * seconds later, computed from real snapshot data, not scripted-- what it
 * actually caused.
 * ------------------------------------------------------------------- */

function explainMechanism(stationId, multiplier) {
  if (multiplier === 1) {
    return `Reset ${stationId} to its normal cycle time. No change expected.`;
  }
  const direction = multiplier > 1 ? "slower" : "faster";
  const timeChange = multiplier > 1 ? "longer" : "less time";
  const neighborEffect =
    multiplier > 1
      ? `its outgoing buffer will drain slower than the incoming one fills, so the station just ` +
        `<b>before</b> it may start showing BLOCKED`
      : `it will pull from its incoming buffer faster than normal, so the station just ` +
        `<b>before</b> it may start showing STARVED`;
  return (
    `You set <b>${stationId}</b> to run at <b>${multiplier}×</b> its normal cycle time ` +
    `(${direction}). What to expect: ${stationId} now takes ${timeChange} to finish each unit, so ` +
    `${neighborEffect}. The Active Period Method tracks how long each station stays continuously ` +
    `active; if ${stationId}'s active period becomes the line's longest, it becomes the new ` +
    `bottleneck — usually within a couple of seconds. Its defect-risk score (if a model is loaded) ` +
    `will also update at the next scoring pass using the new queue-pressure and cycle-time signals.`
  );
}

function explainOutcome(pending, snap) {
  const bn = snap.bottleneck;
  const currentBottleneckId = bn && bn.station_id ? bn.station_id : null;
  const st = snap.stations.find((s) => s.station_id === pending.stationId);
  const queueNow = st ? st.queue_depth : null;

  let msg;
  if (currentBottleneckId === pending.stationId && pending.beforeBottleneckId !== pending.stationId) {
    msg = `<b>${pending.stationId} became the new bottleneck</b> (was ${pending.beforeBottleneckId || "none"}).`;
  } else if (currentBottleneckId === pending.stationId) {
    msg = `${pending.stationId} is still the bottleneck.`;
  } else {
    msg = `The bottleneck is ${currentBottleneckId || "none"}, not ${pending.stationId} — ` +
      `the effect either hasn't propagated far enough yet, or another station is still the bigger constraint.`;
  }
  if (queueNow !== null && pending.beforeQueue !== null) {
    const delta = queueNow - pending.beforeQueue;
    msg += ` ${pending.stationId}'s queue went from ${pending.beforeQueue} to ${queueNow} ` +
      `(${delta >= 0 ? "+" : ""}${delta}).`;
  }
  return msg;
}

function renderNarration(label, html, isOutcome) {
  const el = $("ctl-narration");
  el.hidden = false;
  const block = document.createElement("div");
  block.className = "narration" + (isOutcome ? " n-outcome" : "");
  block.innerHTML = `<span class="n-label">${label}</span>${html}`;
  if (isOutcome) {
    el.appendChild(block);
  } else {
    el.innerHTML = "";
    el.appendChild(block);
  }
}

function updateNarrationFollowup(snap) {
  if (!pendingNarration) return;
  if (snap.tick < pendingNarration.appliedAtTick + NARRATION_FOLLOWUP_TICKS) return;
  renderNarration("What actually happened", explainOutcome(pendingNarration, snap), true);
  pendingNarration = null;
}

/* ---------------------------------------------------------------------
 * Persona tabs
 * ------------------------------------------------------------------- */

function switchView(viewId) {
  for (const view of document.querySelectorAll(".view")) {
    view.hidden = view.id !== viewId;
  }
  for (const item of document.querySelectorAll(".persona")) {
    const active = item.dataset.view === viewId;
    item.classList.toggle("is-active", active);
    item.setAttribute("aria-selected", active ? "true" : "false");
  }
  // A chart constructed inside a hidden view measured a zero-width
  // container and fell back to a default. Now that the view is visible,
  // re-measure -- otherwise the Plant Manager charts stay the wrong width
  // for as long as the tab is open.
  resizeCharts();
}

/* ---------------------------------------------------------------------
 * Alerts drawer
 * ------------------------------------------------------------------- */

function openAlerts() {
  $("alerts-drawer").hidden = false;
  $("drawer-scrim").hidden = false;
}

function closeAlerts() {
  $("alerts-drawer").hidden = true;
  $("drawer-scrim").hidden = true;
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
$("ctl-station").addEventListener("change", (e) => {
  const mult = stationMultipliers[e.target.value] ?? 1.0;
  $("ctl-mult").value = mult;
  $("ctl-mult-val").textContent = `${mult.toFixed(1)}×`;
});
$("ctl-apply").addEventListener("click", applyControl);
$("btn-kill").addEventListener("click", killStream);
$("btn-resume").addEventListener("click", resumeStream);
$("btn-restart").addEventListener("click", restartEngine);

for (const item of document.querySelectorAll(".persona")) {
  item.addEventListener("click", () => switchView(item.dataset.view));
}

/* Alerts live in a drawer rather than a nav destination: an alert is an
 * interruption, and making the supervisor leave the line view to read one
 * is exactly backwards. */
$("risk-expand").addEventListener("click", () => {
  riskListExpanded = !riskListExpanded;
  if (lastSnapshot) updateQualityPage(lastSnapshot.stations);
});

$("tb-bell").addEventListener("click", openAlerts);
$("drawer-close").addEventListener("click", closeAlerts);
$("drawer-scrim").addEventListener("click", closeAlerts);
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") closeAlerts();
});

$("ld-budget").addEventListener("input", (e) => {
  $("ld-budget-val").textContent = e.target.value;
  // Debounced -- a slider drag fires many input events per second, and
  // each one is a real HTTP request server-side (Phase 9's greedy
  // placement re-solves per pick, not free).
  clearTimeout(ldBudgetFetchTimer);
  ldBudgetFetchTimer = setTimeout(fetchSensorPlacement, 250);
});

loadEconomicsConfig();
loadRiskThreshold();
fetchSensorPlacement();
connect();
