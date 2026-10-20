"use strict";

const state = { temperatures: [], alerts: [] };
const $ = (selector) => document.querySelector(selector);
const connection = $("#connection");
const number = (value, places = 1) => Number.isFinite(value) ? value.toFixed(places) : "n/a";
const vector = (value) => Array.isArray(value) ? value.map((v) => number(v, 3)).join(" / ") : "n/a";
let demoToken = null;

function setConnection(label, className) {
  connection.className = `connection ${className}`;
  connection.lastChild.textContent = ` ${label}`;
}

function renderReading(reading) {
  $("#temperature").textContent = number(reading.temperature_c);
  $("#pressure").textContent = number(reading.pressure_hpa);
  $("#device-id").textContent = reading.device_id;
  state.temperatures.push(reading.temperature_c);
  if (state.temperatures.length > 60) state.temperatures.shift();
  drawChart();
  const firmware = reading.firmware;
  $("#acceleration").textContent = vector(reading.accel_g);
  $("#gyro").textContent = vector(firmware?.imu?.gyro_dps);
  $("#altitude").textContent = number(firmware?.baro?.altitude_m);
  $("#imu-status").textContent = firmware ? `${firmware.sensors.mpu6050} / ${firmware.imu ? "valid" : "no data"}` : "unknown";
  $("#baro-status").textContent = firmware ? `${firmware.sensors.bmp280} / ${firmware.baro ? "valid" : "no data"}` : "unknown";
  $("#boot-id").textContent = reading.boot_id ?? "legacy";
  $("#frame-sequence").textContent = reading.sequence;
  $("#sample-sequence").textContent = firmware?.sample_present ? firmware.seq : "n/a";
  $("#i2c-total").textContent = reading.i2c_errors;
  $("#queue-total").textContent = reading.queue_drops;
  $("#i2c-retries").textContent = firmware?.health.i2c_retries ?? "n/a";
  $("#deadlines").textContent = firmware?.health.deadline_misses ?? "n/a";
  $("#timing").textContent = firmware ? `${firmware.timing.acquisition_us} / ${firmware.timing.processing_us} us` : "n/a";
  $("#watchdog").textContent = firmware ? `${firmware.health.watchdog_enabled ? "enabled" : "disabled"}; health ${firmware.health.watchdog === "fed" ? "eligible" : "withheld"}` : "unknown";
  $("#reset-flags").textContent = firmware ? `0x${firmware.health.reset_flags.toString(16)}` : "unknown";
}

function renderHealth(health) {
  if (!health) return;
  $("#device-state").textContent = health.state;
  $("#sample-state").textContent = health.measurement_state;
  $("#sample-age").textContent = `${number(health.sample_age_seconds, 2)} s`;
}

function renderAlerts(alerts) {
  if (!alerts?.length) return;
  const known = new Set(state.alerts.map((alert) => alert.id));
  state.alerts.unshift(...alerts.filter((alert) => !known.has(alert.id)));
  state.alerts = state.alerts.slice(0, 12);
  $("#alert-count").textContent = `${state.alerts.length} events`;
  $("#alerts").innerHTML = state.alerts.map((alert) => `
    <li>
      <span class="severity ${alert.severity}">${alert.severity}</span>
      <span class="device">${escapeHtml(alert.device_id)}</span>
      <span class="message">${escapeHtml(alert.message)}</span>
      <time>${new Date(alert.created_at).toLocaleTimeString()}</time>
    </li>`).join("");
}

function escapeHtml(value) {
  const node = document.createElement("span");
  node.textContent = value;
  return node.innerHTML;
}

function drawChart() {
  const canvas = $("#chart");
  const ratio = window.devicePixelRatio || 1;
  const width = canvas.clientWidth;
  const height = canvas.clientHeight;
  canvas.width = width * ratio;
  canvas.height = height * ratio;
  const ctx = canvas.getContext("2d");
  ctx.scale(ratio, ratio);
  ctx.clearRect(0, 0, width, height);
  ctx.strokeStyle = "#263b36";
  ctx.lineWidth = 1;
  for (let row = 1; row < 5; row += 1) {
    ctx.beginPath(); ctx.moveTo(0, (height / 5) * row); ctx.lineTo(width, (height / 5) * row); ctx.stroke();
  }
  if (state.temperatures.length < 2) return;
  const valid = state.temperatures.filter(Number.isFinite);
  if (!valid.length) return;
  const min = Math.min(...valid) - 1;
  const max = Math.max(...valid) + 1;
  ctx.strokeStyle = "#6ee7b7";
  ctx.lineWidth = 2;
  ctx.beginPath();
  let connected = false;
  state.temperatures.forEach((value, index) => {
    if (!Number.isFinite(value)) { connected = false; return; }
    const x = (index / 59) * width;
    const y = height - ((value - min) / (max - min || 1)) * height;
    connected ? ctx.lineTo(x, y) : ctx.moveTo(x, y);
    connected = true;
  });
  ctx.stroke();
}

async function refreshSnapshot() {
  const response = await fetch("/api/v1/snapshot");
  if (!response.ok) throw new Error("snapshot unavailable");
  const snapshot = await response.json();
  $("#samples").textContent = snapshot.stats.frames_accepted;
  if (snapshot.devices.length) {
    renderHealth(snapshot.devices[0]);
    $("#device-id").textContent = snapshot.devices[0].device_id;
    const readingsResponse = await fetch(`/api/v1/devices/${encodeURIComponent(snapshot.devices[0].device_id)}/readings?limit=60`);
    if (readingsResponse.ok) {
      const readings = await readingsResponse.json();
      state.temperatures = readings.slice(0, -1).map((reading) => reading.temperature_c);
      if (readings.length) renderReading(readings.at(-1));
    }
  }
  renderAlerts(snapshot.alerts);
}

function connect() {
  const protocol = location.protocol === "https:" ? "wss" : "ws";
  const socket = new WebSocket(`${protocol}://${location.host}/ws/telemetry`);
  socket.addEventListener("open", () => setConnection("Live", "online"));
  socket.addEventListener("message", (event) => {
    const payload = JSON.parse(event.data);
    if (payload.type === "telemetry") {
      renderReading(payload.reading);
      renderAlerts(payload.alerts);
      const samples = Number($("#samples").textContent || 0) + 1;
      $("#samples").textContent = samples;
      renderHealth(payload.health);
    } else if (payload.type === "snapshot") {
      $("#samples").textContent = payload.stats.frames_accepted;
      renderAlerts(payload.alerts);
      renderHealth(payload.devices[0]);
    } else if (payload.type === "heartbeat") {
      $("#samples").textContent = payload.stats.frames_accepted;
      if (payload.devices?.length) renderHealth(payload.devices[0]);
    }
  });
  socket.addEventListener("close", () => { setConnection("Reconnecting", "offline"); setTimeout(connect, 1500); });
}

$("#fault-controls").addEventListener("click", async (event) => {
  const button = event.target.closest("button[data-mode]");
  if (!button) return;
  const response = await fetch("/api/v1/simulator/fault", {
    method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ mode: button.dataset.mode }),
  });
  if (response.ok) {
    document.querySelectorAll("#fault-controls button").forEach((item) => item.classList.toggle("active", item === button));
  } else {
    button.textContent = "Unavailable for serial source";
  }
});

$("#renode-controls").addEventListener("click", async (event) => {
  const button = event.target.closest("button[data-action]");
  if (!button || !demoToken) return;
  const buttons = document.querySelectorAll("#renode-controls button");
  buttons.forEach((item) => { item.disabled = true; });
  $("#demo-status").textContent = `${button.textContent} — waiting for firmware telemetry...`;
  try {
    const response = await fetch("/api/v1/demo/action", {
      method: "POST",
      headers: { "content-type": "application/json", "x-demo-token": demoToken },
      body: JSON.stringify({ action: button.dataset.action }),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.detail || "Control request failed");
    $("#demo-status").textContent = result.message;
  } catch (error) {
    $("#demo-status").textContent = `Demo control: ${error.message}. Check that the launcher is running.`;
  } finally {
    buttons.forEach((item) => { item.disabled = false; });
  }
});

window.addEventListener("resize", drawChart);
refreshSnapshot().catch(() => {}).finally(connect);
fetch("/healthz").then((response) => response.json()).then((health) => {
  $("#source-name").textContent = health.source;
  if (health.source !== "simulator") {
    $("#fault-controls").hidden = true;
    $("#source-hint").textContent = health.source === "firmware"
      ? "Live output from the compiled C firmware simulator. The selected scenario injects faults and recovery; each repeat starts a new boot."
      : "Telemetry arrives from the selected capture or serial device.";
  }
  if (health.source === "renode") {
    $("#source-hint").textContent = "STM32F401RE firmware executes in Renode. Digital sensor models feed I2C, then USART2 carries telemetry to this dashboard.";
    return fetch("/api/v1/demo").then(async (response) => {
      if (!response.ok) return;
      const demo = await response.json();
      demoToken = demo.token;
      $("#controls-title").textContent = "Renode demonstration";
      $("#renode-controls").hidden = false;
      $("#demo-status").hidden = false;
      $("#demo-status").textContent = demo.message;
    });
  }
}).catch(() => {});
