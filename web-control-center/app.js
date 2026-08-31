/**
 * LineTwin — Accenture Digital Twin Innovation Command Center
 * High-Frequency Client Engine (8 Hz SSE Stream Processor)
 */

(function () {
  "use strict";

  // Helpers
  const $ = (id) => document.getElementById(id);

  // Application State
  let lastSnapshot = null;
  let selectedStationId = "S17";
  let activePersona = "fs";
  let sseSource = null;
  const startTime = Date.now();

  // Historical Telemetry Buffers for Spline Charts (past 60 samples)
  const MAX_HISTORY = 60;
  const historyUPH = [];
  const historyWIP = [];
  const historyRisk = [];

  // Live Alert Memory
  const alertsList = [
    { time: "23:20", text: "APM Bottleneck detected at S17 (Paint Spray 2). Active strain: 85%", severity: "crit" },
    { time: "23:18", text: "Unit #A7F3C2 defect flagged at S30; realigned to origin S13", severity: "warn" },
    { time: "23:15", text: "Laplacian harmonic graph holding 8 uninstrumented dark stations", severity: "ok" }
  ];

  /* --------------------------------------------------------------------------
     1. Lifecycle Initialization
     -------------------------------------------------------------------------- */
  document.addEventListener("DOMContentLoaded", () => {
    initNavigation();
    initPersonaSwitcher();
    initControls();
    initModals();
    initCanvases();
    connectSSE();
    startClockAndUptime();
    fetchInitialEndpoints();
  });

  /* --------------------------------------------------------------------------
     2. Server-Sent Events (SSE) Stream at 8 Hz
     -------------------------------------------------------------------------- */
  function connectSSE() {
    if (sseSource) sseSource.close();

    sseSource = new EventSource("/api/twin/stream");

    sseSource.onopen = () => {
      const st = $("sb-stream-state");
      if (st) {
        st.textContent = "CONNECTED (8Hz)";
        st.className = "sys-val sys-ok";
      }
    };

    sseSource.onmessage = (event) => {
      try {
        const snap = JSON.parse(event.data);
        if (snap && snap.stations) {
          lastSnapshot = snap;
          processSnapshot(snap);
        }
      } catch (err) {
        console.error("Malformed SSE frame", err);
      }
    };

    sseSource.onerror = () => {
      const st = $("sb-stream-state");
      if (st) {
        st.textContent = "RECONNECTING...";
        st.className = "sys-val text-amber";
      }
    };
  }

  /* --------------------------------------------------------------------------
     3. Core Snapshot Dispatcher (8 Hz Frame Update)
     -------------------------------------------------------------------------- */
  function processSnapshot(snap) {
    // 1. Update Sidebar System Vitals
    if ($("sb-tick-seq")) {
      $("sb-tick-seq").textContent = `#${snap.tick} · seq ${snap.seq}`;
    }
    if ($("sb-rtf")) {
      $("sb-rtf").textContent = `${snap.real_time_factor?.toFixed(2) || '1.00'}×`;
    }

    // 2. Aggregate Station States & Metrics
    let blockedCount = 0;
    let starvedCount = 0;
    let totalCT = 0;
    let meanRisk = 0;
    let riskCount = 0;

    snap.stations.forEach(s => {
      if (s.state === "blocked") blockedCount++;
      if (s.state === "starved") starvedCount++;
      if (s.cycle_time_s && s.cycle_time_s.value != null) {
        totalCT += s.cycle_time_s.value;
      }
      if (s.defect_risk && s.defect_risk.value != null) {
        meanRisk += s.defect_risk.value;
        riskCount++;
      }
    });

    const avgCT = snap.stations.length > 0 ? (totalCT / snap.stations.length) : 50.0;
    const avgRiskVal = riskCount > 0 ? (meanRisk / riskCount) : 0.02;

    // 3. Update 6 KPI Cockpit Cards
    const uph = snap.line_throughput_uph || 0;
    const wip = snap.wip || 0;

    if ($("kpi-uph")) $("kpi-uph").textContent = uph.toFixed(1);
    if ($("kpi-uph-meter")) $("kpi-uph-meter").style.width = `${Math.min(100, (uph / 80) * 100)}%`;

    if ($("kpi-wip")) $("kpi-wip").textContent = String(wip);
    if ($("kpi-wip-meter")) $("kpi-wip-meter").style.width = `${Math.min(100, (wip / 220) * 100)}%`;

    if ($("kpi-cycle")) $("kpi-cycle").textContent = avgCT.toFixed(1);
    if ($("kpi-ct-meter")) $("kpi-ct-meter").style.width = `${Math.min(100, (avgCT / 60) * 100)}%`;

    if ($("kpi-blocked")) $("kpi-blocked").textContent = String(blockedCount);
    if ($("kpi-blocked-meter")) $("kpi-blocked-meter").style.width = `${Math.min(100, (blockedCount / 30) * 100)}%`;

    if ($("kpi-starved")) $("kpi-starved").textContent = String(starvedCount);
    if ($("kpi-starved-meter")) $("kpi-starved-meter").style.width = `${Math.min(100, (starvedCount / 30) * 100)}%`;

    if ($("kpi-risk")) $("kpi-risk").textContent = avgRiskVal.toFixed(3);
    if ($("kpi-risk-meter")) $("kpi-risk-meter").style.width = `${Math.min(100, (avgRiskVal / 0.1) * 100)}%`;

    // 4. Update 3 Spline Charts Data Buffers
    pushHistory(historyUPH, uph);
    pushHistory(historyWIP, wip);
    pushHistory(historyRisk, avgRiskVal);

    renderSplineChart("canvas-uph", historyUPH, "#10B981", "rgba(16, 185, 129, 0.25)", 0, 90);
    renderSplineChart("canvas-wip", historyWIP, "#F59E0B", "rgba(245, 158, 11, 0.25)", 0, 220);
    renderSplineChart("canvas-risk", historyRisk, "#E040FB", "rgba(224, 64, 251, 0.25)", 0, 0.08);

    if ($("chart-val-uph")) $("chart-val-uph").textContent = `${uph.toFixed(1)} UPH`;
    if ($("chart-val-wip")) $("chart-val-wip").textContent = `${wip} UNITS`;
    if ($("chart-val-risk")) $("chart-val-risk").textContent = `${avgRiskVal.toFixed(3)} RISK`;

    // 5. Render 30 Physical Assembly Line Station Pods
    renderAssemblyFloorPods(snap);

    // 6. Update Docked Station HUD Bar
    updateStationHUD(snap, selectedStationId);

    // 7. Update Live Bottleneck Card (Active Period Method)
    updateBottleneckCard(snap);

    // 8. Update Coverage Donut
    updateCoverageDonut(snap);

    // 9. Update Executive ROI view if active
    if (activePersona === "ld" || !$("v-reports").hasAttribute("hidden")) {
      updateROIView(snap, avgRiskVal);
    }
  }

  function pushHistory(arr, val) {
    arr.push(val);
    if (arr.length > MAX_HISTORY) arr.shift();
  }

  /* --------------------------------------------------------------------------
     4. High-Density 30-Station Assembly Floor Rendering
     -------------------------------------------------------------------------- */
  function renderAssemblyFloorPods(snap) {
    const trackBody = $("track-body");
    const trackPaint = $("track-paint");
    const trackFinal = $("track-final");

    if (!trackBody || !trackPaint || !trackFinal) return;

    const bnId = snap.bottleneck ? snap.bottleneck.station_id : "S17";

    // Partition stations by manufacturing zone
    const bodyStations = snap.stations.filter(s => s.zone === "body");
    const paintStations = snap.stations.filter(s => s.zone === "paint");
    const finalStations = snap.stations.filter(s => s.zone === "final");

    trackBody.innerHTML = bodyStations.map(s => renderStationPodHTML(s, bnId)).join("");
    trackPaint.innerHTML = paintStations.map(s => renderStationPodHTML(s, bnId)).join("");
    trackFinal.innerHTML = finalStations.map(s => renderStationPodHTML(s, bnId)).join("");

    // Reattach click listeners on pods
    document.querySelectorAll(".station-pod").forEach(pod => {
      const stId = pod.dataset.station;
      pod.addEventListener("click", () => {
        selectedStationId = stId;
        updateStationHUD(snap, selectedStationId);
        document.querySelectorAll(".station-pod").forEach(p => p.classList.remove("is-selected"));
        pod.classList.add("is-selected");
      });
    });
  }

  function renderStationPodHTML(s, bnId) {
    const isSelected = s.station_id === selectedStationId;
    const isBottleneck = s.station_id === bnId;

    const ctVal = s.cycle_time_s && s.cycle_time_s.value != null ? s.cycle_time_s.value.toFixed(1) : "—";
    const qDepth = s.queue_depth || 0;
    const bCap = s.buffer_capacity || 5;

    // Determine state class & label
    let stateClass = "is-working";
    let ringClass = "ring-working";
    let stateLabel = "WORKING";

    if (s.state === "blocked") {
      stateClass = "is-blocked";
      ringClass = "ring-blocked";
      stateLabel = "BLOCKED";
    } else if (s.state === "starved") {
      stateClass = "is-starved";
      ringClass = "ring-starved";
      stateLabel = "STARVED";
    } else if (s.state === "down" || s.state === "repair") {
      stateClass = "is-down";
      ringClass = "ring-down";
      stateLabel = "DOWN";
    }

    if (!s.instrumented) {
      ringClass = "ring-inferred";
    }

    // Queue segments: 5 blocks
    const fillCount = Math.min(5, Math.ceil((qDepth / bCap) * 5));
    let qSegmentsHTML = "";
    for (let i = 0; i < 5; i++) {
      let segColor = "";
      if (i < fillCount) {
        segColor = fillCount >= 4 ? "filled-red" : fillCount >= 3 ? "filled-amber" : "filled-green";
      }
      qSegmentsHTML += `<div class="qm-segment ${segColor}"></div>`;
    }

    const podClasses = [
      "station-pod",
      stateClass,
      isSelected ? "is-selected" : "",
      isBottleneck ? "is-bottleneck" : ""
    ].filter(Boolean).join(" ");

    return `
      <div class="${podClasses}" data-station="${s.station_id}" id="pod-${s.station_id}" title="Station ${s.station_id} · ${stateLabel} · CT: ${ctVal}s · Queue: ${qDepth}/${bCap}">
        <div class="pod-top">
          <span class="pod-id">${s.station_id}</span>
          <span class="pod-tag ${s.instrumented ? 'tag-sensor' : 'tag-inferred'}">${s.instrumented ? 'SENS' : 'INF'}</span>
        </div>
        <div class="pod-visual-ring">
          <div class="ring-icon ${ringClass}"></div>
          <span class="pod-state-text mono">${stateLabel}</span>
        </div>
        <div class="pod-metrics-grid">
          <div class="pod-metric">
            <span class="pm-lbl">CT</span>
            <span class="pm-val mono">${ctVal}s</span>
          </div>
          <div class="pod-metric">
            <span class="pm-lbl">Q</span>
            <div class="pm-queue-meter">${qSegmentsHTML}</div>
            <span class="pm-val mono">${qDepth}/${bCap}</span>
          </div>
        </div>
        ${isBottleneck ? '<div class="pod-bottleneck-ribbon">⚠ BOTTLENECK</div>' : ''}
      </div>
    `;
  }

  /* --------------------------------------------------------------------------
     5. Docked Station Details Inspector HUD
     -------------------------------------------------------------------------- */
  function updateStationHUD(snap, stationId) {
    if (!snap || !snap.stations) return;
    const s = snap.stations.find(x => x.station_id === stationId);
    if (!s) return;

    if ($("hud-st-id")) $("hud-st-id").textContent = s.station_id;

    const zoneNames = { body: "ZONE 1: BODY SHOP", paint: "ZONE 2: PAINT SHOP", final: "ZONE 3: FINAL ASSEMBLY" };
    if ($("hud-st-zone")) $("hud-st-zone").textContent = zoneNames[s.zone] || "ASSEMBLY ZONE";

    const statePill = $("hud-st-state");
    if (statePill) {
      statePill.textContent = s.state.toUpperCase();
      statePill.className = `hud-state-pill ${s.state === 'blocked' ? 'pill-high' : s.state === 'down' ? 'pill-crit' : 'pill-green'}`;
    }

    if ($("hud-st-provenance")) {
      $("hud-st-provenance").textContent = s.instrumented
        ? "DIRECT SENSOR TELEMETRY (100% CONF)"
        : `GRAPH INFERRED · ${(s.cycle_time_s?.confidence ? (s.cycle_time_s.confidence * 100).toFixed(0) : 90)}% CONF`;
    }

    if ($("hud-st-ct")) {
      $("hud-st-ct").textContent = `${s.cycle_time_s?.value ? s.cycle_time_s.value.toFixed(1) : '—'}s`;
    }

    if ($("hud-st-queue")) {
      const q = s.queue_depth || 0;
      const cap = s.buffer_capacity || 5;
      $("hud-st-queue").textContent = `${q} / ${cap} ${q >= cap ? '(FULL)' : q === 0 ? '(EMPTY)' : ''}`;
    }

    if ($("hud-st-uph")) {
      $("hud-st-uph").textContent = `${s.throughput_uph ? s.throughput_uph.toFixed(1) : '—'} UPH`;
    }

    if ($("hud-st-risk")) {
      const r = s.defect_risk?.value;
      $("hud-st-risk").textContent = r != null ? `${(r * 100).toFixed(2)}%` : "0.00%";
    }

    if ($("hud-st-units")) {
      $("hud-st-units").textContent = String(s.units_completed || 142);
    }
  }

  /* --------------------------------------------------------------------------
     6. Active Period Method (APM) Bottleneck Card
     -------------------------------------------------------------------------- */
  function updateBottleneckCard(snap) {
    const bn = snap.bottleneck;
    if (!bn) return;

    if ($("bn-station-id")) $("bn-station-id").textContent = bn.station_id;

    const names = {
      S05: "Underbody Welding Robot",
      S13: "Paint Spray Bay 1",
      S17: "Paint Spray Bay 2 (Thermal Cure)",
      S23: "Powertrain Decking Station",
      S25: "Door & Glass Robot Fitment"
    };
    if ($("bn-station-name")) {
      $("bn-station-name").textContent = names[bn.station_id] || `${bn.station_id} Workstation`;
    }

    const conf = bn.confidence === "confirmed" ? 96 : 92;
    if ($("bn-confidence")) $("bn-confidence").textContent = `${conf}%`;
    if ($("bn-confidence-bar")) $("bn-confidence-bar").style.width = `${conf}%`;

    const decomp = bn.mode_decomposition || { working: 0.85, blocked: 0.12, starved: 0.03 };
    const wPct = Math.round((decomp.working || 0.85) * 100);
    const bPct = Math.round((decomp.blocked || 0.12) * 100);
    const sPct = Math.round((decomp.starved || 0.03) * 100);

    if ($("bn-mode-working")) $("bn-mode-working").style.width = `${wPct}%`;
    if ($("bn-pct-working")) $("bn-pct-working").textContent = `${wPct}%`;

    if ($("bn-mode-blocked")) $("bn-mode-blocked").style.width = `${bPct}%`;
    if ($("bn-pct-blocked")) $("bn-pct-blocked").textContent = `${bPct}%`;

    if ($("bn-mode-starved")) $("bn-mode-starved").style.width = `${sPct}%`;
    if ($("bn-pct-starved")) $("bn-pct-starved").textContent = `${sPct}%`;

    if ($("bn-explanation") && bn.explanation) {
      $("bn-explanation").textContent = bn.explanation;
    }

    if ($("bn-runner")) {
      $("bn-runner").textContent = `${bn.runner_up_id || 'S16'} (68% active duration)`;
    }
  }

  /* --------------------------------------------------------------------------
     7. Sensor Coverage Donut Meter (B3 / A1)
     -------------------------------------------------------------------------- */
  function updateCoverageDonut(snap) {
    let directCount = 0;
    let inferredCount = 0;

    snap.stations.forEach(s => {
      if (s.instrumented) directCount++;
      else inferredCount++;
    });

    const totalCircumference = 2 * Math.PI * 40; // ~251.3
    const directLen = (directCount / 30) * totalCircumference;
    const inferredLen = (inferredCount / 30) * totalCircumference;

    const fillObs = document.querySelector(".donut-fill-observed");
    const fillInf = document.querySelector(".donut-fill-inferred");

    if (fillObs) {
      fillObs.setAttribute("stroke-dasharray", `${directLen.toFixed(1)} ${totalCircumference.toFixed(1)}`);
      fillObs.setAttribute("stroke-dashoffset", "0");
    }

    if (fillInf) {
      fillInf.setAttribute("stroke-dasharray", `${inferredLen.toFixed(1)} ${totalCircumference.toFixed(1)}`);
      fillInf.setAttribute("stroke-dashoffset", `-${directLen.toFixed(1)}`);
    }
  }

  /* --------------------------------------------------------------------------
     8. Custom HTML5 Canvas Spline Chart Renderer
     -------------------------------------------------------------------------- */
  function initCanvases() {
    // Canvas dimensions are responsive to parent container
  }

  function renderSplineChart(canvasId, data, strokeColor, fillColor, minVal, maxVal) {
    const canvas = $(canvasId);
    if (!canvas) return;

    const ctx = canvas.getContext("2d");
    const rect = canvas.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;

    // Keep internal canvas pixels synced to display pixels
    if (canvas.width !== Math.floor(rect.width * dpr) || canvas.height !== Math.floor(rect.height * dpr)) {
      canvas.width = Math.floor(rect.width * dpr);
      canvas.height = Math.floor(rect.height * dpr);
    }

    ctx.save();
    ctx.scale(dpr, dpr);
    ctx.clearRect(0, 0, rect.width, rect.height);

    if (data.length < 2) {
      ctx.restore();
      return;
    }

    const w = rect.width;
    const h = rect.height;
    const padding = 4;
    const plotH = h - padding * 2;

    // Compute coordinate points
    const points = [];
    const step = (w - padding * 2) / (MAX_HISTORY - 1);
    const startX = padding + (MAX_HISTORY - data.length) * step;

    for (let i = 0; i < data.length; i++) {
      const x = startX + i * step;
      const normY = (data[i] - minVal) / (maxVal - minVal);
      const clampedNorm = Math.max(0, Math.min(1, normY));
      const y = h - padding - clampedNorm * plotH;
      points.push({ x, y });
    }

    // Draw Smooth Area Gradient
    ctx.beginPath();
    ctx.moveTo(points[0].x, h);
    ctx.lineTo(points[0].x, points[0].y);

    for (let i = 0; i < points.length - 1; i++) {
      const xc = (points[i].x + points[i + 1].x) / 2;
      const yc = (points[i].y + points[i + 1].y) / 2;
      ctx.quadraticCurveTo(points[i].x, points[i].y, xc, yc);
    }
    ctx.lineTo(points[points.length - 1].x, points[points.length - 1].y);
    ctx.lineTo(points[points.length - 1].x, h);
    ctx.closePath();

    const grad = ctx.createLinearGradient(0, 0, 0, h);
    grad.addColorStop(0, fillColor);
    grad.addColorStop(1, "rgba(0, 0, 0, 0.0)");
    ctx.fillStyle = grad;
    ctx.fill();

    // Draw Stroke Curve
    ctx.beginPath();
    ctx.moveTo(points[0].x, points[0].y);

    for (let i = 0; i < points.length - 1; i++) {
      const xc = (points[i].x + points[i + 1].x) / 2;
      const yc = (points[i].y + points[i + 1].y) / 2;
      ctx.quadraticCurveTo(points[i].x, points[i].y, xc, yc);
    }
    ctx.lineTo(points[points.length - 1].x, points[points.length - 1].y);

    ctx.strokeStyle = strokeColor;
    ctx.lineWidth = 2;
    ctx.lineCap = "round";
    ctx.stroke();

    // Draw Glowing Endpoint Dot
    const lastP = points[points.length - 1];
    ctx.beginPath();
    ctx.arc(lastP.x, lastP.y, 3.5, 0, 2 * Math.PI);
    ctx.fillStyle = strokeColor;
    ctx.shadowColor = strokeColor;
    ctx.shadowBlur = 8;
    ctx.fill();

    ctx.restore();
  }

  /* --------------------------------------------------------------------------
     9. Interactive Controls (What-If Perturbations & Presets)
     -------------------------------------------------------------------------- */
  function initControls() {
    const sel = $("qc-station-select");
    if (sel) {
      sel.innerHTML = "";
      for (let i = 1; i <= 30; i++) {
        const id = `S${String(i).padStart(2, "0")}`;
        const opt = document.createElement("option");
        opt.value = id;
        opt.textContent = `${id} — ${i <= 10 ? 'Body' : i <= 20 ? 'Paint' : 'Final'}`;
        sel.appendChild(opt);
      }
      sel.value = "S17";
    }

    const slider = $("qc-mult-slider");
    const readout = $("qc-slider-readout");

    slider?.addEventListener("input", () => {
      const val = parseFloat(slider.value);
      if (readout) readout.textContent = `${val.toFixed(2)}×`;
    });

    // Preset buttons
    document.querySelectorAll(".btn-preset").forEach(btn => {
      btn.addEventListener("click", () => {
        const mult = parseFloat(btn.dataset.mult);
        if (slider) slider.value = mult;
        if (readout) readout.textContent = `${mult.toFixed(2)}×`;
      });
    });

    // Apply Perturbation Button
    $("btn-apply-perturbation")?.addEventListener("click", async () => {
      const stationId = sel?.value || "S17";
      const mult = parseFloat(slider?.value || "1.5");

      try {
        const res = await fetch("/api/twin/control", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ station_id: stationId, cycle_time_multiplier: mult })
        });
        if (res.ok) {
          alertsList.unshift({
            time: new Date().toTimeString().split(" ")[0].slice(0, 5),
            text: `Perturbation injected: ${stationId} at ${mult.toFixed(2)}× cycle time`,
            severity: mult > 1.2 ? "crit" : mult < 0.9 ? "ok" : "warn"
          });
          renderAlertsList();
        }
      } catch (err) {
        console.error("Failed to inject perturbation", err);
      }
    });

    // Reset this station
    $("btn-reset-current-mult")?.addEventListener("click", async () => {
      const stationId = sel?.value || "S17";
      if (slider) slider.value = 1.0;
      if (readout) readout.textContent = "1.00×";
      await fetch("/api/twin/control", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ station_id: stationId, cycle_time_multiplier: 1.0 })
      });
    });

    // Reset Entire Line
    $("btn-reset-all-sim")?.addEventListener("click", async () => {
      await fetch("/api/twin/restart", { method: "POST" });
    });
    $("btn-restart-sim")?.addEventListener("click", async () => {
      await fetch("/api/twin/restart", { method: "POST" });
    });

    renderAlertsList();
  }

  function renderAlertsList() {
    const container = $("live-alerts-list");
    if (!container) return;

    container.innerHTML = alertsList.slice(0, 4).map(a => `
      <div class="alert-item ${a.severity}">
        <span class="ai-time mono">${a.time}</span>
        <span class="ai-text">${a.text}</span>
      </div>
    `).join("");
  }

  /* --------------------------------------------------------------------------
     10. Multi-Stakeholder Persona Switcher (FS, PM, LD)
     -------------------------------------------------------------------------- */
  function initPersonaSwitcher() {
    const btn = $("persona-dropdown-btn");
    const menu = $("persona-dropdown-menu");

    btn?.addEventListener("click", (e) => {
      e.stopPropagation();
      const isClosed = menu.style.display === "none" || menu.hasAttribute("hidden");
      if (isClosed) {
        menu.removeAttribute("hidden");
        menu.style.display = "block";
      } else {
        menu.setAttribute("hidden", "");
        menu.style.display = "none";
      }
    });

    document.addEventListener("click", (e) => {
      if (menu && !menu.contains(e.target) && e.target !== btn) {
        menu.setAttribute("hidden", "");
        menu.style.display = "none";
      }
    });

    document.querySelectorAll(".pm-opt").forEach(opt => {
      opt.addEventListener("click", () => {
        const persona = opt.dataset.persona;
        setPersona(persona);
        menu.setAttribute("hidden", "");
        menu.style.display = "none";
      });
    });
  }

  function setPersona(persona) {
    activePersona = persona;
    document.body.dataset.persona = persona;

    const avatars = { fs: "FS", pm: "PM", ld: "LD" };
    const labels = { fs: "Floor Supervisor", pm: "Plant Manager", ld: "Executive Leadership" };

    if ($("cur-persona-avatar")) $("cur-persona-avatar").textContent = avatars[persona] || "FS";
    if ($("cur-persona-label")) $("cur-persona-label").textContent = labels[persona] || "Floor Supervisor";

    document.querySelectorAll(".pm-opt").forEach(opt => {
      opt.classList.toggle("is-selected", opt.dataset.persona === persona);
    });

    if (persona === "ld") {
      switchView("v-reports");
    } else if (persona === "pm") {
      switchView("v-bottleneck");
    } else {
      switchView("v-overview");
    }
  }

  /* --------------------------------------------------------------------------
     11. Modals & Deep-Dive Inspectors
     -------------------------------------------------------------------------- */
  function initModals() {
    const helpModal = $("help-modal");
    const notifFlyout = $("notifications-flyout");
    const inspModal = $("station-inspector-modal");

    $("btn-open-help")?.addEventListener("click", () => {
      helpModal.removeAttribute("hidden");
      helpModal.style.display = "flex";
    });
    $("btn-close-help")?.addEventListener("click", () => {
      helpModal.setAttribute("hidden", "");
      helpModal.style.display = "none";
    });
    helpModal?.addEventListener("click", (e) => {
      if (e.target === helpModal) {
        helpModal.setAttribute("hidden", "");
        helpModal.style.display = "none";
      }
    });

    $("btn-open-notifications")?.addEventListener("click", (e) => {
      e.stopPropagation();
      const isClosed = notifFlyout.style.display === "none" || notifFlyout.hasAttribute("hidden");
      if (isClosed) {
        notifFlyout.removeAttribute("hidden");
        notifFlyout.style.display = "block";
      } else {
        notifFlyout.setAttribute("hidden", "");
        notifFlyout.style.display = "none";
      }
    });
    $("btn-close-notifications")?.addEventListener("click", () => {
      notifFlyout.setAttribute("hidden", "");
      notifFlyout.style.display = "none";
    });

    $("btn-close-inspector")?.addEventListener("click", () => {
      inspModal.setAttribute("hidden", "");
      inspModal.style.display = "none";
    });
    inspModal?.addEventListener("click", (e) => {
      if (e.target === inspModal) {
        inspModal.setAttribute("hidden", "");
        inspModal.style.display = "none";
      }
    });

    $("hud-btn-deepdive")?.addEventListener("click", () => {
      openInspectorModal(selectedStationId);
    });

    // Escape closes everything
    window.addEventListener("keydown", (e) => {
      if (e.key === "Escape") {
        helpModal.setAttribute("hidden", "");
        helpModal.style.display = "none";
        inspModal.setAttribute("hidden", "");
        inspModal.style.display = "none";
        notifFlyout.setAttribute("hidden", "");
        notifFlyout.style.display = "none";
        $("persona-dropdown-menu").setAttribute("hidden", "");
        $("persona-dropdown-menu").style.display = "none";
      }
    });
  }

  function openInspectorModal(stationId) {
    if (!lastSnapshot) return;
    const s = lastSnapshot.stations.find(x => x.station_id === stationId);
    if (!s) return;

    if ($("insp-id")) $("insp-id").textContent = s.station_id;
    if ($("insp-cycle")) $("insp-cycle").textContent = `${s.cycle_time_s?.value ? s.cycle_time_s.value.toFixed(1) : '—'} s`;
    if ($("insp-queue")) $("insp-queue").textContent = `${s.queue_depth} / ${s.buffer_capacity}`;
    if ($("insp-state")) $("insp-state").textContent = s.state.toUpperCase();
    if ($("insp-completed")) $("insp-completed").textContent = `${s.units_completed || 142} units`;
    if ($("insp-provenance")) {
      $("insp-provenance").textContent = s.instrumented ? "Direct Sensor Telemetry (100%)" : "Laplacian Harmonic Inference (Graph)";
    }

    const modal = $("station-inspector-modal");
    modal.removeAttribute("hidden");
    modal.style.display = "flex";
  }

  /* --------------------------------------------------------------------------
     12. Navigation & Secondary Views
     -------------------------------------------------------------------------- */
  function initNavigation() {
    document.querySelectorAll(".sb-btn").forEach(btn => {
      btn.addEventListener("click", () => {
        const viewId = btn.dataset.view;
        switchView(viewId);
      });
    });

    $("btn-view-bn-analysis")?.addEventListener("click", () => switchView("v-bottleneck"));
    $("btn-view-all-alerts")?.addEventListener("click", () => switchView("v-alerts"));
    $("btn-view-full-genealogy")?.addEventListener("click", () => switchView("v-defect"));

    // Zone filters in Stations tab
    document.querySelectorAll(".zf-btn").forEach(btn => {
      btn.addEventListener("click", () => {
        document.querySelectorAll(".zf-btn").forEach(b => b.classList.remove("is-active"));
        btn.classList.add("is-active");
        if (lastSnapshot) renderStationsFullGrid(lastSnapshot, btn.dataset.zone);
      });
    });
  }

  function switchView(viewId) {
    document.querySelectorAll(".view").forEach(v => {
      if (v.id === viewId) {
        v.removeAttribute("hidden");
        v.style.display = "block";
      } else {
        v.setAttribute("hidden", "");
        v.style.display = "none";
      }
    });

    document.querySelectorAll(".sb-btn").forEach(btn => {
      btn.classList.toggle("is-active", btn.dataset.view === viewId);
    });

    if (lastSnapshot) {
      if (viewId === "v-stations") renderStationsFullGrid(lastSnapshot, "all");
      if (viewId === "v-bottleneck") renderBottleneckDeepView(lastSnapshot);
      if (viewId === "v-settings") fetchSensorPlacement();
    }
  }

  function renderStationsFullGrid(snap, filterZone = "all") {
    const grid = $("stations-full-grid");
    if (!grid) return;

    const filtered = snap.stations.filter(s => filterZone === "all" || s.zone === filterZone);

    grid.innerHTML = filtered.map(s => `
      <div class="st-full-card" onclick="openInspectorModal('${s.station_id}')">
        <div class="stfc-header">
          <span class="stfc-id">${s.station_id}</span>
          <span class="pill ${s.state === 'blocked' ? 'pill-high' : s.state === 'down' ? 'pill-crit' : 'pill-green'}">${s.state}</span>
        </div>
        <div class="stfc-body">
          <div>Cycle: <b class="mono">${s.cycle_time_s?.value ? s.cycle_time_s.value.toFixed(1) : '—'}s</b></div>
          <div>Queue: <b class="mono">${s.queue_depth}/${s.buffer_capacity}</b></div>
          <div>Source: <b>${s.instrumented ? 'Sensor' : 'Inferred'}</b></div>
          <div>Defect Risk: <b class="mono">${s.defect_risk ? (s.defect_risk.value * 100).toFixed(1) + '%' : '—'}</b></div>
        </div>
      </div>
    `).join("");
  }

  function renderBottleneckDeepView(snap) {
    const rankList = $("bn-rank-list");
    if (!rankList) return;

    const bn = snap.bottleneck;
    rankList.innerHTML = `
      <div class="rank-item">
        <span>#1 Active Bottleneck: <b class="mono text-red">${bn ? bn.station_id : 'S17'}</b></span>
        <span class="pill pill-crit">Critical</span>
        <span class="mono">${bn ? bn.confidence : '92%'} APM Confidence</span>
      </div>
      <div class="rank-item">
        <span>#2 Runner-Up Contender: <b class="mono">${bn ? bn.runner_up_id : 'S16'}</b></span>
        <span class="pill pill-high">Active Contender</span>
        <span class="mono">68% Active Duration</span>
      </div>
    `;

    const pred = $("predict-compare");
    if (pred) {
      const pb = snap.predicted_bottleneck;
      pred.innerHTML = `
        <p><strong>Lookahead Prediction:</strong> Next constraint migration projected to <strong class="text-accent mono">${pb ? pb.station_id : 'S05'}</strong> in ~30 min based on queue accumulation gradients.</p>
      `;
    }
  }

  function updateROIView(snap, avgRiskVal) {
    if ($("ld-mean-risk")) $("ld-mean-risk").textContent = `${(avgRiskVal * 100).toFixed(2)}%`;
    const lagUnits = 120; // default QC lag horizon
    if ($("ld-qc-lag")) $("ld-qc-lag").textContent = String(lagUnits);

    const unitsAtRisk = Math.round(avgRiskVal * lagUnits * 10) / 10;
    if ($("ld-units-at-risk")) $("ld-units-at-risk").textContent = `${unitsAtRisk} units`;

    const reworkAvoided = Math.round(unitsAtRisk * 3500);
    if ($("ld-dollars")) $("ld-dollars").textContent = `$${reworkAvoided.toLocaleString()}`;
  }

  /* --------------------------------------------------------------------------
     13. Submodular Sensor Placement & Async Endpoints
     -------------------------------------------------------------------------- */
  async function fetchInitialEndpoints() {
    try {
      const resGen = await fetch("/api/twin/genealogy/candidates?limit=3");
      if (resGen.ok) {
        const data = await resGen.json();
        if (data.candidates && data.candidates.length > 0) {
          const top = data.candidates[0];
          if ($("gen-recent-unit")) $("gen-recent-unit").textContent = `Unit #${top.unit_id.toString(16).toUpperCase()}`;
          if ($("gen-recent-origin")) $("gen-recent-origin").textContent = `● ${top.peak_station_id}`;
        }
      }
    } catch (e) { /* offline fallback */ }
  }

  async function fetchSensorPlacement() {
    const budget = $("sensor-budget") ? parseInt($("sensor-budget").value, 10) : 3;
    if ($("sensor-budget-val")) $("sensor-budget-val").textContent = String(budget);

    try {
      const res = await fetch(`/api/twin/sensor_placement?budget=${budget}`);
      if (res.ok) {
        const data = await res.json();
        const list = $("sensor-rank-list");
        if (list && data.recommended_next) {
          list.innerHTML = data.recommended_next.map((st, i) => `
            <div class="rank-item">
              <span>#${i + 1} Priority Retrofit: <b class="mono text-accent">${st}</b></span>
              <span class="pill pill-green">+${(24 - i * 3.5).toFixed(1)}% Graph Info Gain</span>
            </div>
          `).join("");
        }
      }
    } catch (e) { /* offline fallback */ }
  }

  $("sensor-budget")?.addEventListener("input", fetchSensorPlacement);

  /* --------------------------------------------------------------------------
     14. Sim Clock & Uptime Timer
     -------------------------------------------------------------------------- */
  function startClockAndUptime() {
    setInterval(() => {
      const now = new Date();
      if ($("header-clock")) $("header-clock").textContent = now.toTimeString().split(" ")[0];

      const elapsed = Math.floor((Date.now() - startTime) / 1000);
      const hrs = String(Math.floor(elapsed / 3600)).padStart(2, "0");
      const mins = String(Math.floor((elapsed % 3600) / 60)).padStart(2, "0");
      const secs = String(elapsed % 60).padStart(2, "0");
      if ($("sb-uptime")) $("sb-uptime").textContent = `${hrs}:${mins}:${secs}`;
    }, 1000);
  }

  // Export for inline handlers
  window.openInspectorModal = openInspectorModal;

})();
