/* Local Renode console. Bounded history, one socket and one polling request. */
"use strict";
const $ = (id) => document.getElementById(id);
const state = {view: "overview", catalog: [], selected: new Set(), cards: new Map(),
  reading: null, health: null, stats: {}, history: [], alerts: [], lab: null,
  token: null, ready: false, pending: false, socket: null, backoff: 1000,
  connected: false, lastFrame: 0, dirty: true, reportKey: "", eventKey: "", poll: null};
const titles = {
  overview: ["Live overview", "Follow the complete path from sensor registers to the dashboard."],
  tests: ["Component tests", "Choose a component, inspect its expected behavior, and run a repeatable check."],
  reports: ["Run reports", "Review the evidence from automated tests and manual demonstrations."],
  coverage: ["Model coverage", "See what each digital model verifies and where real hardware is still needed."]};
const fmt = (value, places = 2) => Number.isFinite(value) ? value.toFixed(places) : "—";
const stamp = (value) => value ? new Date(value).toLocaleString() : "—";
const chartTheme = (() => {
  const css = getComputedStyle(document.documentElement);
  return Object.fromEntries(["muted", "chart-grid", "axis-x", "axis-y", "axis-z", "teal"]
    .map(name => [name, css.getPropertyValue(`--${name}`).trim()]));
})();
function set(id, value) { const node = $(id); const next = String(value ?? "—"); if (node.textContent !== next) node.textContent = next; }
function node(tag, text, className) { const el = document.createElement(tag); if (text !== undefined) el.textContent = text; if (className) el.className = className; return el; }
function badge(el, status) { el.textContent = status.replaceAll("_", " ").toUpperCase(); el.className = `badge ${status}`; }
function notice(message, error = false) { $("notice").hidden = !message; $("notice").textContent = message; $("notice").classList.toggle("error", error); }
async function api(path, options) {
  const response = await fetch(path, {cache: "no-store", signal: AbortSignal.timeout(25000), ...options});
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(typeof body.detail === "string" ? body.detail : `Request failed (${response.status})`);
  }
  return response.json();
}
function reportLink(id, format = "html") { return `/api/v1/lab/reports/${encodeURIComponent(id)}?format=${format}`; }
function view(name) {
  state.view = name;
  document.querySelectorAll("[data-panel]").forEach(el => { el.hidden = el.dataset.panel !== name; });
  document.querySelectorAll("[data-view]").forEach(el => {
    if (el.dataset.view === name) el.setAttribute("aria-current", "page"); else el.removeAttribute("aria-current");
  });
  set("page-title", titles[name][0]); set("page-description", titles[name][1]); state.dirty = true;
}
function controls() {
  const busy = state.pending || state.lab?.active || !state.ready || !state.connected;
  document.querySelectorAll("[data-action], [data-run], #run-all, #run-selected, #show-acceleration, #acceleration-restore").forEach(el => { el.disabled = busy; });
  $("cancel-run").disabled = state.pending || state.lab?.cancel_requested || !state.connected;
}
async function act(path, body) {
  if (state.pending) return;
  state.pending = true; controls(); notice("");
  try {
    const data = await api(path, {method: "POST", headers: {"content-type": "application/json", "x-demo-token": state.token}, body: JSON.stringify(body)});
    notice(data.message || "Test run started. Progress and results appear above.");
    return data;
  } catch (error) { notice(error.message, true); }
  finally { state.pending = false; await pollNow(); controls(); }
}
function run(ids) { if (!ids.length) return notice("Select at least one test.", true); act("/api/v1/lab/run", {cases: ids}); }
async function showAcceleration() {
  if (state.pending || state.lab?.active || !state.ready || !state.connected) return;
  view("overview"); $("plot-metric").value = "acceleration"; state.dirty = true;
  $("acceleration-check").hidden = false; $("acceleration-check").classList.remove("error");
  $("acceleration-report").hidden = true;
  set("acceleration-result", "Applying test values through Renode…");
  set("acceleration-observed", "Waiting for a verified firmware sample.");
  $("acceleration").scrollIntoView({block: "center", behavior: "smooth"});
  const result = await act("/api/v1/demo/action", {action: "motion"});
  if (!result) {
    set("acceleration-result", "Acceleration demonstration did not complete");
    $("acceleration-check").classList.add("error");
    return;
  }
  const observed = result.reading.accel_g;
  const passed = Array.isArray(observed) && observed.length === 3 &&
    observed.every((value, index) => value === [0.5, -0.25, 0.75][index]);
  set("acceleration-result", passed ? "PASS · Acceleration verified from ARM firmware" : "FAIL · Unexpected acceleration");
  $("acceleration-check").classList.toggle("error", !passed);
  set("acceleration-observed", `Recorded: ${observed?.map(value => fmt(value, 3)).join(" / ") || "unavailable"} g · Boot ${result.reading.boot_id} · ${stamp(result.reading.received_at)}`);
  $("acceleration-report").href = reportLink(result.report_id); $("acceleration-report").hidden = false;
  notice(passed ? "Acceleration verified and held on the live instruments. Restore / stationary returns to 0 / 0 / 1 g." : "The recorded acceleration does not match the expected values.", !passed);
}
function makeCatalog(catalog) {
  if (state.catalog.length) return;
  state.catalog = catalog; state.selected = new Set(catalog.map(c => c.id));
  set("catalog-count", catalog.length);
  for (const component of new Set(catalog.map(c => c.component))) {
    const option = node("option", component); option.value = component; $("component-filter").append(option);
  }
  for (const item of catalog) {
    const card = node("article", undefined, "test-card"); card.dataset.case = item.id;
    const top = node("div", undefined, "test-top"), input = node("input"), label = node("label", item.title), status = node("span");
    input.type = "checkbox"; input.id = `select-${item.id}`; input.checked = true; label.htmlFor = input.id;
    input.addEventListener("change", () => { input.checked ? state.selected.add(item.id) : state.selected.delete(item.id); selection(); });
    badge(status, "pending"); top.append(input, label, status);
    const details = node("details"), evidence = node("pre"), method = node("p", item.method);
    details.append(node("summary", "Method & observed checks"), method, evidence);
    const error = node("div", "", "result-text"), bottom = node("div", undefined, "test-bottom"), duration = node("small", "Not run in this session"), button = node("button", "Run test", "secondary");
    button.dataset.run = item.id; button.addEventListener("click", () => run([item.id]));
    bottom.append(duration, button);
    card.append(top, node("div", item.component, "component"), node("p", item.expected), details, error, bottom);
    $("test-list").append(card); state.cards.set(item.id, {card, input, status, evidence, error, duration, signature: ""});
  }
  selection();
}
function selection() {
  const component = $("component-filter").value;
  const visible = state.catalog.filter(c => !component || c.component === component);
  for (const item of state.catalog) state.cards.get(item.id).card.hidden = !!component && item.component !== component;
  const count = visible.filter(c => state.selected.has(c.id)).length;
  $("select-all").checked = count === visible.length;
  $("select-all").indeterminate = count > 0 && count < visible.length;
  set("run-selected", `Run selected tests (${count})`);
}
function showResults(data) {
  const report = data.run; $("run-banner").hidden = !report;
  if (!report) return;
  const done = report.cases.filter(c => !["running", "pending"].includes(c.status)).length;
  const active = report.cases.find(c => c.status === "running");
  const passed = report.cases.filter(c => c.status === "passed").length;
  const failed = report.cases.filter(c => c.status === "failed").length;
  badge($("run-state"), report.status);
  set("run-heading", `${report.kind === "manual" ? "Demonstration" : report.kind === "preflight" ? "Startup preflight" : "Component test run"} · ${report.cases.length} ${report.cases.length === 1 ? "case" : "cases"}`);
  set("run-detail", data.cancel_requested && data.active ? "Stopping safely and restoring the board…" : active ? active.title : `${passed} passed · ${failed} failed · ${report.cases.filter(c => c.status === "cancelled").length} cancelled`);
  $("run-progress").max = report.cases.length; $("run-progress").value = done;
  set("progress-text", `${done} / ${report.cases.length} complete`);
  $("cancel-run").hidden = !data.active || report.kind === "manual";
  $("current-report").hidden = data.active; $("current-report").href = reportLink(report.id);
  // Retain the most recent result per case when a later subset is run.
  if (report.kind !== "manual") for (const item of report.cases) {
    const ui = state.cards.get(item.id); if (!ui) continue;
    const signature = JSON.stringify(item); if (ui.signature === signature) continue;
    ui.signature = signature; ui.card.className = `test-card ${item.status}`; badge(ui.status, item.status);
    ui.evidence.textContent = item.checks.map(c => `${c.passed ? "PASS" : "FAIL"} · ${c.name}\nExpected: ${JSON.stringify(c.expected)}\nObserved: ${JSON.stringify(c.actual)}`).join("\n\n");
    ui.error.textContent = item.error || "";
    ui.duration.textContent = item.duration_seconds == null ? "Waiting for execution" : `${item.duration_seconds.toFixed(2)} s · ${stamp(item.finished_at)}`;
  }
}
function showReports(reports) {
  const key = JSON.stringify(reports) + (state.lab?.active ? state.lab.run?.id : "");
  if (key === state.reportKey) return; state.reportKey = key;
  const fragment = document.createDocumentFragment();
  for (const report of reports) {
    const row = node("article", undefined, "report-row"), status = node("span");
    badge(status, report.status === "running" && (!state.lab.active || report.id !== state.lab.run?.id) ? "incomplete" : report.status);
    const info = node("div", undefined, "report-info");
    info.append(node("h3", `${report.kind} · ${stamp(report.started_at)}`), node("p", `${report.counts.passed} passed · ${report.counts.failed} failed · ${report.counts.cancelled} cancelled · ${report.id}`));
    const links = node("div", undefined, "report-actions");
    for (const format of ["html", "json"]) { const link = node("a", format.toUpperCase(), "button secondary"); link.href = reportLink(report.id, format); link.setAttribute("aria-label", `Download ${format.toUpperCase()} report ${report.id}`); links.append(link); }
    row.append(status, info, links); fragment.append(row);
  }
  if (!reports.length) fragment.append(node("p", "Reports will appear after the startup preflight.", "card"));
  $("report-list").replaceChildren(fragment);
}
function showCoverage(coverage) {
  if ($("coverage-list").childElementCount) return;
  for (const item of coverage) {
    const card = node("article", undefined, "card coverage-card"), level = node("span", item.level, "badge neutral");
    card.append(node("h3", item.name), level, node("p", item.limit));
    if (item.tests.length) {
      const button = node("button", `Select ${item.tests.length} related tests`, "secondary");
      button.addEventListener("click", () => {
        state.selected = new Set(item.tests); for (const [id, ui] of state.cards) ui.input.checked = state.selected.has(id);
        $("component-filter").value = ""; selection(); view("tests");
      }); card.append(button);
    }
    $("coverage-list").append(card);
  }
}
function addAlerts(alerts) {
  const known = new Set(state.alerts.map(a => a.id));
  state.alerts = [...(alerts || []).filter(a => !known.has(a.id)), ...state.alerts].sort((a,b) => b.created_at.localeCompare(a.created_at)).slice(0, 8);
}
function addReading(reading, health) {
  if (!reading) return;
  if (state.reading?.device_id !== reading.device_id || state.reading?.boot_id !== reading.boot_id) state.history = [];
  const old = state.reading;
  if (!old || old.device_id !== reading.device_id || old.boot_id !== reading.boot_id || old.sequence !== reading.sequence) {
    state.history.push(reading); if (state.history.length > 90) state.history.shift();
  }
  state.reading = reading; state.health = health || state.health; state.lastFrame = Date.parse(reading.received_at); state.dirty = true;
}
function render() {
  if (document.hidden) return;
  const age = (Date.now() - state.lastFrame) / 1000;
  const live = state.connected && age < 2;
  set("connection", !state.connected ? "Reconnecting…" : live ? "Live telemetry" : "Waiting for firmware");
  $("connection").classList.toggle("live", live);
  $("connection").classList.toggle("offline", !live);
  if (!state.dirty || state.view !== "overview") return; state.dirty = false;
  const r = state.reading, h = state.health, f = r?.firmware;
  if (r && f) {
    set("device-state", age >= 2 ? "Stale" : h?.state || "Waiting");
    set("measurement-state", age >= 2 ? "No recent firmware frames" : `Measurements ${h?.measurement_state || "current"}`);
    set("boot-id", r.boot_id); set("device-id", r.device_id);
    set("watchdog-state", f.health.watchdog); set("watchdog-detail", f.health.watchdog_enabled ? "Enabled · health-gated feed" : "Disabled in this firmware");
    for (const [index, axis] of ["x", "y", "z"].entries()) { set(`a${axis}`, fmt(f.imu?.accel_g[index], 3)); set(`g${axis}`, fmt(f.imu?.gyro_dps[index], 2)); }
    set("temperature", fmt(r.temperature_c)); set("pressure", fmt(r.pressure_hpa)); set("altitude", fmt(f.baro?.altitude_m));
    set("imu-temperature", `${fmt(f.imu?.temperature_c)} °C`);
    set("imu-status", `MPU6050 · ${h?.mpu6050 || "unknown"}`); set("baro-status", `BMP280 · ${h?.bmp280 || "unknown"}`);
    for (const [id, sensor] of [["board-imu", "mpu6050"], ["board-baro", "bmp280"]]) { set(id, h?.[sensor] === "online" ? "● ONLINE" : "● OFFLINE"); $(id).style.color = h?.[sensor] === "online" ? "var(--teal)" : "var(--red)"; }
    set("sample-age", age >= 2 ? "STALE" : `${fmt(h?.sample_age_seconds, 2)} s sample age`);
    set("frame-seq", r.sequence); set("sample-seq", f.seq); set("uptime", `${fmt(f.uptime_ms / 1000, 1)} s`);
    set("i2c", `${f.health.i2c_errors} / ${f.health.i2c_retries}`); set("drops", f.health.queue_drops); set("deadlines", f.health.deadline_misses);
    set("timing", `${f.timing.acquisition_us} / ${f.timing.processing_us} µs`); set("reset-flags", `0x${f.health.reset_flags.toString(16)}`);
    if ($("raw-record").parentElement.open) set("raw-record", JSON.stringify(f, null, 2));
  }
  set("frames", (state.stats.frames_accepted || 0).toLocaleString());
  set("decoder", `${state.stats.parse_errors || 0} / ${state.stats.sequence_anomalies || 0}`);
  const memory = state.lab?.memory;
  if (memory) {
    set("heap", `${memory.free_heap_bytes.toLocaleString()} bytes`); set("min-heap", `${memory.minimum_heap_bytes.toLocaleString()} bytes`);
    set("stacks", Object.entries(memory.stack_minimum_words).map(([k,v]) => `${k}: ${v}`).join(" · "));
    set("memory-age", `Memory snapshot · ${stamp(memory.observed_at)}`);
  }
  const key = JSON.stringify(state.alerts);
  if (key !== state.eventKey) {
    state.eventKey = key; set("alert-count", `${state.alerts.length} RECENT EVENTS`);
    const rows = state.alerts.map(a => { const li = node("li"), time = node("time", new Date(a.created_at).toLocaleTimeString()); time.dateTime = a.created_at; li.append(node("span", a.severity, `severity ${a.severity}`), node("span", a.message), time); return li; });
    $("alerts").replaceChildren(...(rows.length ? rows : [node("li", "No fault transitions in this session.", "empty")]));
  }
  drawChart();
}
function drawChart() {
  const canvas = $("trend"), width = canvas.clientWidth, height = canvas.clientHeight;
  if (!width || !height) return;
  const ratio = window.devicePixelRatio || 1;
  if (canvas.width !== Math.round(width * ratio) || canvas.height !== Math.round(height * ratio)) { canvas.width = Math.round(width * ratio); canvas.height = Math.round(height * ratio); }
  const ctx = canvas.getContext("2d"); ctx.setTransform(ratio, 0, 0, ratio, 0, 0); ctx.clearRect(0, 0, width, height);
  const metric = $("plot-metric").value;
  const series = state.history.map(r => metric === "acceleration" ? r.firmware.imu?.accel_g : metric === "gyro" ? r.firmware.imu?.gyro_dps : [metric === "altitude" ? r.firmware.baro?.altitude_m : metric === "pressure" ? r.pressure_hpa : r.temperature_c]);
  const finite = series.flat().filter(Number.isFinite); const low = finite.length ? Math.min(...finite) : 0, high = finite.length ? Math.max(...finite) : 1;
  const padding = Math.max((high - low) * .15, .1), min = low - padding, max = high + padding;
  const left = 58;
  ctx.font = "12px Segoe UI"; ctx.fillStyle = chartTheme.muted; ctx.strokeStyle = chartTheme["chart-grid"]; ctx.lineWidth = 1;
  for (let i = 0; i < 4; i++) { const y = 16 + i / 3 * (height - 36); ctx.beginPath(); ctx.moveTo(left, y); ctx.lineTo(width, y); ctx.stroke(); ctx.fillText((max - i / 3 * (max - min)).toFixed(1), 0, y + 3); }
  const axes = ["acceleration", "gyro"].includes(metric) ? 3 : 1, colors = axes === 3 ? [chartTheme["axis-x"], chartTheme["axis-y"], chartTheme["axis-z"]] : [chartTheme.teal];
  for (let axis = 0; axis < axes; axis++) {
    ctx.strokeStyle = colors[axis]; ctx.lineWidth = 2; ctx.beginPath(); let connected = false;
    series.forEach((values, i) => { const value = values?.[axis]; if (!Number.isFinite(value)) { connected = false; return; } const x = left + i / Math.max(series.length - 1, 1) * (width - left - 3), y = 16 + (max - value) / (max - min) * (height - 36); connected ? ctx.lineTo(x,y) : ctx.moveTo(x,y); connected = true; }); ctx.stroke();
  }
  set("plot-range", finite.length ? `${fmt(low)} – ${fmt(high)}${axes === 3 ? " · X blue / Y gold / Z purple" : ""}` : "No available measurements");
}
async function pollNow() {
  if (state.poll) return state.poll;
  state.poll = (async () => {
    try {
      const [demo, lab, snapshot] = await Promise.all([api("/api/v1/demo"), api("/api/v1/lab"), api("/api/v1/snapshot")]);
      if (state.apiError) { notice(""); state.apiError = false; }
      state.token = demo.token; state.ready = demo.ready; state.lab = lab; state.stats = snapshot.stats;
      if (snapshot.devices[0]) state.health = snapshot.devices[0]; addAlerts(snapshot.alerts);
      makeCatalog(lab.catalog); showResults(lab); showReports(lab.reports); showCoverage(lab.coverage); controls(); state.dirty = true;
    } catch (error) { state.apiError = true; state.ready = false; controls(); notice(`Console unavailable: ${error.message}. Waiting to reconnect.`, true); }
    finally { state.poll = null; }
  })(); return state.poll;
}
async function pollLoop() { if (!document.hidden) await pollNow(); setTimeout(pollLoop, state.lab?.active ? 750 : 3000); }
function connectSocket() {
  if (state.socket && [WebSocket.OPEN, WebSocket.CONNECTING].includes(state.socket.readyState)) return;
  const socket = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws/telemetry`); state.socket = socket;
  socket.onopen = () => { state.connected = true; state.backoff = 1000; pollNow(); controls(); };
  socket.onmessage = async (event) => {
    const data = JSON.parse(event.data);
    if (data.type === "telemetry") { addReading(data.reading, data.health); addAlerts(data.alerts); state.stats.frames_accepted = (state.stats.frames_accepted || 0) + 1; }
    else {
      state.stats = data.stats || state.stats; state.health = data.devices?.[0] || state.health; addAlerts(data.alerts); state.dirty = true;
      if (data.type === "snapshot" && data.devices?.[0]) {
        try {
          const readings = await api(`/api/v1/devices/${encodeURIComponent(data.devices[0].device_id)}/readings?limit=90`);
          // Only initialize from REST before the first live frame; never overwrite a newer socket update.
          if (!state.reading && readings.length) { state.history = readings.slice(-89, -1); addReading(readings.at(-1), data.devices[0]); }
        } catch (error) { notice(error.message, true); }
      }
    }
  };
  socket.onclose = () => { state.connected = false; state.ready = false; controls(); state.dirty = true; setTimeout(connectSocket, state.backoff); state.backoff = Math.min(state.backoff * 2, 10000); };
  socket.onerror = () => socket.close();
}
document.querySelectorAll("[data-view]").forEach(el => el.addEventListener("click", () => view(el.dataset.view)));
document.querySelectorAll("[data-component]").forEach(el => el.addEventListener("click", () => { $("component-filter").value = el.dataset.component; selection(); view("tests"); }));
$("run-all").addEventListener("click", () => run(state.catalog.map(c => c.id)));
$("show-acceleration").addEventListener("click", showAcceleration);
$("acceleration-restore").addEventListener("click", () => act("/api/v1/demo/action", {action: "rest"}));
$("run-selected").addEventListener("click", () => run(state.catalog.filter(c => state.selected.has(c.id) && (!$("component-filter").value || c.component === $("component-filter").value)).map(c => c.id)));
$("cancel-run").addEventListener("click", () => act("/api/v1/lab/cancel", {}));
$("manual-controls").addEventListener("click", e => { const button = e.target.closest("[data-action]"); if (button && !button.disabled) act("/api/v1/demo/action", {action: button.dataset.action}); });
$("component-filter").addEventListener("change", selection);
$("select-all").addEventListener("change", () => { for (const item of state.catalog.filter(c => !$("component-filter").value || c.component === $("component-filter").value)) { $("select-all").checked ? state.selected.add(item.id) : state.selected.delete(item.id); state.cards.get(item.id).input.checked = $("select-all").checked; } selection(); });
$("plot-metric").addEventListener("change", () => { state.dirty = true; });
$("raw-record").parentElement.addEventListener("toggle", () => { state.dirty = true; });
new ResizeObserver(() => { state.dirty = true; }).observe($("trend"));
document.addEventListener("visibilitychange", () => { if (!document.hidden) { state.dirty = true; pollNow(); render(); } });
// Hidden/occluded tabs can suspend RAF. Keep at most one queued render callback.
let renderPending = false;
setInterval(() => {
  if (document.hidden || renderPending) return;
  if (Date.now() - state.lastFrame > 2000) state.dirty = true;
  renderPending = true;
  requestAnimationFrame(() => { renderPending = false; render(); });
}, 250);
controls(); connectSocket(); pollLoop();
