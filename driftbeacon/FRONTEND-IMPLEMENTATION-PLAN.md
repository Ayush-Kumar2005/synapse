# DriftBeacon frontend implementation brief

## Objective

Implement the exported Stitch mobile showcase as the working frontend. Preserve its phone frame, desktop companion panel, visual hierarchy, colours, typography, spacing, cards, and three-tab navigation. Change the product behaviour and only the screen areas specified below: a real route map with tappable evidence, a driver-focused explanation of excess fuel use, and an editable driver report. The result must be interactive and driven by replay responses rather than the export's hardcoded calculations.

## Sources of truth and current state

- Visual reference: `reference/code.html` and `reference/screen.png`. The original archive is `../stitch_driftbeacon_mobile_showcase.zip`. Keep these files intact for comparison.
- Existing service: `server.py` exposes health, trips, replay creation, playback control, cursor-based polling, and report routes. `test_replay.py` covers basic detector behaviour.
- Existing `web/` is a separate functional preview. Its layout does not match the Stitch export. Use its API integration and state-handling ideas, but use the Stitch export for the final appearance.
- The current server has **generated development telemetry**, a provisional detector, no GPS coordinates, and no trained VED model. `/api/health` reports `model_loaded: false`. Present this mode visibly until real data and inference are connected.
- The Stitch HTML is a single Tailwind-CDN page with embedded JavaScript. It currently computes fuel values, status, route position, multi-trip bars, and report figures in the browser. Those calculations are visual fixtures, not evidence.

## Implementation sequence

### 1. Recreate the Stitch shell

Port `reference/code.html` into the served frontend, splitting markup, style, and behaviour only where that improves maintenance. Keep the exported mobile welcome screen, Drive/Analysis/Report tabs, bottom navigation, desktop phone showcase, desktop explanation panel, and responsive layout. Match `reference/screen.png` for the welcome state and inspect the exported tab states before replacing their content. The current `web/index.html` is not the visual target.

Replace unsupported fixed copy and identifiers with API metadata or honest unavailable states. Examples in the export include `VED Trip #4829`, `2.0L Turbo`, six comparable trips, `98.4%` coverage, `2.31 kg/L`, continuous CAN-BUS, a confirmed physical cause, and detection weeks before a warning light. Keep the visual treatment, not those unverified claims.

**Done when:** desktop and mobile layouts retain the Stitch visual language; all three tabs open; no sample value is presented as a validated result.

### 2. Connect one replay state across the phone

Use the server session ID, cursor, and sequence numbers. Create a replay from the selected trip and scenario; poll only new points; append each point once. Ignore stale responses after reset or scenario changes. Play, pause, reset, and speed call the server control route. Playback speed changes pacing, not source-time calculations. Switching tabs preserves the current run and selected evidence.

Remove the export's browser-generated prediction, fuel integration, alert decisions, fake trip history, and route interpolation as sources of truth (`getTripDataAt`, `calculateAccumulatedExcess`, and preset-driven status changes). The original and simulated channels must remain independent. Presets may set scenario parameters and start a new run; they may not directly set an assessment.

Maintain separate replay, connection, model, and assessment states. A disconnected or missing-data state must never appear as green/normal. Keep the generated-fixture label visible with the existing backend; switch to recorded VED/model provenance only after those are actually loaded.

**Done when:** every visible KPI, assessment, chart point, and alert matches the active API session; pause/reset/speed work; stale responses cannot mix runs.

### 3. Drive tab

Retain the exported map-hero card, top-down car marker, status card, four KPI cards, one playback timeline, and expandable demo controls. Replace the decorative SVG route with the selected trip's coordinates when GPS data becomes available. The car marker, travelled route, current speed, time, and driving-context label follow the same source-time cursor. Use one timeline, not separate competing progress bars.

Show estimated observed and expected fuel rates in L/h, accumulated excess fuel in L, and estimated additional CO2 in kg. Show a source/method label for CO2. Keep `Collecting`, `Within expected range`, `Watch`, `Inspection recommended`, and `Insufficient data` as plain-language states backed by the server. The synthetic badge stays visible when a modified run is selected.

**Done when:** the car, route progress, figures, and status advance together and stop together; no car position is invented when coordinates are unavailable.

### 4. Analysis tab and route evidence

Replace the exported S1-S5 route buttons with an interactive OpenStreetMap-backed map inside the same card style. First obtain a prepared VED trip whose GPS coordinates, fuel observations, and model predictions share the same timestamps, whether from the backend teammate or by preparing a bounded trip from the official data. The current generated trip has no GPS and cannot be joined to an unrelated route. Render the recorded, de-identified route as selectable time-aligned sections; colour each section by **unexpected excess fuel above expected use**, not total fuel burned. Use a neutral style for insufficient coverage. Keep visible map attribution and respect the OSM tile-use policy. When no usable coordinates exist, show a route-unavailable state and retain the linked time chart; treat the real-map milestone as unfinished.

On tapping a route section, open a bottom sheet with its source-time range, distance when valid, average speed, driving context, expected and estimated observed fuel, net excess fuel, RPM/load context when present, and valid-data coverage. A `View on chart` action focuses the same interval. Selecting a chart evidence window highlights its matching route section. Preserve gaps rather than drawing evidence through missing points.

The API should provide or support computing route sections **server-side** from the same full observation stream used by the detector. Suggested section fields: `id`, `start_s`, `end_s`, `coordinates`, `expected_l`, `observed_l`, `excess_l`, `coverage`, `mean_speed_kph`, `context`, and optional `rpm`/`load_pct`. Compute excess over a valid window as `max(0, integrated observed - integrated expected)`; do not sum only positive pointwise differences. Keep missing values as null.

**Done when:** every coloured route section opens matching, time-aligned evidence and synchronizes with the chart; gaps and missing GPS never become coloured hotspots.

### 5. Explain driving context and possible faults

Lead Analysis with `Why did this alert appear?`. Explain the recorded speed, stopping pattern, acceleration, RPM, and load used to set expected fuel use. Show where excess persisted under comparable conditions, with duration and coverage. Derive `idle`, `stop-and-go`, and `steady cruise` from the trip's own telemetry. Present them as driving context, not proof of external traffic conditions or a mechanical cause. No live traffic feed is part of this scope.

Show trip efficiency in L/100 km only after integrating valid trip fuel and distance; do not divide an instantaneous L/h rate by a single speed sample. Show a trend across comparable trips only when actual eligible trip history exists, with sample count and comparison rules. Otherwise display `Not enough comparable trips yet`. Keep fuel impact, optional user-entered price, and documented fuel-derived CO2 visible. A future projection requires an explicit driving assumption.

Add a conditional `Possible fault pattern` result supplied by the team's separate fault-analysis service. Display a named pattern only when the model's required inputs are present, its output is returned for this exact case, and the training-domain limitation is stated. Otherwise show `Fault analysis unavailable for this trip`. Never infer a component failure from the fuel residual alone. EngineFaultDB is laboratory data from a particular spark-ignition engine; it includes CO but not NO, and its fields are not interchangeable with VED fields. Keep the two dataset identities separate.

**Done when:** the screen explains why driving conditions were considered, what excess remains unexplained, and which conclusions are unavailable. No chart or model result claims to have proved a fault.

### 6. Emissions and PUC states

CO2 is calculated from excess gasoline and a documented conversion factor carried in backend metadata. Remove the export's hardcoded `2.31 kg/L` and disclose the factor source and units used by the actual implementation. NO and CO cards may have a designed unavailable state, but must not display numeric readings until a real source and units are connected. If integrated later, distinguish measured from estimated values and keep their timestamps and provenance.

In Report, add a driver-entered PUC certificate expiry reminder. Show `Not added`, `Due soon`, or `Expired` from the date the driver entered, separate from the fuel/emissions assessment. A concern may say `Consider an authorised PUC test`. Do not generate a PUC pass/fail result or claim that the app issues a certificate. Do not treat NO as a current petrol PUC reading.

**Done when:** absent NO/CO data is visibly unavailable; PUC certificate timing and inspection advice are separate concepts.

### 7. Driver-owned editable report

Keep the Stitch Report card styling. Replace `Technician & Review Pack`, the workshop observation log, technician dropdown, and technician notes with a report composed by the driver. The generated evidence section is read-only: trip/source, alert window, route hotspot selection, fuel chart, driving-context explanation, excess fuel, CO2 estimate, data coverage, detector settings, and an optional compatible fault-model result. Make clear which figures are synthetic when the scenario is active.

Allow the driver to edit the report title, personal observations, relevant service history, and included sections/trips. Store these edits separately from immutable evidence. Provide Preview, Print/Save as PDF, and user-initiated Share actions. Include a technical appendix for a recipient without making the app a technician workspace. The report asks for investigation and does not prescribe a replacement part.

**Done when:** driver edits survive tab changes and printing; they cannot alter detector values; the printed report retains source, simulation, and coverage labels.

### 8. Verification and handoff

- Compare the finished welcome, Drive, Analysis, and Report screens with the Stitch export at phone and desktop widths. Record only the agreed visual changes.
- Verify clean and drift replay outcomes, independent original/simulated states, reset, pause, playback speed, missing readings, and disconnected/model-unavailable states.
- Verify route-section totals against source-time integration, map/chart synchronization, evidence rewind, and no route colouring across data gaps.
- Verify driver notes stay editable while calculated evidence stays read-only. Print a report and inspect the result.
- Run the existing numerical tests and add focused checks for any new server calculations. Do not invent evaluation accuracy or detection rates.
- Update `README.md` with the actual run command, data source, current limitations, and which optional integrations are available.

**Complete when:** a user can start the exported-looking mobile app, replay a trip, inspect a selected route section and its chart evidence, understand the driving-context explanation, and create an editable driver report. Every unavailable integration has a clear state, and the app never presents generated fixture values as VED measurements.

## External references

- VED data: https://github.com/gsoh/VED
- EngineFaultDB: https://github.com/leoxthomas/EngineFaultDB
- OpenStreetMap tile usage and attribution: https://operations.osmfoundation.org/policies/tiles/
- Indian PUC portal: https://puc.parivahan.gov.in/
