// Run the production dashboard handlers against real C->gateway WebSocket events.
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const path = require('node:path');
const events = JSON.parse(fs.readFileSync(0, 'utf8'));
const nodes = new Map();
const context2d = new Proxy({}, { get: () => (...values) => {
  assert(values.every(v => typeof v !== 'number' || Number.isFinite(v)), 'nonfinite chart coordinate');
}});
function node(selector) {
  if (!nodes.has(selector)) nodes.set(selector, {
    textContent: '', innerHTML: '', lastChild: {textContent: ''}, className: '',
    addEventListener() {}, clientWidth: 900, clientHeight: 320, getContext: () => context2d,
  });
  return nodes.get(selector);
}
const context = vm.createContext({
  document: {querySelector: node, querySelectorAll: () => [], createElement: () => node('span')},
  window: {devicePixelRatio: 1, addEventListener() {}},
  fetch: () => new Promise(() => {}), console,
});
vm.runInContext(fs.readFileSync(path.join(__dirname, '../gateway/src/sensor_monitor/web/app.js'), 'utf8'), context);
let outage = false, recovered = false;
for (const event of events) {
  context.reading = event.reading; context.health = event.health;
  vm.runInContext('renderReading(reading); renderHealth(health)', context);
  for (const [selector, missing] of [
    ['#pressure', event.reading.pressure_hpa === null],
    ['#acceleration', event.reading.accel_g === null],
    ['#temperature', event.reading.temperature_c === null],
    ['#gyro', event.reading.firmware.imu === null],
    ['#altitude', event.reading.firmware.baro === null],
  ]) {
    assert.equal(node(selector).textContent === 'n/a', missing, selector);
  }
  assert.equal(node('#device-state').textContent, event.health.state);
  assert.equal(node('#sample-state').textContent, event.health.measurement_state);
  assert.equal(String(node('#boot-id').textContent), String(event.reading.boot_id));
  assert.equal(String(node('#frame-sequence').textContent), String(event.reading.sequence));
  if (event.reading.firmware.imu) {
    assert.equal(node('#acceleration').textContent, event.reading.accel_g.map(v => v.toFixed(3)).join(' / '));
    assert.equal(node('#gyro').textContent, event.reading.firmware.imu.gyro_dps.map(v => v.toFixed(3)).join(' / '));
  }
  if (event.reading.firmware.baro) {
    assert.equal(node('#altitude').textContent, event.reading.firmware.baro.altitude_m.toFixed(1));
  }
  if (event.reading.pressure_hpa === null || event.reading.accel_g === null) {
    outage = true;
  } else if (outage) {
    recovered = true;
  }
}
assert(outage && recovered, 'must render both missing values and subsequent recovery');
assert.equal(node('#device-state').textContent, 'online');
assert.equal(node('#sample-state').textContent, 'current');
console.log('dashboard recovery verified with real compiled firmware events');
