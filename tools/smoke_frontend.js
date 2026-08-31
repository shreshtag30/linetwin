/* Headless smoke test for web/app.js.
 *
 * There is no browser automation in this project's toolchain, and the cost of
 * that showed up concretely: a range-based edit silently deleted the whole
 * `updateKpis` function, so every tick threw partway through renderSnapshot.
 * The dashboard still LOOKED alive -- the masthead, the line map and the
 * constraint card all render before the throw -- while the station dropdown,
 * every chart and the entire Plant Manager view stayed empty. Nothing in
 * `node --check` catches that, because the file parses fine.
 *
 * This stubs just enough DOM for app.js to run, feeds it a real snapshot from
 * the live server (or a fixture), and fails if anything throws or if the
 * elements a working tick must populate are still empty afterwards.
 *
 * Usage:  node tools/smoke_frontend.js [http://127.0.0.1:8000]
 *         node tools/smoke_frontend.js --fixture
 */

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const ROOT = path.resolve(__dirname, "..");
const HTML = fs.readFileSync(path.join(ROOT, "web", "index.html"), "utf8");
const APP = fs.readFileSync(path.join(ROOT, "web", "app.js"), "utf8");

const ids = new Set([...HTML.matchAll(/id="([A-Za-z0-9_-]+)"/g)].map((m) => m[1]));

const thrown = [];
const nodes = new Map();

function makeEl(id) {
  const el = {
    id,
    _text: "",
    _html: "",
    dataset: {},
    style: {},
    classList: {
      _s: new Set(),
      add(...c) { c.forEach((x) => this._s.add(x)); },
      remove(...c) { c.forEach((x) => this._s.delete(x)); },
      toggle(c, on) { if (on === undefined) { this._s.has(c) ? this._s.delete(c) : this._s.add(c); } else if (on) this._s.add(c); else this._s.delete(c); },
      contains(c) { return this._s.has(c); },
    },
    hidden: false,
    disabled: false,
    value: "1.0",
    children: [],
    get textContent() { return this._text; },
    set textContent(v) { this._text = String(v); },
    get innerHTML() { return this._html; },
    set innerHTML(v) { this._html = String(v); },
    appendChild(c) { this.children.push(c); this._html += (c._html || "") + (c._text || ""); return c; },
    addEventListener() {},
    removeEventListener() {},
    setAttribute() {},
    getAttribute() { return null; },
    querySelector() { return makeEl("_q"); },
    querySelectorAll() { return []; },
    scrollIntoView() {},
    getBoundingClientRect() { return { width: 360, height: 150 }; },
    clientWidth: 360,
    closest() { return null; },
    focus() {},
  };
  return el;
}

function $(id) {
  if (!nodes.has(id)) nodes.set(id, makeEl(id));
  return nodes.get(id);
}

const document = {
  documentElement: { getAttribute: () => null, setAttribute: () => {}, style: {} },
  body: { classList: makeEl("body").classList },
  getElementById(id) {
    // Mirror the browser: an id that is NOT in index.html returns null, so a
    // stale reference surfaces here exactly as it would in the page.
    if (!ids.has(id)) return null;
    return $(id);
  },
  querySelectorAll() { return []; },
  querySelector() { return null; },
  createElement() { return makeEl("_new"); },
  addEventListener() {},
};

let esInstance = null;
class EventSource {
  constructor() { this.listeners = {}; this.readyState = 1; esInstance = this; }
  addEventListener(t, fn) { (this.listeners[t] ||= []).push(fn); }
  close() { this.readyState = 2; }
  emit(t, data) { for (const fn of this.listeners[t] || []) fn({ data: JSON.stringify(data) }); }
}
EventSource.CLOSED = 2;

function uPlot() {
  return { setData() {}, setSize() {}, destroy() {}, width: 360 };
}

const sandbox = {
  document,
  window: { addEventListener() {}, matchMedia: () => ({ matches: false }) },
  EventSource,
  uPlot,
  console,
  localStorage: { getItem: () => null, setItem() {}, removeItem() {} },
  getComputedStyle: () => ({ getPropertyValue: () => "#000000" }),
  setTimeout, clearTimeout, setInterval, clearInterval,
  fetch: async () => ({ ok: true, status: 200, json: async () => ({
    threshold: 0.1, qc_lag_units: 120, rework_cost_delta_usd: 250,
    recommended_next: [], dark_stations: [], candidates: [],
  }) }),
  Math, JSON, Date, Object, Array, String, Number, Boolean, Promise, Error,
  parseInt, parseFloat, isNaN,
};
sandbox.globalThis = sandbox;
sandbox.window.document = document;

async function main() {
  const arg = process.argv[2] || "http://127.0.0.1:8000";
  let snap;
  if (arg === "--fixture") {
    const fx = JSON.parse(fs.readFileSync(path.join(ROOT, "fixtures", "replay_30x60.json"), "utf8"));
    snap = fx.snapshots[fx.snapshots.length - 1];
  } else {
    const res = await fetch(`${arg}/api/twin/state`);
    snap = await res.json();
  }

  const ctx = vm.createContext(sandbox);
  try {
    vm.runInContext(APP, ctx, { filename: "app.js" });
  } catch (e) {
    console.error("FAIL: app.js threw at load time\n", e);
    process.exit(1);
  }

  // Drive one run_meta + three snapshots, exactly as the server would.
  const origError = console.error;
  console.error = (...a) => { thrown.push(a.join(" ")); origError(...a); };
  try {
    esInstance.emit("run_meta", {
      schema_version: snap.schema_version, run_id: "smoke-run-0001", seed: 1,
      scenario: "line30", station_count: snap.stations.length,
      instrumented_count: snap.stations.filter((s) => s.instrumented).length,
      sim_dt: 7.5, real_dt: 0.125, started_at_unix: 0,
    });
    for (let i = 0; i < 3; i++) esInstance.emit("snapshot", { ...snap, tick: snap.tick + i, seq: snap.seq + i });
  } catch (e) {
    console.error("FAIL: renderSnapshot threw\n", e);
    process.exit(1);
  }

  // Every element a healthy tick MUST populate. Each of these was empty in the
  // bug this harness exists to catch.
  const required = [
    "v-tick", "v-seq", "v-simtime", "v-rtf",
    "kpi-throughput", "kpi-wip", "kpi-cycle", "kpi-blocked", "kpi-starved", "kpi-risk",
    "bn-station", "ev-note", "ev-list",
    "floor-map", "zones", "risk-list",
    "coverage-pct", "coverage-legend",
    "ld-station-count", "ld-instrumented-count", "ld-dark-count",
    "pm-frequency", "ctl-station",
  ];

  const empty = required.filter((id) => {
    const el = document.getElementById(id);
    if (!el) return true;
    return !(el._text || "").trim() && !(el._html || "").trim();
  });

  if (empty.length) {
    console.error("FAIL: these elements were never populated by a tick:");
    for (const id of empty) console.error("   #" + id);
    process.exit(1);
  }

  console.log("PASS: 3 ticks rendered, all " + required.length + " required elements populated");
  console.log("  bottleneck   " + document.getElementById("bn-station")._text);
  console.log("  throughput   " + document.getElementById("kpi-throughput")._text);
  console.log("  stations in dropdown  " + (document.getElementById("ctl-station").children.length || "n/a"));
}

main().catch((e) => { console.error("FAIL:", e); process.exit(1); });
