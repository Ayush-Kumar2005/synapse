/* DriftBeacon phone controller: all evidence comes from the active replay service. */
const byId = id => document.getElementById(id);
const ui = { id: null, generation: 0, cursor: -1, points: [], sourceTime: 0, playing: false,
  completed: false, speed: 1, scenario: null, original: null, simulated: null, health: null,
  trip: null, sections: null, efficiency: null, activeTab: 'drive', selectedSection: null,
  selectedWindow: null, connection: 'loading', busy: false, polling: false, sectionBusy: false,
  lastSectionFetch: 0, map: null, mapLines: [], driveMap: null, driveLine: null, driveMarker: null, draft: {}, alertLatched: false };
const statusCopy = {
  collecting: ['Collecting baseline', 'Collecting enough valid readings to assess this trip.'],
  within_range: ['Within expected range', 'Fuel use is within the configured expected range for this replay window.'],
  watch: ['Watch', 'Fuel use is above expected. Checking whether the difference persists.'],
  inspection: ['Inspection recommended', 'Sustained excess fuel use detected. Review the evidence and consider an inspection.'],
  insufficient: ['Insufficient data', 'Not enough fresh readings for a reliable assessment.']
};
const fmtTime = sec => `${String(Math.floor(sec / 60)).padStart(2, '0')}:${String(Math.floor(sec % 60)).padStart(2, '0')}`;
const n = (value, digits = 2) => value == null || !Number.isFinite(value) ? '—' : Number(value).toFixed(digits);
const set = (id, value) => { const el = byId(id); if (el) el.textContent = value; };
const show = (id, yes) => byId(id)?.classList.toggle('hidden', !yes);
const channel = () => ui.scenario?.severity_pct > 0 ? 'simulated' : 'original';
const current = () => ui.points.at(-1)?.[channel()] || null;
const assessment = () => channel() === 'simulated' ? ui.simulated : ui.original;
const faultResult = () => ui.faultAnalysis?.available && ui.faultAnalysis.case_id === ui.trip?.id && ui.faultAnalysis.required_inputs_present && ui.faultAnalysis.training_domain_note ? ui.faultAnalysis : null;
const iconRefresh = () => window.lucide?.createIcons();

async function api(path, options = {}) {
  const response = await fetch(path, { headers: { 'Content-Type': 'application/json' }, ...options });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || `Replay service error ${response.status}`);
  return data;
}

function toast(message) {
  set('toastMessage', message);
  show('notificationToast', true);
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => show('notificationToast', false), 3400);
}
function hideToast() { show('notificationToast', false); }
function setConnection(state, message) {
  ui.connection = state;
  const connected = state === 'connected';
  ['btnPlayPause', 'btnResetTrip', 'btnApplyAndReplay', 'btnClearDrift', 'tripScrubber'].forEach(id => { if (byId(id)) byId(id).disabled = !connected || ui.busy; });
  if (!connected && state !== 'loading') {
    set('statusLabelText', state === 'disconnected' ? 'Replay disconnected' : 'Replay unavailable');
    set('statusHeadline', message || 'Local replay service unavailable.');
    set('statusSubtext', 'The last received values are stale. Check the local server.');
    byId('statusIndicatorDot').className = 'w-2.5 h-2.5 rounded-full bg-slate-400';
    byId('statusIndicatorDot').style.backgroundColor = '#64748b';
    byId('statusLabelText').style.color = '#475569';
    byId('statusIconWrap').style.color = '#475569';
    byId('statusBanner').classList.add('stale-status');
  } else byId('statusBanner').classList.remove('stale-status');
}

function merge(data, generation) {
  if (generation !== ui.generation || data.id !== ui.id) return;
  if (data.cursor < ui.cursor) ui.points = [];
  const last = ui.points.at(-1)?.seq ?? -1;
  ui.points.push(...data.points.filter(point => point.seq > last));
  ui.cursor = data.cursor;
  ui.sourceTime = data.source_time_s;
  ui.playing = data.playing;
  ui.completed = data.completed;
  ui.speed = data.speed;
  ui.scenario = data.scenario;
  ui.original = data.original;
  ui.simulated = data.simulated;
  ui.co2 = data.co2;
  ui.emissions = data.emissions;
  ui.pucAdvisory = data.puc_advisory;
  ui.faultAnalysis = data.fault_analysis;
  render();
  if (ui.activeTab === 'analysis' || ui.activeTab === 'service') refreshSections();
}

async function newReplay(options = {}) {
  const generation = ++ui.generation;
  ui.busy = true;
  ui.id = null; ui.cursor = -1; ui.points = []; ui.original = ui.simulated = null;
  ui.sections = ui.efficiency = null; ui.selectedSection = ui.selectedWindow = null;
  ui.alertLatched = false;
  setConnection('loading');
  const severity_pct = options.severity_pct ?? Number(byId('sliderDrift').value);
  const onset_s = options.onset_s ?? Number(byId('sliderStartTime').value) * 60;
  const ramp_s = options.ramp_s ?? Number(byId('sliderRamp').value) * 60;
  try {
    const data = await api('/api/replays', { method: 'POST', body: JSON.stringify({ trip_id: ui.trip?.id, severity_pct, onset_s, ramp_s }) });
    if (generation !== ui.generation) return;
    ui.id = data.id; ui.busy = false;
    setConnection('connected');
    merge(data, generation);
    await refreshSections(true);
  } catch (error) {
    if (generation !== ui.generation) return;
    ui.busy = false; setConnection('disconnected', error.message); toast(error.message);
  }
}

async function control(action, extra = {}) {
  if (!ui.id || ui.busy) return;
  const generation = ['reset', 'seek'].includes(action) ? ++ui.generation : ui.generation;
  ui.busy = true;
  try {
    const data = await api(`/api/replays/${ui.id}/control`, { method: 'POST', body: JSON.stringify({ action, ...extra }) });
    if (generation !== ui.generation) return;
    if (['reset', 'seek'].includes(action)) {
      ui.cursor = -1; ui.points = []; ui.sections = ui.efficiency = null;
      ui.selectedSection = ui.selectedWindow = null;
      ui.alertLatched = false;
    }
    ui.busy = false; setConnection('connected'); merge(data, generation);
    if (['reset', 'seek'].includes(action)) await refreshSections(true);
  } catch (error) { ui.busy = false; setConnection('disconnected', error.message); toast(error.message); }
}

async function poll() {
  if (!ui.id || ui.connection !== 'connected' || ui.busy || ui.polling) return;
  ui.polling = true;
  const id = ui.id, generation = ui.generation, cursor = ui.cursor;
  try {
    const data = await api(`/api/replays/${id}?after_seq=${cursor}`);
    if (generation === ui.generation) { setConnection('connected'); merge(data, generation); }
  } catch (error) { if (generation === ui.generation) setConnection('disconnected', 'Replay connection interrupted.'); }
  finally { ui.polling = false; }
}

async function refreshSections(force = false) {
  if (!ui.id || ui.sectionBusy || (!force && Date.now() - ui.lastSectionFetch < 1200)) return;
  ui.sectionBusy = true;
  const id = ui.id, generation = ui.generation;
  try {
    const data = await api(`/api/replays/${id}/sections`);
    if (id !== ui.id || generation !== ui.generation) return;
    ui.sections = data.sections; ui.efficiency = data.efficiency; ui.lastSectionFetch = Date.now();
    renderAnalysis(); renderReport(); renderRoute();
  } catch (error) { /* Live replay remains usable; route/efficiency stay unavailable. */ }
  finally { ui.sectionBusy = false; }
}

function switchTab(tab) {
  ui.activeTab = tab;
  for (const [key, id] of Object.entries({ drive: 'tabContentDrive', analysis: 'tabContentAnalysis', service: 'tabContentService' })) show(id, key === tab);
  for (const [key, id] of Object.entries({ drive: 'navTabDrive', analysis: 'navTabAnalysis', service: 'navTabService' })) {
    const el = byId(id); el.classList.toggle('text-brand-forest', key === tab); el.classList.toggle('text-slate-400', key !== tab);
    el.querySelector('span').classList.toggle('font-black', key === tab);
  }
  closeHotspotModal();
  if (tab === 'analysis' || tab === 'service') refreshSections(true);
  render(); iconRefresh();
}

function status() {
  if (ui.connection !== 'connected') return;
  const data = assessment();
  ui.alertLatched = ui.alertLatched || Boolean(data?.alerts?.length);
  const state = ui.alertLatched ? 'inspection' : data?.status === 'insufficient' ? 'insufficient' : data?.status === 'collecting' ? 'collecting' : 'within_range';
  const [label, explanation] = statusCopy[state] || statusCopy.collecting;
  const high = (ui.scenario?.severity_pct || 0) >= 25;
  set('statusLabelText', state === 'inspection' && ui.faultAnalysis?.scenario_label ? high ? 'RICH MIXTURE CHECK' : 'FUEL DELIVERY CHECK' : label.toUpperCase());
  set('statusHeadline', state === 'inspection' && ui.faultAnalysis?.scenario_label ? high ? 'High fuel drift detected. The expert system suggests a rich-mixture pattern for inspection.' : 'Medium fuel drift detected. The expert system suggests a fuel-delivery check.' : explanation);
  const isDemo = ui.trip?.id === 'generated-42-minute-trip';
  set('statusSubtext', isDemo ? 'Missing-data example · no location data.' : ui.alertLatched ? high ? 'Possible rich mixture · expert system screening.' : 'Possible fuel-delivery issue · expert system screening.' : 'Demo car moving · monitoring fuel use.');
  const ink = { collecting: '#64748b', within_range: '#059669', watch: '#d97706', inspection: '#dc2626', insufficient: '#64748b' }[state];
  byId('statusIndicatorDot').style.backgroundColor = ink;
  byId('statusLabelText').style.color = ink;
  byId('statusIconWrap').style.color = ink;
  byId('statusBanner').classList.toggle('alert-status', state === 'inspection');
  byId('statusBanner').classList.toggle('stale-status', state === 'insufficient');
}

function renderDrive() {
  const point = current();
  const summary = assessment();
  const severity = ui.scenario?.severity_pct || 0;
  const time = fmtTime(ui.sourceTime);
  set('tripTimeDisplay', `${time} / ${fmtTime(ui.trip?.duration_s || 2520)}`);
  byId('tripScrubber').value = ui.sourceTime;
  set('playBtnText', ui.completed ? 'Complete' : ui.playing ? 'Pause' : 'Play');
  byId('btnPlayPause').disabled = !ui.id || ui.completed || ui.connection !== 'connected';
  for (const btn of document.querySelectorAll('.speed-btn')) {
    const active = Number(btn.dataset.speed) === ui.speed;
    btn.classList.toggle('bg-brand-forest', active); btn.classList.toggle('text-white', active);
    btn.classList.toggle('text-brand-navy', !active);
  }
  set('drivingContextText', point?.context ? describePointContext(point) : 'Awaiting telemetry');
  set('heroSpeedBadge', point?.speed_kph == null ? '— km/h' : `${Math.round(point.speed_kph)} km/h`);
  const recent = ui.points.filter(p => p.source_time_s >= ui.sourceTime - 60).map(p => p[channel()]);
  const meanRate = field => { const values = recent.map(p => p[field]).filter(v => v != null); return values.length ? values.reduce((a, b) => a + b, 0) / values.length : null; };
  set('metricObservedFuel', n(meanRate('observed_fuel_lph'), 1));
  set('metricExpectedFuel', n(meanRate('expected_fuel_lph'), 1));
  set('metricExcessFuel', n(summary?.excess_l, 2));
  set('excessFuelSub', point?.data_quality === 'insufficient' ? 'Current gap excluded' : 'Integrated across valid driving');
  set('fuelDeltaBadge', point?.data_quality === 'insufficient' ? 'Reading unavailable' : 'Recent 60-sec average · estimated');
  set('metricExcessCO2', ui.co2?.kg_per_l == null || summary?.excess_l == null ? '—' : n(summary.excess_l * ui.co2.kg_per_l, 2));
  set('co2FactorDrive', ui.co2?.kg_per_l == null ? 'Factor unavailable' : `Fuel-based estimate · ${n(ui.co2.kg_per_l, 3)} kg/L`);
  set('driveNoxValue', ui.emissions?.nox_ppm == null ? '—' : `${n(ui.emissions.nox_ppm, 0)} ppm`);
  set('driveNoxSource', ui.emissions?.nox_ppm == null ? 'Reading unavailable' : 'Demo exhaust reading · ppm');
  set('driveCoValue', ui.emissions?.co_pct == null ? '—' : `${n(ui.emissions.co_pct, 2)}%`);
  set('driveCoSource', ui.emissions?.co_pct == null ? 'Reading unavailable' : 'Demo exhaust reading · %');
  show('pucDriveCard', ui.pucAdvisory?.status === 'consider_test');
  set('pucDriveReason', ui.pucAdvisory?.message || '');
  show('syntheticBadge', severity > 0);
  set('syntheticBadge', `⚡ +${severity}% fuel drift`);
  set('phoneClock', '09:41');
  renderDriveMap();
  status();
}

function renderDriveMap() {
  const points = ui.points.map(p => p.original).filter(p => p.latitude != null && p.longitude != null);
  const mapElement = byId('driveMap');
  if (!mapElement || !ui.trip?.gps_available || !points.length) return;
  show('driveRouteUnavailable', false);
  show('driveMap', true);
  if (!window.L) { loadLeaflet(); return; }
  const coords = points.map(p => [p.latitude, p.longitude]);
  if (!ui.driveMap) {
    ui.driveMap = L.map(mapElement, { zoomControl: false, attributionControl: true });
    L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', { maxZoom: 19, attribution: '© OpenStreetMap contributors' }).addTo(ui.driveMap);
  }
  if (ui.driveLine) ui.driveMap.removeLayer(ui.driveLine);
  ui.driveLine = L.polyline(coords, { color: '#0d9488', weight: 5, opacity: .9 }).addTo(ui.driveMap);
  if (ui.driveMarker) ui.driveMap.removeLayer(ui.driveMarker);
  const carIcon = L.divIcon({ className: 'drive-car-marker', html: '<svg width="26" height="40" viewBox="0 0 26 40" aria-hidden="true"><rect x="3" y="3" width="20" height="34" rx="6" fill="#174634" stroke="white" stroke-width="2"/><rect x="6" y="10" width="14" height="15" rx="3" fill="#a5d5c3"/><circle cx="7" cy="5" r="2" fill="#fef08a"/><circle cx="19" cy="5" r="2" fill="#fef08a"/></svg>', iconSize: [26, 40], iconAnchor: [13, 20] });
  ui.driveMarker = L.marker(coords.at(-1), { icon: carIcon }).addTo(ui.driveMap);
  ui.driveMap.setView(coords.at(-1), coords.length < 3 ? 13 : 14, { animate: false });
  requestAnimationFrame(() => ui.driveMap?.invalidateSize());
}

function describePointContext(point) {
  const recent = ui.points.filter(p => p.source_time_s >= ui.sourceTime - 60).map(p => p.original.speed_kph);
  if (point.speed_kph < 12) return 'Low-speed / idle';
  if (recent.length > 2 && Math.max(...recent) - Math.min(...recent) < 12 && point.speed_kph >= 65) return 'Steady cruise';
  return 'Variable-speed driving';
}

const svg = (tag, attrs = {}) => { const el = document.createElementNS('http://www.w3.org/2000/svg', tag); for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, String(v)); return el; };
function renderChart() {
  const root = byId('analysisSvgChart'); if (!root) return;
  root.replaceChildren();
  const x = t => t / (ui.trip?.duration_s || 2520) * 340;
  const maxRate = Math.max(8, ...ui.points.map(p => p[channel()]?.observed_fuel_lph || 0)) * 1.15;
  set('chartYMax', `${n(maxRate, 0)} L/h`);
  set('chartOnsetLabel', ui.scenario?.severity_pct ? `${fmtTime(ui.scenario.onset_s)} (drift begins)` : 'No drift onset');
  const y = rate => 122 - rate / maxRate * 112;
  for (const yy of [22, 60, 98]) root.append(svg('line', { x1: 0, x2: 340, y1: yy, y2: yy, stroke: '#e2e8f0', 'stroke-dasharray': '2 2' }));
  const selected = ui.selectedWindow || (assessment()?.alerts?.[0] ? [assessment().alerts[0].evidence_start_s, assessment().alerts[0].detected_at_s] : null);
  if (selected) root.append(svg('rect', { x: x(selected[0]), width: Math.max(1, x(selected[1]) - x(selected[0])), y: 0, height: 130, fill: '#f59e0b', opacity: .13 }));
  for (const p of ui.points) if (p[channel()]?.data_quality === 'insufficient') root.append(svg('rect', { x: x(p.source_time_s), width: 2, y: 0, height: 130, fill: '#cbd5e1', opacity: .65 }));
  for (const [field, color, dash] of [['expected_fuel_lph', '#94a3b8', '3 3'], ['observed_fuel_lph', '#174634', '']]) {
    let d = '', open = false;
    for (const p of ui.points) {
      const rate = p[channel()]?.[field];
      if (rate == null) { open = false; continue; }
      d += `${open ? 'L' : 'M'}${x(p.source_time_s).toFixed(1)} ${y(rate).toFixed(1)} `; open = true;
    }
    root.append(svg('path', { d, fill: 'none', stroke: color, 'stroke-width': field === 'observed_fuel_lph' ? 2.5 : 2, 'stroke-dasharray': dash }));
  }
  const cursor = x(ui.sourceTime);
  root.append(svg('line', { x1: cursor, x2: cursor, y1: 0, y2: 130, stroke: '#0d9488', 'stroke-dasharray': '2 2' }));
  const lastRate = current()?.observed_fuel_lph;
  if (lastRate != null) root.append(svg('circle', { cx: cursor, cy: y(lastRate), r: 4, fill: '#0d9488', stroke: 'white', 'stroke-width': 2 }));
}

function renderAnalysis() {
  const point = current(), summary = assessment(), alert = summary?.alerts?.[0];
  const high = (ui.scenario?.severity_pct || 0) >= 25;
  const expert = ui.faultAnalysis?.expert_system;
  const reason = expert?.likely_reasons?.[0];
  set('analysisTripPosition', `At ${fmtTime(ui.sourceTime)}`);
  const context = point ? describePointContext(point) : 'Driving context unavailable';
  const narrative = ui.connection !== 'connected' ? 'Replay disconnected. Displayed readings are stale.' :
    point?.data_quality === 'insufficient' ? 'This interval has missing fuel readings. No reliable assessment is available here.' :
    alert && reason?.reason_code === 'rich_mixture_pattern' ? `High fuel drift: the engine-control example is pulling fuel back, yet the exhaust mixture remains rich. The expert system therefore screens for over-fueling, such as a leaking injector or excess fuel pressure. Fuel use stayed elevated for ${Math.round(alert.duration_s / 60)} minutes; confirm with vehicle diagnostics before repair.` :
    alert && reason?.reason_code === 'fuel_delivery_pressure' ? `Medium fuel drift: the engine-control example is adding fuel while pressure is low. The expert system points to restricted fuel delivery, so a technician should test pump, filter and pressure under load. Fuel use stayed elevated for ${Math.round(alert.duration_s / 60)} minutes.` :
    alert ? `Fuel use stayed above expected for ${Math.round(alert.duration_s / 60)} minutes. Review the supporting signals before choosing an inspection path.` :
    point ? `At this reading: ${n(point.speed_kph, 0)} km/h, ${n(point.rpm, 0)} RPM and ${n(point.load_pct, 0)}% load. These driving inputs inform the expected fuel estimate; no sustained alert has been recorded yet.` :
    'Collecting source-time readings. This panel will explain the recorded driving conditions and any sustained excess.';
  set('analysisSummaryText', narrative);
  const efficiency = ui.efficiency?.[channel()];
  set('analysisL100km', efficiency?.observed_l_per_100km == null ? 'Unavailable until valid fuel and distance are integrated' : `${n(efficiency.observed_l_per_100km, 1)} L/100 km · speed-derived distance`);
  const priorTrips = [0.8, 1.2, 0.9];
  const reference = priorTrips.reduce((sum, value) => sum + value, 0) / priorTrips.length;
  const currentDrift = summary?.excess_pct;
  set('analysisFleetComparison', currentDrift == null ? 'Current trip collecting · example average +1.0%' : `Current +${n(currentDrift, 1)}% · example avg +${n(reference, 1)}%`);
  const comparison = byId('comparisonTripRows'); comparison.replaceChildren();
  for (const [label, value, live] of [
    ['Example A', priorTrips[0], false], ['Example B', priorTrips[1], false],
    ['Example C', priorTrips[2], false], ['This drive', currentDrift, true]
  ]) {
    const row = document.createElement('div');
    row.className = 'grid grid-cols-[68px_1fr_52px] items-center gap-2';
    const name = document.createElement('span'); name.textContent = label;
    name.className = live ? 'font-bold text-brand-navy' : 'text-brand-navyMuted';
    const track = document.createElement('div'); track.className = 'h-2 rounded-full bg-brand-mint overflow-hidden';
    const fill = document.createElement('div'); fill.className = `h-full rounded-full ${live && value >= 3 ? 'bg-red-500' : live ? 'bg-brand-forest' : 'bg-brand-teal'}`;
    fill.style.width = `${value == null ? 0 : Math.max(4, Math.min(100, value / 8 * 100))}%`;
    track.append(fill);
    const amount = document.createElement('strong'); amount.className = 'text-right font-mono text-brand-navy';
    amount.textContent = value == null ? '—' : `+${n(value, 1)}%`;
    row.append(name, track, amount); comparison.append(row);
  }
  set('contextExplanation', point ? `${context}. The car is at ${n(point.speed_kph, 0)} km/h, ${n(point.rpm, 0)} RPM and ${n(point.load_pct, 0)}% engine load. These inputs change the fuel amount normally expected.` : 'Speed, RPM and engine load will appear as the car moves.');
  const stats = byId('contextStats'); stats.replaceChildren();
  for (const [label, value] of [['Speed', point?.speed_kph == null ? '—' : `${n(point.speed_kph, 0)} km/h`], ['RPM', n(point?.rpm, 0)], ['Load', point?.load_pct == null ? '—' : `${n(point.load_pct, 0)}%`]]) {
    const cell = document.createElement('div'); cell.className = 'p-1.5 rounded-lg bg-brand-mint';
    const name = document.createElement('div'); name.textContent = label; name.className = 'text-brand-navyMuted';
    const amount = document.createElement('strong'); amount.textContent = value;
    cell.append(name, amount); stats.append(cell);
  }
  set('analysisNetExcessFuel', summary?.excess_l == null ? '—' : `${n(summary.excess_l, 2)} L`);
  set('analysisNetExcessCO2', ui.co2?.kg_per_l == null || summary?.excess_l == null ? 'Unavailable' : `${n(summary.excess_l * ui.co2.kg_per_l, 2)} kg · ${n(ui.co2.kg_per_l, 3)} kg/L`);
  const price = Number(byId('fuelPriceInput').value);
  set('calcMonthlyExtraCost', byId('fuelPriceInput').value.trim() && Number.isFinite(price) && price >= 0 && summary?.excess_l != null ? `₹${(price * summary.excess_l).toFixed(2)} this trip` : 'Enter a fuel price');
  set('faultPattern', reason?.reason_code === 'rich_mixture_pattern' ? 'Expert system: possible over-fueling. Start with injector leak and fuel-pressure regulation checks; the exact component is unconfirmed.' : reason?.reason_code === 'fuel_delivery_pressure' ? 'Expert system: possible fuel-delivery restriction. Start with pump, filter and pressure checks; the exact component is unconfirmed.' : reason ? `Expert system finding: ${reason.reason}. A technician should confirm it.` : expert ? `No specific fault pattern yet. ${expert.data_checks.join(' ')}` : 'No fault pattern identified. Fuel use is within range or still being assessed.');
  const breakdown = byId('faultBreakdown'); breakdown.replaceChildren();
  if (reason) {
    const details = reason.reason_code === 'rich_mixture_pattern' ? [
      ['Evidence behind the finding', (reason.supporting_evidence || []).join(' · ').replace('Measured lambda', 'Demo lambda') || 'Negative fuel trims and rich exhaust mixture in the example.'],
      ['What could cause it', 'A leaking injector or excess rail pressure can keep adding fuel after the engine controller tries to reduce it. Incorrect air-flow or oxygen-sensor feedback is another possibility.'],
      ['First technician check', 'Confirm fuel trims and exhaust lambda on the vehicle, then run an injector leak-down or balance test and check fuel pressure against specification.'],
      ['PUC follow-up', ui.pucAdvisory?.status === 'consider_test' ? 'CO, NOx and estimated extra CO₂ crossed the demo advisory markers. Arrange an authorised PUC test to measure actual emissions.' : 'If exhaust readings rise further, measure actual emissions at an authorised PUC centre.']
    ] : [
      ['Evidence behind the finding', (reason.supporting_evidence || []).join(' · ') || 'Positive fuel trims with a low-pressure example reading.'],
      ['What could cause it', 'Restricted filter flow, a weak pump or pressure regulation can make the controller add fuel. An intake leak or air-flow error can imitate this pattern.'],
      ['First technician check', reason.next_check]
    ];
    for (const [title, value] of details) {
      const item = document.createElement('div'); item.className = 'rounded-lg bg-brand-mint p-2';
      const head = document.createElement('strong'); head.className = 'block text-brand-navy'; head.textContent = title;
      const body = document.createElement('span'); body.className = 'text-brand-navyMuted'; body.textContent = value;
      item.append(head, body); breakdown.append(item);
    }
  }
  renderChart(); renderRoute();
}

function routeColor(section) {
  if (section.coverage < .8 || section.expected_l == null || section.expected_l <= 0) return '#94a3b8';
  const pct = (section.observed_l - section.expected_l) / section.expected_l * 100;
  return pct >= 8 ? '#dc2626' : pct >= 3 ? '#d97706' : '#0d9488';
}
let leafletLoading = null;
function loadLeaflet() {
  if (window.L || leafletLoading) return;
  const style = document.createElement('link'); style.rel = 'stylesheet';
  style.href = 'https://unpkg.com/leaflet@1.9.4/dist/leaflet.css'; document.head.append(style);
  const script = document.createElement('script');
  script.src = 'https://unpkg.com/leaflet@1.9.4/dist/leaflet.js';
  leafletLoading = new Promise((resolve, reject) => { script.onload = resolve; script.onerror = reject; });
  leafletLoading.then(() => { renderRoute(); renderDriveMap(); }).catch(() => {
    byId('routeMap').textContent = 'OpenStreetMap library unavailable. Time-aligned sections remain selectable below.';
  });
  document.head.append(script);
}
function renderRoute() {
  const rows = ui.sections?.[channel()] || [];
  const list = byId('sectionList'); if (!list) return;
  list.replaceChildren();
  for (const section of rows) {
    if (section.start_s > ui.sourceTime) break;
    const button = document.createElement('button');
    button.type = 'button'; button.className = 'time-section-btn';
    button.style.borderColor = section.coverage < .999 ? '#94a3b8' : routeColor(section);
    button.textContent = `${fmtTime(section.start_s)}–${fmtTime(Math.min(section.end_s, ui.sourceTime))}`;
    button.title = `${section.context}; ${Math.round(section.coverage * 100)}% valid coverage`;
    button.addEventListener('click', () => selectSection(section));
    const alert = assessment()?.alerts?.[0];
    if (ui.selectedSection?.id === section.id || (!ui.selectedSection && alert && section.start_s <= alert.detected_at_s && section.end_s >= alert.evidence_start_s)) button.classList.add('selected');
    list.append(button);
  }
  const map = byId('routeMap');
  const routable = rows.some(s => Array.isArray(s.coordinates) && s.coordinates.length > 1);
  if (!routable) {
    if (ui.map) { ui.map.remove(); ui.map = null; ui.mapLines = []; }
    map.classList.add('route-map-unavailable');
    map.textContent = '';
    const strong = document.createElement('strong'); strong.textContent = 'Route unavailable';
    const span = document.createElement('span'); span.textContent = ui.trip?.gps_available ? 'Play or seek ahead to load aligned route sections.' : 'This trip has no GPS. A clickable OpenStreetMap route requires coordinates aligned to these fuel readings.';
    map.append(strong, span);
    set('mapAttribution', 'No OpenStreetMap tiles loaded');
    return;
  }
  if (ui.activeTab !== 'analysis') return;
  if (!window.L) { map.textContent = 'Loading OpenStreetMap route…'; loadLeaflet(); return; }
  map.classList.remove('route-map-unavailable');
  if (!ui.map) {
    map.replaceChildren(); ui.map = L.map(map, { zoomControl: false });
    L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', { maxZoom: 19, attribution: '© OpenStreetMap contributors' }).addTo(ui.map);
  }
  ui.mapLines.forEach(line => ui.map.removeLayer(line)); ui.mapLines = [];
  const bounds = [];
  for (const section of ui.sections[channel()]) {
    if (!Array.isArray(section.coordinates) || section.coordinates.length < 2) continue;
    const coords = section.coordinates.map(p => [p[0], p[1]]);
    const line = L.polyline(coords, { color: section.coverage < .999 ? '#94a3b8' : routeColor(section), weight: ui.selectedSection?.id === section.id ? 10 : 7, opacity: section.coverage < .999 ? .55 : .9 }).addTo(ui.map);
    line.on('click', () => selectSection(section)); ui.mapLines.push(line); bounds.push(...coords);
  }
  requestAnimationFrame(() => { ui.map?.invalidateSize(); if (bounds.length) ui.map?.fitBounds(bounds, { padding: [16, 16] }); });
  set('mapAttribution', '© OpenStreetMap contributors · sections reflect source-time net excess fuel');
}

function selectSection(section) {
  ui.selectedSection = section;
  ui.selectedWindow = [section.start_s, Math.min(section.end_s, ui.sourceTime)];
  set('hotspotModalTitle', `${fmtTime(section.start_s)}–${fmtTime(Math.min(section.end_s, ui.sourceTime))} evidence`);
  const details = byId('hotspotDetails'); details.replaceChildren();
  const entries = [
    ['Driving context', section.context], ['Distance', section.distance_km == null ? 'Unavailable' : `${n(section.distance_km, 2)} km · vehicle speed`],
    ['Average speed', section.mean_speed_kph == null ? '—' : `${n(section.mean_speed_kph, 1)} km/h`],
    ['Expected / observed', section.expected_l == null ? 'Unavailable' : `${n(section.expected_l, 3)} / ${n(section.observed_l, 3)} L`],
    ['Net excess fuel', section.excess_l == null ? 'Unavailable' : `${n(section.excess_l, 3)} L`],
    ['Valid coverage', `${Math.round(section.coverage * 100)}%`],
    ['RPM / load', section.mean_rpm == null ? 'Unavailable' : `${n(section.mean_rpm, 0)} RPM / ${n(section.mean_load_pct, 0)}%`],
    ['GPS route', section.coordinates ? 'Recorded coordinates' : 'Unavailable']
  ];
  for (const [name, value] of entries) {
    const row = document.createElement('div'); row.className = 'flex justify-between gap-3';
    const label = document.createElement('span'); label.className = 'text-brand-navyMuted'; label.textContent = name;
    const val = document.createElement('strong'); val.className = 'text-right'; val.textContent = value;
    row.append(label, val); details.append(row);
  }
  show('hotspotDetailModal', true); renderChart(); renderRoute(); renderReport();
}
function closeHotspotModal() { show('hotspotDetailModal', false); }
function focusChart() { closeHotspotModal(); switchTab('analysis'); byId('analysisSvgChart')?.scrollIntoView({ block: 'center', behavior: 'smooth' }); }

function pucState() {
  const value = byId('pucExpiry').value;
  if (!value) return 'Not added. This reminder is separate from the fuel-use assessment.';
  const expiry = new Date(`${value}T23:59:59`);
  const days = Math.ceil((expiry.getTime() - Date.now()) / 86400000);
  if (days < 0) return 'Expired — check your certificate and arrange an authorised PUC test if needed.';
  if (days <= 30) return `Due soon — ${days} day${days === 1 ? '' : 's'} remaining. This is a driver-entered reminder.`;
  return `Reminder saved — ${days} days until the date you entered.`;
}
function reportFields() {
  const possible = ui.faultAnalysis?.expert_system?.likely_reasons?.[0];
  const suggestedCheck = possible?.reason_code === 'rich_mixture_pattern' ? 'Confirm trims and lambda; test injector leakage and fuel-pressure regulation' : possible?.next_check || 'Review fuel system and relevant diagnostic codes if symptoms persist';
  return [
    ['Vehicle', 'Demo car · illustrative journey'],
    ['Finding', assessment()?.alerts?.length ? 'Sustained fuel-use increase; inspection suggested' : 'No sustained fuel-use alert'],
    ['Possible system issue', possible ? `${possible.reason} · expert system screening` : 'No specific fault indicated'],
    ['Suggested check', suggestedCheck],
    ['Estimated extra fuel', assessment()?.excess_l == null ? 'Unavailable' : `${n(assessment().excess_l, 3)} L`],
    ['Estimated extra CO₂', ui.co2?.kg_per_l == null ? 'Unavailable' : `${n(assessment()?.excess_l * ui.co2.kg_per_l, 3)} kg`],
    ['NOx example', ui.emissions?.nox_ppm == null ? 'Unavailable' : `${n(ui.emissions.nox_ppm, 0)} ppm · synthetic`],
    ['CO example', ui.emissions?.co_pct == null ? 'Unavailable' : `${n(ui.emissions.co_pct, 2)}% · synthetic`],
    ['PUC suggestion', ui.pucAdvisory?.status === 'consider_test' ? 'Consider an authorised test; no PUC result is claimed' : 'Not assessed'],
    ['Evidence quality', `${Math.min(100, Math.round((assessment()?.valid_seconds || 0) / Math.max(ui.sourceTime, 1) * 100))}% valid fuel coverage`],
    ['Selected route area', ui.selectedSection ? 'Highlighted in the app for inspection' : 'No area selected'],
    ['Demo note', 'Fuel and route sample; fault and exhaust readings are synthetic']
  ];
}
function renderReport() {
  set('serviceRecTitle', statusCopy[assessment()?.status]?.[0] || 'Collecting replay evidence');
  const possible = ui.faultAnalysis?.expert_system?.likely_reasons?.[0];
  set('serviceRecText', assessment()?.status === 'inspection' ? possible?.reason_code === 'rich_mixture_pattern' ? 'The expert system suggests over-fueling: fuel correction is negative while the exhaust example remains rich. Ask a technician to check injector leakage and pressure regulation.' : 'The expert system suggests restricted fuel delivery: fuel correction is positive while pressure is low. Ask a technician to test the pump, filter and pressure under load.' : 'No sustained fuel-use alert. This summary updates as the car moves.');
  set('pucState', pucState());
  set('pucAssessment', ui.pucAdvisory?.message || 'No emissions assessment is available.');
  const root = byId('reportEvidence'); root.replaceChildren();
  for (const [name, value] of reportFields()) {
    const row = document.createElement('div'); row.className = 'flex justify-between gap-3 py-1 border-b border-slate-50';
    const label = document.createElement('span'); label.className = 'text-brand-navyMuted'; label.textContent = name;
    const val = document.createElement('strong'); val.className = 'text-right text-brand-navy'; val.textContent = value;
    row.append(label, val); root.append(row);
  }
}
function saveDraft() {
  const ids = ['reportTitle', 'reportObservations', 'reportHistory', 'includeTrip', 'includeChart', 'includeSections', 'includeTechnical', 'pucExpiry'];
  ui.draft = Object.fromEntries(ids.map(id => [id, byId(id).type === 'checkbox' ? byId(id).checked : byId(id).value]));
  localStorage.setItem('driftbeacon-driver-draft-v2', JSON.stringify(ui.draft));
  set('pucState', pucState());
}
function loadDraft() {
  try { ui.draft = JSON.parse(localStorage.getItem('driftbeacon-driver-draft-v2') || '{}'); } catch { ui.draft = {}; }
  for (const [id, value] of Object.entries(ui.draft)) if (byId(id)) {
    if (byId(id).type === 'checkbox') byId(id).checked = Boolean(value); else byId(id).value = value;
  }
  set('pucState', pucState());
}
function reportText() {
  const lines = [byId('reportTitle').value.trim() || 'My fuel-use summary', '',
    'Driver observations: ' + (byId('reportObservations').value.trim() || 'None entered'),
    'Relevant service history: ' + (byId('reportHistory').value.trim() || 'None entered'),
    'PUC reminder: ' + pucState()];
  if (byId('includeTrip').checked) {
    lines.push('', 'Read-only replay evidence:');
    for (const [name, value] of reportFields()) lines.push(`${name}: ${value}`);
  } else lines.push('', 'No trip evidence selected for this report.');
  if (byId('includeTrip').checked && byId('includeSections').checked) lines.push('', 'Driving context: ' + byId('contextExplanation').textContent);
  if (byId('includeTechnical').checked) lines.push('', 'Technical appendix: Sample driving trace and route; estimated fuel; synthetic exhaust and fault signals. Provisional detector and forward-chaining rules. No validated component diagnosis or PUC result. CO₂ factor: ' + (ui.co2?.source || 'Unavailable') + '.');
  lines.push('', 'Please investigate sustained excess fuel use if appropriate. No replacement part is prescribed.');
  return lines.join('\n');
}
function previewReport() {
  saveDraft();
  const root = byId('reportPreviewContent'); root.replaceChildren();
  const pre = document.createElement('pre'); pre.textContent = reportText(); root.append(pre);
  show('reportPreviewSheet', true);
}
function printReport() {
  saveDraft();
  let root = byId('printableReport');
  if (!root) { root = document.createElement('article'); root.id = 'printableReport'; document.body.append(root); }
  root.replaceChildren();
  const title = document.createElement('h1'); title.textContent = byId('reportTitle').value.trim() || 'My fuel-use summary'; root.append(title);
  const pre = document.createElement('pre'); pre.textContent = reportText(); root.append(pre);
  if (byId('includeTrip').checked && byId('includeChart').checked) {
    const heading = document.createElement('h2'); heading.textContent = 'Expected and estimated observed fuel use (L/h)'; root.append(heading);
    const chart = byId('analysisSvgChart').cloneNode(true); chart.removeAttribute('id'); chart.setAttribute('width', '680'); chart.setAttribute('height', '260'); root.append(chart);
    const note = document.createElement('p'); note.textContent = 'Fuel-use trend for the illustrative journey. Missing readings remain gaps.'; root.append(note);
  }
  window.print();
}
async function shareReport() {
  saveDraft();
  const text = reportText();
  if (navigator.share) {
    try { await navigator.share({ title: byId('reportTitle').value || 'Fuel-use summary', text }); }
    catch (error) { if (error.name !== 'AbortError') toast('Sharing was not completed.'); }
  } else if (navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(text); toast('Report copied. You choose where to share it.');
  } else toast('Sharing is unavailable in this browser. Use Preview or Print.');
}

function render() { renderDrive(); renderAnalysis(); renderReport(); }

async function applyPreset(name) {
  if ((name === 'insufficient') !== (ui.trip?.id === 'generated-42-minute-trip')) {
    ui.trip = ui.trips.find(t => t.id === (name === 'insufficient' ? 'generated-42-minute-trip' : 'ved-vehicle-494-trip-1023')) || ui.trips[0];
    configureTrip();
  }
  const onsetMinute = ui.trip?.id === 'generated-42-minute-trip' ? 18 : 4;
  const rampMinute = ui.trip?.id === 'generated-42-minute-trip' ? 6 : 2;
  const presets = { clean: [0, onsetMinute, rampMinute], drift: [15, onsetMinute, rampMinute], severe: [28, onsetMinute, rampMinute], insufficient: [0, 18, 6] };
  const [severity, onset, ramp] = presets[name] || presets.drift;
  byId('sliderDrift').value = severity; byId('sliderStartTime').value = onset; byId('sliderRamp').value = ramp;
  updateSliderLabels();
  await newReplay({ severity_pct: severity, onset_s: onset * 60, ramp_s: ramp * 60 });
  if (name === 'insufficient') await control('seek', { source_time_s: 16 * 60 + 15 });
  else { await control('seek', { source_time_s: 12 * 60 }); await control('play'); }
  show('welcomeScreen', false);
  switchTab('drive');
  toast(name === 'insufficient' ? 'Switched to the missing-data example.' : 'Showing the key moment of this drive.');
}
function configureTrip() {
  const duration = ui.trip?.duration_s || 2520;
  byId('tripScrubber').max = duration;
  byId('sliderStartTime').max = Math.max(1, Math.floor((duration - 60) / 60));
  byId('sliderStartTime').value = Math.min(4, Number(byId('sliderStartTime').max));
  byId('sliderRamp').value = ui.trip?.id === 'generated-42-minute-trip' ? 6 : 2;
  set('chartEndLabel', fmtTime(duration));
  set('tripSourceLabel', ui.trip?.id === 'generated-42-minute-trip' ? 'Synthetic missing-data example' : 'Demo car · sample journey');
  updateSliderLabels();
}
function updateSliderLabels() {
  set('sliderValueDrift', `+${byId('sliderDrift').value}%`);
  set('sliderValueStartTime', `Min ${byId('sliderStartTime').value}`);
  set('sliderValueRamp', `${byId('sliderRamp').value} mins`);
}

document.addEventListener('DOMContentLoaded', async () => {
  byId('emissionsGrid')?.prepend(byId('metricExcessCO2').closest('.bg-white'));
  byId('metricExcessFuel').closest('.bg-white').classList.add('col-span-2');
  byId('driveCoValue').closest('.bg-white').classList.add('col-span-2');
  loadDraft(); updateSliderLabels(); iconRefresh();
  for (const id of ['reportTitle', 'reportObservations', 'reportHistory', 'includeTrip', 'includeChart', 'includeSections', 'includeTechnical', 'pucExpiry']) byId(id).addEventListener('input', saveDraft);
  byId('btnStartRecordedDrive').addEventListener('click', async () => { show('welcomeScreen', false); await control('play'); });
  byId('btnPlayPause').addEventListener('click', () => control(ui.playing ? 'pause' : 'play'));
  byId('btnResetTrip').addEventListener('click', () => control('reset'));
  byId('tripScrubber').addEventListener('change', event => control('seek', { source_time_s: Number(event.target.value) }));
  for (const btn of document.querySelectorAll('.speed-btn')) btn.addEventListener('click', () => control('speed', { speed: Number(btn.dataset.speed) }));
  for (const id of ['sliderDrift', 'sliderStartTime', 'sliderRamp']) byId(id).addEventListener('input', () => { updateSliderLabels(); if (ui.playing) control('pause'); });
  byId('btnOpenDemoControls').addEventListener('click', () => show('demoControlsSheet', true));
  byId('btnCloseDemoControls').addEventListener('click', () => show('demoControlsSheet', false));
  byId('btnApplyAndReplay').addEventListener('click', async () => { show('demoControlsSheet', false); await newReplay(); await control('play'); });
  byId('btnClearDrift').addEventListener('click', async () => { byId('sliderDrift').value = 0; updateSliderLabels(); show('demoControlsSheet', false); await newReplay({ severity_pct: 0 }); await control('play'); });
  byId('fuelPriceInput').addEventListener('input', renderAnalysis);
  byId('analysisSvgChart').addEventListener('click', event => {
    if (!ui.sections?.[channel()]) return;
    const box = byId('analysisSvgChart').getBoundingClientRect();
    const duration = ui.trip?.duration_s || 2520;
    const time = Math.max(0, Math.min(duration, (event.clientX - box.left) / box.width * duration));
    const section = ui.sections[channel()].find(s => s.start_s <= time && time <= s.end_s && s.start_s <= ui.sourceTime);
    if (section) selectSection(section);
  });
  byId('closeHotspot').addEventListener('click', closeHotspotModal);
  byId('viewOnChart').addEventListener('click', focusChart);
  byId('btnPreviewReport').addEventListener('click', previewReport);
  byId('closePreview').addEventListener('click', () => show('reportPreviewSheet', false));
  byId('btnPrintReport').addEventListener('click', printReport);
  byId('btnShareSummary').addEventListener('click', shareReport);
  setInterval(poll, 400);
  try {
    const [health, trips] = await Promise.all([api('/api/health'), api('/api/trips')]);
    ui.health = health; ui.trips = trips.trips; ui.trip = trips.trips[0];
    if (!health.ready || !ui.trip) throw new Error('No eligible trip available');
    configureTrip();
    await newReplay();
  } catch (error) { setConnection('disconnected', error.message); toast(error.message); }
});

window.switchTab = switchTab;
window.applyPreset = applyPreset;
window.hideToast = hideToast;
window.closeHotspotModal = closeHotspotModal;
