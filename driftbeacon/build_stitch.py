"""Rebuild the served phone shell from the untouched Stitch export."""

from pathlib import Path
import re


ROOT = Path(__file__).parent
source = (ROOT / "reference" / "code.html").read_text(encoding="utf-8")


def between(start: str, end: str, replacement: str):
    global source
    a = source.index(start)
    b = source.index(end, a)
    source = source[:a] + replacement + "\n" + source[b:]


def replace(old: str, new: str):
    global source
    if old not in source:
        raise ValueError(f"Missing Stitch text: {old[:70]}")
    source = source.replace(old, new)


between('<!-- JAVASCRIPT: Full interactive simulation logic -->', '</html>',
        '<script defer src="/stitch-app.js"></script>\n')
replace('</head>', '<link rel="stylesheet" href="/stitch-overrides.css"/>\n</head>')

copy = {
    'Subtle Powertrain Fuel Efficiency Drift Detection • 2.0L Turbo Gasoline ICE': 'Demo drive · fuel and emissions monitor',
    'VED Dataset #4829 • Recorded 42 min run (Ann Arbor corridor)': 'Moving car demo · sample route',
    'Live Interactive Phone Simulation': 'Interactive car demo',
    'Spot efficiency drift before it becomes a bigger problem.': 'See when fuel use starts to drift.',
    'Monitors subtle powertrain fuel burn changes by comparing recorded trips against physics-based baseline expectations for gasoline engines.': 'Watch a car move, see its fuel and emissions readings, and explore a possible system issue.',
    'Pre-Fault Early Warning': 'Sustained Excess Alert',
    'Detects gradual 4–15% excess consumption weeks before Check Engine lights trigger.': 'Shows when a simulated fuel-use increase persists above a provisional threshold.',
    'Objective timeline evidence for your technician to isolate mechanical root causes.': 'Choose the evidence and observations you want to share for an inspection.',
    'Recorded VED gasoline trip demo. No live OBD needed.': 'Sample car journey with expert-system screening examples.',
    'Start Recorded Drive': 'Start Demo Drive',
    'VED Trip #4829 • Recorded Run': '<span id="tripSourceLabel">Demo car · sample journey</span>',
    'Active Playback Simulation': 'Trip replay',
    'Steady Highway Cruise': 'Awaiting telemetry',
    '78 km/h': '— km/h',
    'Powertrain fuel burn is tracking baseline model.': 'Collecting trip readings.',
    'No abnormal efficiency loss observed across current speed profile.': 'Assessment will appear as the replay progresses.',
    'Within expected range</span>': 'Collecting baseline</span>',
    '>6.2</span>': '>—</span>',
    '>6.1</span>': '>—</span>',
    '>+0.04</span>': '>—</span>',
    '>+0.09</span>': '>—</span>',
    'VED ICE model': 'Expected for this drive',
    'Est. Fuel Burn': 'Recent fuel use',
    'Nominal</span>': 'Waiting</span>',
    '+0.8% variance': 'Waiting for valid readings',
    '*2.31 kg CO₂ / L gas': '<span id="co2FactorDrive">Factor loading</span>',
    'Notice a divergence? Inspect root factors': 'Notice a divergence? Inspect the evidence',
    'Deep Diagnostics': 'Trip evidence',
    'Efficiency Drift Analysis': 'Fuel-use analysis',
    'Key Telemetry Finding': 'Why did this alert appear?',
    'Fuel use is currently consistent with previous normal baseline runs (+0.8%). Steady speed cruise matches model expectations.': 'The demo will show a stable alert when sustained medium drift is detected.',
    '6.9 vs 6.8 L/100km': 'Unavailable until distance is integrated',
    'Nominal Correlated': 'Not enough comparable trips yet',
    'Extra CO₂ (2.31 kg/L):': 'Estimated extra CO₂:',
    '18:00 (Highway entry)': '<span id="chartOnsetLabel">18:00 (drift begins)</span>',
    '12 L/h</div>': '<span id="chartYMax">Fuel L/h</span></div>',
    '+0.04 L</span>': '—</span>',
    '+0.09 kg</span>': '—</span>',
    'Fuel cost ($/L):': 'Fuel price (₹/L):',
    'value="1.65"': 'value="100" min="0" inputmode="decimal" aria-label="Edit fuel price per litre in rupees"',
    'Projected Monthly Extra:': 'Estimated extra cost:',
    '+$0.52 / mo': '₹0.00 this trip',
    'Inject controlled fuel inefficiency into recorded VED baseline run to evaluate how DriftBeacon surfaces pre-fault anomalies.': 'Change the synthetic fuel-use fault in this car demo. Apply to start a fresh journey.',
    'max="35" min="0" step="1" type="range" value="15"': 'max="30" min="0" step="1" type="range" value="15"',
    '+35% (Severe)': '+30% (High)',
    'How DriftBeacon Detects Powertrain Drift': 'How the DriftBeacon replay works',
    'OBD-II Check Engine lights only illuminate after emissions thresholds fail or components break. DriftBeacon continuously compares real-world recorded driving against powertrain model baselines to uncover subtle fuel waste and physical drag weeks in advance.': 'Follow the moving car, then compare clean, medium and high fuel drift. The expert system suggests a different inspection path for each outcome.',
    'Drive: Passive Background Recording': 'Drive: Follow the replay',
    'Continuous Telemetry': 'Live demo view',
    'During daily trips, fuel burn rate, vehicle speed, load, and ambient temperature are recorded into baseline bins. Non-intrusive statuses keep the driver informed.': 'Watch speed, RPM, load, expected fuel and estimated observed fuel progress together through the trip.',
    'Zero annoying chime alarms during regular driving.': 'Pause, reset and change playback speed at any point.',
    'Isolates exact operating regimes (e.g. steady cruise vs idle) where observed fuel exceeds expected L/h, differentiating driving behavior from mechanical resistance.': 'Explains why an alert appeared, what the expert system suggests, and the checks needed to confirm it.',
    'Historical multi-trip comparison across valid datasets.': 'Comparable-trip figures are labeled synthetic examples.',
    'Report: Driver-Controlled Technician Handoff': 'Report: Driver-owned summary',
    'Handoff Ready': 'Editable draft',
    'Generates an objective evidence dossier with speed, fuel delta, and telemetry coverage. Prompts the technician to inspect physical systems without declaring false part failures.': 'Combines read-only replay evidence with a title, observations and service history written by the driver.',
    'Certified mechanics retain full diagnostic discretion.': 'Print or share only when you choose.',
    'Instant scenario switch': 'Each preset jumps to the key moment',
    'Nominal ICE (0%)': 'No added drift (0%)',
    '3. Inspection': '3. High Drift',
    '+28% divergence': '+28% fuel-use ramp',
    '4. Insufficient': '4. Missing Data',
    'Early calibration': 'Synthetic gap example',
    'DriftBeacon is demonstrated on recorded 2.0L Turbo Gasoline ICE data from the Vehicle Energy Dataset (VED). Extra CO₂ emissions are computed using the EPA/standard factor: <strong>2.31 kg CO₂ / L gasoline</strong>.': 'Demo data: a de-identified sample route and driving trace, estimated fuel values, and synthetic comparison trips, fault signals and exhaust readings. This is an illustration, not a vehicle diagnosis or PUC result.',
    'Live demo view Evaluation': 'Interactive car demo',
    '42:00 (Exit)': '<span id="chartEndLabel">42:00</span> (End)',
    '+15% at min 18': '+15% from min 4',
}
for old, new in copy.items():
    replace(old, new)

# Preserve the exported card and road-area dimensions for the published GPS route.
between('<svg class="w-full h-full" id="heroOsmSvg"', '<!-- Map Overlays -->', '''<div id="driveRouteUnavailable" class="h-full w-full flex flex-col items-center justify-center text-center px-8">
  <div class="demo-car-icon" aria-hidden="true">▰</div>
  <strong class="text-xs text-brand-navy">Car location unavailable</strong>
  <span class="text-[10px] text-brand-navyMuted leading-relaxed mt-1">The demo car will appear with the sample journey.</span>
</div><div id="driveMap" class="hidden absolute inset-0" aria-label="Recorded route and car position"></div>''')
replace('id="tripScrubber" max="100"', 'id="tripScrubber" aria-label="Trip time" max="2520"')
replace('<!-- 4 Compact Readable Telemetry Metrics Grid -->', '''<div class="text-[10px] font-black uppercase tracking-wider text-brand-forest pt-1">Fuel readings <span class="font-medium normal-case tracking-normal text-brand-navyMuted">· sample drive</span></div>
<!-- 4 Compact Readable Telemetry Metrics Grid -->''')
replace('<!-- Quick Action Card to Analysis -->', '''<!-- Measured exhaust channels remain unavailable until aligned sensor readings arrive. -->
<div class="text-[10px] font-black uppercase tracking-wider text-brand-forest pt-1">Emissions readings <span class="font-medium normal-case tracking-normal text-brand-navyMuted">· demo values</span></div>
<div class="grid grid-cols-2 gap-2" id="emissionsGrid">
  <div class="bg-white p-2.5 rounded-2xl border border-brand-borderLight shadow-subtle">
    <div class="text-[10px] text-brand-navyMuted font-semibold">NO / NOx</div>
    <div class="text-lg font-black text-brand-navy font-mono" id="driveNoxValue">—</div>
    <div class="text-[9px] text-brand-navyMuted" id="driveNoxSource">Sensor reading unavailable</div>
  </div>
  <div class="bg-white p-2.5 rounded-2xl border border-brand-borderLight shadow-subtle">
    <div class="text-[10px] text-brand-navyMuted font-semibold">CO</div>
    <div class="text-lg font-black text-brand-navy font-mono" id="driveCoValue">—</div>
    <div class="text-[9px] text-brand-navyMuted" id="driveCoSource">Sensor reading unavailable</div>
  </div>
</div>
<div id="pucDriveCard" class="hidden p-2.5 bg-amber-50 border border-amber-200 rounded-xl text-brand-navy">
  <strong class="text-[11px]">Consider an authorised PUC test</strong>
  <p class="text-[10px] leading-relaxed mt-1" id="pucDriveReason"></p>
</div>
<!-- Quick Action Card to Analysis -->''')

between('<!-- Drift Over Comparable Trips', '<!-- Interactive Chart Container', '''<!-- Illustrative comparison trips; the current row uses the active replay. -->
<div class="bg-white p-3 rounded-2xl border border-brand-borderLight shadow-subtle space-y-2">
  <div class="flex items-center justify-between gap-2"><div class="text-[11px] font-bold text-brand-navy">Drift over comparable trips</div><span class="text-[9px] font-bold text-brand-teal bg-brand-tealLight px-1.5 py-0.5 rounded">EXAMPLE TRIPS</span></div>
  <p class="text-[9px] text-brand-navyMuted">Three synthetic trips for illustration; current trip is calculated from this replay.</p>
  <div id="comparisonTripRows" class="space-y-1.5 text-[10px]"></div>
</div>''')

between('<!-- Driving Condition Breakdown', '<!-- Practical Impact Calculator', '''<!-- Driving context and route evidence in the exported card style. -->
<div class="bg-white p-3 rounded-2xl border border-brand-borderLight shadow-subtle space-y-2">
  <div class="flex items-center justify-between"><span class="text-[11px] font-bold text-brand-navy">Driving conditions considered</span><span class="text-[9px] text-brand-navyMuted">From this trip</span></div>
  <p class="text-[10px] text-brand-navyMuted leading-relaxed" id="contextExplanation">Speed, changes in speed, RPM and engine load will appear here as the replay progresses.</p>
  <div class="grid grid-cols-3 gap-1.5 text-center text-[9px]" id="contextStats"></div>
  <p class="text-[9px] text-slate-400">Context describes vehicle motion; it is not proof of traffic conditions or a failed component.</p>
</div>
<div class="bg-white p-3 rounded-2xl border border-brand-borderLight shadow-subtle space-y-2">
  <div class="flex items-center justify-between"><span class="text-[11px] font-bold text-brand-navy">Route evidence</span><span class="text-[9px] text-brand-navyMuted">Time-aligned sections</span></div>
  <div id="routeMap" class="route-map-unavailable"><strong>Route loading</strong><span>The recorded sections will appear as the trip replays.</span></div>
  <div class="text-[9px] text-brand-navyMuted" id="mapAttribution">No OpenStreetMap tiles loaded</div>
  <div id="sectionList" class="grid grid-cols-3 gap-1.5"></div>
  <p class="text-[9px] text-brand-navyMuted">Map sections and fuel evidence share source timestamps. GPS coordinates are de-identified.</p>
</div>
<div class="bg-white p-3 rounded-2xl border border-brand-borderLight shadow-subtle space-y-2">
  <div class="text-[11px] font-bold text-brand-navy">Expert system finding</div>
  <p class="text-[10px] text-brand-navyMuted" id="faultPattern">Fault analysis unavailable for this trip. No compatible fault-model output was returned.</p>
  <div id="faultBreakdown" class="space-y-1.5 text-[10px]"></div>
  <div class="text-[9px] text-brand-navyMuted">Possible faults are screening suggestions. A technician must confirm the cause.</div>
</div>''')

between('<!-- VIEW 3: TAB - REPORT SCREEN', '<!-- HOTSPOT DETAILS MODAL', '''<!-- VIEW 3: TAB - REPORT SCREEN -->
<div class="hidden flex-1 flex flex-col p-3.5 space-y-3 overflow-y-auto" id="tabContentService">
  <div class="pt-0.5"><div class="text-[10px] font-bold uppercase tracking-wider text-brand-teal">Driver-owned report</div><h2 class="text-sm font-black text-brand-navy">My fuel-use summary</h2></div>
  <div class="p-3.5 bg-white rounded-2xl border border-brand-borderLight shadow-subtle space-y-2" id="serviceRecommendationCard">
    <div class="flex items-center gap-2"><div class="p-1.5 rounded-xl bg-brand-tealLight text-brand-teal"><i class="w-4 h-4" data-lucide="file-text"></i></div><h3 class="text-xs font-bold text-brand-navy uppercase tracking-wider" id="serviceRecTitle">Collecting replay evidence</h3></div>
    <p class="text-xs text-brand-navy leading-relaxed" id="serviceRecText">The report will update with the current replay. A fuel-use alert recommends investigation, not a replacement part.</p>
  </div>
  <div class="p-3.5 bg-white rounded-2xl border border-brand-borderLight shadow-subtle space-y-2.5">
    <div class="flex items-center justify-between text-xs pb-1.5 border-b border-slate-100"><span class="font-black text-brand-navy">Read-only demo evidence</span><span class="text-[9px] font-bold text-brand-teal bg-brand-tealLight px-1.5 py-0.5 rounded">ILLUSTRATIVE DEMO</span></div>
    <div class="report-evidence-grid" id="reportEvidence"></div>
    <p class="text-[9px] text-brand-navyMuted">Calculated figures come from the replay service and cannot be edited here.</p>
  </div>
  <div class="p-3 bg-white rounded-2xl border border-brand-borderLight shadow-subtle space-y-2.5">
    <div class="text-xs font-black text-brand-navy">My report details</div>
    <label class="report-label">Report title<input id="reportTitle" class="report-input" maxlength="80" value="My fuel-use observations"></label>
    <label class="report-label">What I noticed<textarea id="reportObservations" class="report-input" rows="3" placeholder="For example: when the change seemed noticeable"></textarea></label>
    <label class="report-label">Relevant service history<textarea id="reportHistory" class="report-input" rows="2" placeholder="Optional work already done"></textarea></label>
    <div class="text-[10px] text-brand-navyMuted">Include in report</div>
    <label class="report-check"><input id="includeTrip" type="checkbox" checked> Demo journey</label>
    <label class="report-check"><input id="includeChart" type="checkbox" checked> Fuel chart and alert evidence</label>
    <label class="report-check"><input id="includeSections" type="checkbox" checked> Highlighted route area and driving context</label>
    <label class="report-check"><input id="includeTechnical" type="checkbox"> Technical appendix</label>
  </div>
  <div class="p-3 bg-white rounded-2xl border border-brand-borderLight shadow-subtle space-y-2">
    <div class="text-xs font-black text-brand-navy">PUC certificate reminder</div>
    <label class="report-label">Expiry date entered by me<input id="pucExpiry" class="report-input" type="date"></label>
    <p class="text-[10px] text-brand-navyMuted" id="pucState">Not added. This reminder is separate from the fuel-use assessment.</p>
    <p class="text-[10px] text-brand-navyMuted" id="pucAssessment">No emissions assessment is available.</p>
    <p class="text-[9px] text-slate-400">DriftBeacon does not test emissions, issue a certificate, or predict PUC pass/fail. If concerned, consider an authorised PUC test.</p>
  </div>
  <div class="flex gap-2 pb-2"><button id="btnPreviewReport" class="flex-1 py-2 bg-brand-forest text-white text-xs font-bold rounded-xl">Preview</button><button id="btnPrintReport" class="py-2 px-3 bg-slate-100 text-brand-navy text-xs font-bold rounded-xl">Print / PDF</button><button id="btnShareSummary" class="py-2 px-3 bg-slate-100 text-brand-navy text-xs font-bold rounded-xl">Share</button></div>
</div>''')

between('<!-- HOTSPOT DETAILS MODAL', '<!-- DEMO CONTROLS BOTTOM SHEET', '''<!-- Time-section details use the exported bottom-sheet styling. -->
<div class="hidden absolute inset-x-3 bottom-14 bg-white rounded-2xl border border-brand-borderLight shadow-2xl z-50 p-3.5" id="hotspotDetailModal">
  <div class="flex items-center justify-between pb-2 border-b border-slate-100"><h4 class="text-xs font-black text-brand-navy" id="hotspotModalTitle">Time-section evidence</h4><button id="closeHotspot" class="p-1 text-slate-400" aria-label="Close section">✕</button></div>
  <div class="py-2 space-y-1.5 text-[11px]" id="hotspotDetails"></div>
  <button id="viewOnChart" class="w-full mt-1 py-1.5 bg-brand-forest text-white text-xs font-bold rounded-lg">View on chart</button>
</div>
<div class="hidden absolute inset-x-3 bottom-14 bg-white rounded-2xl border border-brand-borderLight shadow-2xl z-50 p-3.5" id="reportPreviewSheet"><div class="flex justify-between pb-2 border-b border-slate-100"><h4 class="text-xs font-black text-brand-navy">Report preview</h4><button id="closePreview" aria-label="Close preview">✕</button></div><div id="reportPreviewContent" class="report-preview-content"></div></div>''')

# The untouched SVG curve and bars are visual fixtures; clear them before the API paints real values.
source = re.sub(r'(<path[^>]+id="(?:pathExpectedCurve|pathObservedCurve)"[^>]*?)d="[^"]*"', r'\1d=""', source)
source = re.sub(r'(<polygon[^>]+id="divergenceArea"[^>]*?)points="[^"]*"', r'\1points=""', source)
source = source.replace('id="sliderDrift" max="35"', 'id="sliderDrift" max="30"')

(ROOT / "web" / "index.html").write_text(source, encoding="utf-8")
