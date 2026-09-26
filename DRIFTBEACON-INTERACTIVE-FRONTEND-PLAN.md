# DriftBeacon — interactive frontend specification

Companion: [Implementation plan](DRIFTBEACON-IMPLEMENTATION-PLAN.md). Target: individual driver, with technician-readable evidence. Evaluation checkpoint: 11 AM IST, 26 September 2026.

## 1. Experience goal

In one screen, answer: Is my fuel use within the expected range? What changed? Why did the app alert? What should I do next?

The distinctive interaction is a judge-controlled, explicitly synthetic drift experiment followed by evidence rewind. Every displayed prediction and alert must come from the backend. This is a functional replay application, not a scripted chart animation.

## 2. Single-screen layout

```text
DRIFTBEACON by ANKOR                       VED REPLAY • Gasoline
Vehicle / Trip                            Data quality: Adequate

[ Within expected range / Watch / Inspection recommended       ]
[ Plain-language explanation and recommended next action       ]

[ Play/Pause ] [ Reset ] [ Playback speed ] [ Source trip time ]

 ORIGINAL TRIP                         SIMULATED DRIFT
 Expected vs observed chart            Same trip, modified fuel observations
 Current assessment                    Independent assessment

[ Estimated excess fuel ] [ Excess over expected ] [ Coverage ]

[ Why this alert? → evidence drawer / jump to evidence window   ]

 DEMO LAB — SYNTHETIC FUEL-USE DRIFT
 [ Ramp severity ] [ Ramp onset ] [ Ramp duration ] [ Apply & reset ]

 Later: [ Petrol price ] [ Extra cost / CO2 ] [ Technician summary ]
```

Before 11 AM, a single chart with Original/Simulation tabs is an acceptable fallback. Both channels must still have independent state. On smaller screens, stack chart panels and keep controls reachable.

## 3. Priority and acceptance criteria

| Priority | Feature | Acceptance criterion |
|---|---|---|
| P0 | Real API integration | Model state and actual replay values reach the screen; fixtures visibly marked during development |
| P0 | Ghost baseline chart | Expected and estimated observed fuel share time and L/h axes; legend/provenance always visible |
| P0 | Replay controls | Play, pause, reset, and speed work without changing source-time detection results |
| P0 | Drift challenge | Applying simulation settings resets the run; controls modify observations, not alert state |
| P0 | Alert evidence | Clicking the alert focuses its evidence window and explains sustained excess |
| P0 | Honest states | Loading, insufficient data, disconnected, and model unavailable are distinct from normal operation |
| P1 | Paired panels | Same source-time cursor and shared comparable axis scale across original/simulated charts |
| P1 | Impact calculator | User price changes cost only; it cannot affect the detector |
| P1 | Technician summary | Report contains evidence and provenance, without component-diagnosis claims |
| P2 | Convenience polish | Keyboard shortcuts, subtle transitions, chart zoom and report styling |

Build in this order: shell and API contract → real chart → replay → status → simulation → evidence → paired layout → impacts/export. Do not spend the checkpoint window on custom illustrations or decorative animation.

## 4. Visual direction

Use a restrained automotive instrument style: dark navy background, readable neutral cards, cyan expected-fuel line, amber estimated-observed line, and a shaded excess region. Inspection state uses red; adequate/within-range state can use teal. Always pair color with text and icons.

Use large readable chart labels, tabular numerals, and units next to values. Keep one dominant chart area and minimal navigation. Avoid decorative gauges that consume space without explaining deviation. Use familiar terms: “Expected fuel use,” “Estimated observed fuel use,” “Sustained excess,” and “Inspection recommended.”

Provide adequate contrast, visible keyboard focus, labelled controls, reduced-motion support, and a layout usable on a laptop projector. Never show estimated measurements with misleading precision.

## 5. Core interactions

### A. Ghost baseline

- Plot backend expected and observed L/h against source elapsed time.
- Tooltip shows timestamp, expected/observed values, speed, RPM, and data provenance.
- Preserve visible gaps where required readings are missing; do not interpolate them as measured evidence.
- Use an alert marker at detection time and separately indicate the evidence-window start. Do not imply the detector knew the onset in advance.
- Downsample for rendering only; inference and impact calculations use full prepared observations on the server.

### B. Judge-controlled drift challenge

- Simulation settings: fractional fuel increase, onset, and ramp duration. Proposed UI range is 0–30% increase; this is a sensitivity-test range, not a medically/mechanically validated severity scale.
- Clearly label the control group “Synthetic fuel-use drift.” Explain that driving-condition inputs are held fixed.
- Applying changed settings creates/resets a run. Pause playback while settings are being edited; do not retroactively change earlier chart values.
- Original trip remains untouched. The simulation's state can remain within expected range, alert, or fail to detect; no predetermined success animation.
- Show simulated onset as demo ground truth separately from detector evidence, with a different marker style.

### C. Evidence rewind

- Alert card action: “Show why.” Pause replay and focus the recorded evidence window.
- Drawer shows source-time duration, expected/observed integrated fuel, excess, coverage, target provenance, and detector version/config reference when useful in export.
- Keep historical evidence frozen. Returning to “Current replay” resumes the live view; inspecting history never mutates detector state.
- If no alert exists, disable rewind and explain that no sustained deviation has been detected.

### D. Later: personal impact

- Show accumulated excess litres first. Add cost only after the user supplies a price per litre.
- CO2 is a fuel-derived estimate with its conversion assumption available in a tooltip.
- If adding a future projection, explicitly ask for driving assumptions and use “if this pattern continues.” Do not present it as a forecast of mechanical failure or guaranteed savings.

### E. Later: technician handoff

- Use a printable HTML summary with browser “Save as PDF” to avoid a new PDF service during the hackathon.
- Include vehicle/trip identifiers, source and simulation labels, alert time/window, chart, estimated excess, data coverage, and “Inspect the cause of sustained excess fuel use.”
- Do not prescribe a replacement part or claim a verified engine fault. Clearly identify simulated reports.

## 6. UI and backend state separation

Keep backend assessment separate from browser connection and replay state.

Replay states: loading, ready, playing, paused, completed, error.

Connection/model states: connected, disconnected, model unavailable.

Assessment states: collecting baseline, within expected range, watch, inspection recommended, insufficient data.

Examples of deterministic copy, populated only from actual returned evidence:

- Collecting baseline: “Collecting enough valid readings to assess this trip.”
- Within expected range: “Fuel use is within the configured expected range for this replay window.”
- Watch: “Fuel use is above expected. Checking whether the difference persists.”
- Inspection recommended: “Sustained excess fuel use detected. Review the evidence and consider an inspection.”
- Insufficient data: “Not enough fresh readings for a reliable assessment.”
- Disconnected: “Replay connection interrupted. Displaying the last received reading.”

Do not replace disconnected/missing readings with zeros or retain a green status without a stale-data warning. Do not invent a confidence percentage.

## 7. Suggested React components

```text
App
  DashboardHeader          vehicle, trip, replay/provenance badges
  VehicleAssessment        state and next action
  ReplayControls           play, pause, reset, speed, elapsed time
  ComparisonWorkspace
    FuelChart              reusable original/simulated chart
  ImpactSummary            excess litres and later cost/CO2
  AlertEvidenceDrawer      frozen evidence and rewind action
  SimulationControls       explicit demo-lab settings
  DataQualityDetails       coverage, gaps, target provenance
  TechnicianReport         later printable view
```

Use a familiar React chart library already available to the team; avoid switching libraries for decoration. Keep API polling/session state in one hook or small store. Use server session ID, cursor, and sequence numbers to append samples once. Cancel stale requests when switching runs; ignore responses from a previous session. A refresh should either reconnect to an explicit session or start cleanly, not mix runs.

## 8. Demo script

1. “This is recorded VED telemetry arriving sequentially, not a live car connection.”
2. Play original trip: point to the expected line responding to changing driving conditions.
3. Ask the judge to select a synthetic ramp severity; apply and replay from the start.
4. Observe the actual detector result. If an alert appears, click “Show why” and inspect its evidence window.
5. Show excess litres and any implemented impact estimates. Explain that the driver receives inspection evidence, not a diagnosis.
6. End with actual held-out metrics, or explicitly state which evaluations remain incomplete.

## 9. Frontend verification checklist

- Actual prediction values render with correct units and provenance.
- Original and simulated traces share source timestamps and comparable scales.
- Pause freezes progression; reset clears charts, evidence, and backend detector state.
- Playback speed affects pacing only.
- Scenario controls cannot directly toggle inspection status.
- Missing points create gaps; connection failures are visible.
- Evidence rewind opens the stored window without recalculating its results.
- Data-quality and simulation labels remain visible during the pitch and in exports.
- Browser console is free of blocking errors; basic laptop/mobile layouts work.
- A locally runnable copy and short backup recording exist before evaluation.
