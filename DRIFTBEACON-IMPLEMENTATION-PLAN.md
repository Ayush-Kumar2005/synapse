# DriftBeacon — six-hour implementation plan

Prepared for ANKOR, 26 September 2026. Evaluation checkpoint: 11:00 AM IST. Planning time: approximately 10:19 AM IST; about 41 minutes remain before that checkpoint. Total event start/end time has not been supplied, so later milestones are relative to the build start and must fit the actual remaining event time.

## 1. Outcome and committed scope

Build a driver-focused application that replays engine telemetry, predicts typical fuel consumption for the driving conditions, detects sustained excess, and explains why an inspection may be warranted. A technician can use the resulting evidence summary.

The core demonstration is a paired replay of the same held-out trip: original observations alongside a copy with explicitly simulated gradual fuel-use drift. Both pass through the same frozen model and detector, using independent detector state.

Committed: gasoline-only VED data, sequential replay, expected-versus-estimated-observed chart, persistent alerts, evidence rewind, estimated excess fuel, and visible data provenance.

After the evaluation checkpoint: cost/CO2 estimates, technician export, broader held-out evaluation, and interface refinement.

Excluded: HEVs and PHEV/EVs, fleet management, accounts, maps, live OBD integration, component diagnosis, compliance verdicts, failure-date prediction, separate CO/HC emissions model, and chatbot.

This demonstrates sensitivity to synthetic fuel-observation drift, not validated early detection of real mechanical faults. VED provides typical operation rather than a certified healthy reference. Use “Within expected range,” not “Engine healthy.”

## 2. Ownership and critical path

| Owner | Responsibilities | First deliverable |
|---|---|---|
| Builder 1 | Data audit, target calculation, model, detector, FastAPI, evaluation | A real prepared trip and executable baseline |
| Builder 2 | React/Vite interface, API integration, replay UX, evidence, pitch | Dashboard wired to the agreed response format |
| Both | Response contract, integration, rehearsal | One complete end-to-end flow |

Start a separate `driftbeacon/` directory when implementation begins. The current workspace contains an unrelated recycling application; do not replace or repurpose it accidentally. Agree on the response contract first. Each builder owns separate directories and checks in integration changes frequently.

Critical path: usable data → defensible target → baseline prediction → sequential detector → visible evidence. Codex accelerates implementation, but each builder remains responsible for checking their outputs.

## 3. Checkpoint at 11 AM

| Deadline, IST | Builder 1 | Builder 2 |
|---|---|---|
| 10:25 | Confirm readable VED sample, gasoline IDs, units, target coverage | Scaffold one dashboard; use clearly labelled development fixtures |
| 10:40 | Fit/save a small baseline and return real predictions | Connect API and plot returned values |
| 10:50 | Add persistent detector and synthetic ramp replay | Add paired replay, simulation label, alert explanation |
| 10:55 | Verify complete flow together | Rehearse and record a backup |
| 11:00 | Present working checkpoint and current evidence | Explain remaining evaluation work |

At this checkpoint, one vehicle/trip and one working chart are sufficient. If time runs short, use a single chart with Original/Simulation selection before adding two simultaneous panels. Keep evidence rewind as a simple “jump to alert” chart action.

If real predictions are not ready by 10:40, present genuine data exploration/replay and identify model integration as unfinished. Development fixtures must never appear as trained-model outputs in evaluation mode. If thresholds have not been validated, label them provisional demo settings. Do not invent validation numbers.

## 4. Six-hour work budget

These are cumulative milestones from the actual build start, not six additional hours after 11 AM. Compress or omit later scope according to the event clock.

| Elapsed | Deliverable |
|---|---|
| 0:00–0:15 | Assign ownership, confirm contract, initialize separate project |
| 0:15–1:00 | Data audit/prepared subset; frontend shell and sample contract |
| 1:00–2:00 | Baseline and small Random Forest; validation comparison; save selected model |
| 2:00–3:00 | Replay, optional personal offset calibration, persistent detector, actual frontend integration |
| 3:00–4:00 | Validation-only threshold selection; paired simulation; alert evidence |
| 4:00–5:00 | Freeze parameters; final held-out evaluation; impact estimates and report if time permits |
| 5:00–6:00 | Feature freeze, end-to-end verification, rehearsal, backup recording |

Stop model experimentation at the two-hour mark. Retain the simpler baseline if the Random Forest does not improve validation performance. If data preparation blocks progress, narrow the dataset before adding model complexity.

## 5. Data and training pipeline

### Acquire and audit

Use the official VED repository and its static vehicle metadata. Verify archive links, extraction support, actual column names, units, timestamp conventions, missing-value markers, and the gasoline classification before implementing transformations.

The repository currently distributes two compressed dynamic-data parts, approximately 79 MB and 89 MB. Published uncompressed data is much larger; process a small subset first. Select a bounded collection of whole trips across multiple eligible vehicles rather than loading every row into memory.

Write `data_audit.json` containing source files, counts by vehicle/powertrain, usable target rows, missingness by required signal, chronological coverage, and exclusions. Audit raw fuel-rate availability rather than treating the fleet count as the training population.

### Fuel target and features

The VED paper reports sparse directly populated fuel rate for ICE/HEV. Prefer a consistent MAF/fuel-trim-based estimated target for the initial pipeline, following the paper's documented formula, sensor semantics, units, and assumptions. Record the formula and constants in model metadata. Verify the conversion on several hand-checked examples before training.

Do not silently mix measured and derived targets. If a direct-fuel-only experiment becomes viable, report it separately. Do not use load/RPM fallback fuel estimates as targets while also feeding their generating variables into the predictor.

Candidate predictors: speed, RPM, absolute load, acceleration from valid consecutive timestamps, engine displacement, and available generalized vehicle weight. Exclude MAF and fuel trims when they generate the target. Omit optional sparse signals for the first model. Fit imputers/encoders on training data only; do not impute the fuel target.

Sort within vehicle/trip, resolve duplicate timestamps, reject invalid time deltas, and never derive acceleration across trip boundaries. Use bounded, causal alignment of asynchronously sampled signals. Long gaps and stale required inputs produce insufficient-data output, not confident predictions. Document freshness limits in configuration.

### Splits and models

- Assign eligible vehicles to roughly 70% training, 15% validation, and 15% final test with a fixed seed; save vehicle IDs. Counts depend on the usable subset.
- Keep entire vehicle histories in their assigned groups. Do not use random row splits.
- Compare a simple fitted regression baseline against a small Random Forest on validation MAE/RMSE in L/h. Bound samples and training time; skip extensive tuning.
- Select features/model and detector settings without inspecting final test outcomes.
- For personalization, estimate a constant prediction offset from an earlier reference period for each held-out vehicle. Exclude that period from scoring; freeze the offset for later trips. If history is insufficient, retain population predictions and disclose “Personal baseline unavailable.”
- Save the pipeline, feature schema, units, split manifest, seed, target provenance, calibration rules, and evaluation summary.

A low regression error against an estimated target does not establish physical fuel-measurement accuracy. Driving-condition inputs may themselves change with a real fault; the synthetic replay does not validate performance under all such changes.

## 6. Detector and impact calculations

For each valid interval, calculate predicted fuel rate, adjusted by any frozen personal offset, and residual `observed_lph - expected_lph`. Integrate rates using actual source-time deltas. Use a time-aware smoother or rolling time window and require a minimum duration/coverage before evaluating sustained excess.

Tune window length, excess threshold, persistence, and recovery hysteresis on validation trips only. Configuration must be saved with the model. Prevent division by near-zero expected fuel: require adequate expected consumption and valid coverage, otherwise withhold percentage-based alerts.

States: Collecting baseline; Within expected range; Watch; Inspection recommended. Insufficient data overrides confident assessment. On trip changes reset transient detector state; a frozen vehicle reference may persist. Gaps break persistence rather than counting as continuing evidence. Before evaluation, keep an inspection alert latched until replay reset to avoid confusing flicker; automatic recovery can be added after validation.

Define excess litres over a valid reporting window as `max(0, integrated observed fuel - integrated expected fuel)`. Do not sum only positive pointwise errors: that inflates waste from ordinary prediction noise. Freeze each alert's evidence snapshot when triggered. Show coverage for partial windows and avoid implying full-trip totals when data is incomplete.

After the checkpoint:

- Estimated extra cost = estimated excess litres × user-supplied price per litre.
- Estimated extra CO2 = estimated excess litres × documented gasoline CO2 factor; verify its source and units before enabling.
- Optional future-cost projection uses an explicit driving assumption and says “if this excess continues.” No default invented local fuel price.

## 7. Replay and scenario design

Replay a held-out trip sequentially. Playback speed changes wall-clock pacing only; detector persistence and integration use source timestamps. Pause freezes replay advancement. Reset reconstructs all replay/detector state deterministically.

The simulation layer applies a gradual fractional ramp to estimated observed fuel rate over a selected source-time interval. It leaves driving-condition inputs unchanged. Both original and modified observations use the same expected-fuel model. This is a sensitivity test of the detector, not a physically complete engine simulation.

The detector accepts telemetry/observations only, never scenario labels, severity settings, or ground-truth onset. The replay controller stores those separately for display and evaluation. Controls change the observation stream, never alert state directly. Changing a scenario parameter requires a fresh replay run.

## 8. Minimal API contract

Use simple polling initially; WebSockets are optional. The server owns ordered replay progression and detector state. Return every sample since the client's cursor so display polling cannot drop evidence. Process each source sample once, independent of browser polling frequency. Use separate session IDs for independent runs.

Suggested routes:

- `GET /api/health` — backend readiness and model loaded state.
- `GET /api/trips` — eligible demo trips and provenance.
- `POST /api/replays` — create original/paired replay using trip ID and simulation settings.
- `POST /api/replays/{id}/control` — play, pause, reset, playback speed.
- `GET /api/replays/{id}?after_seq=N` — new points, state, impacts, frozen alerts, cursor.
- `GET /api/replays/{id}/report` — later: downloadable evidence summary.

Point fields: `seq`, `source_time_s`, `vehicle_id`, `trip_id`, `speed_kph`, `rpm`, `load_pct`, `expected_fuel_lph`, `observed_fuel_lph`, `smoothed_excess_lph`, `status`, `data_quality`, `fuel_provenance`.

For paired mode, return `original` and `simulated` channels with independent detector state and shared source time. Session metadata carries scenario details separately from detector input. Missing values are JSON null, never fabricated zeros or NaN.

Alert fields: `id`, `detected_at_s`, `evidence_start_s`, `duration_s`, `window_expected_l`, `window_observed_l`, `excess_l`, `excess_pct` when valid, `valid_coverage`, and a deterministic explanatory sentence. No uncalibrated “AI confidence” percentage.

## 9. Suggested project structure

```text
driftbeacon/
  README.md
  backend/
    app/                 # API, replay, inference, detector, schemas
    scripts/             # audit, prepare, train, evaluate
    tests/               # critical numerical/state checks
    requirements.txt
  frontend/
    src/                 # dashboard, controls, charts, evidence, API client
    package.json
  data/                  # local raw/prepared data; exclude from Git
  artifacts/             # model, metadata, splits, measured metrics
  reports/               # evaluation outputs and optional technician export
```

## 10. Verification and completion criteria

Prioritize meaningful checks: no split overlap; feature/target lineage audit; known constant-rate integration; no integration across long gaps; playback speed does not change source-time detection; reset clears state; original replay is unchanged by simulation settings; missing data cannot become a healthy reading.

Final evaluation records MAE/RMSE, false alerts per valid original-replay driving hour, number of trips/vehicles tested, synthetic detection rate and source-time delay across multiple ramp severities/onsets, and missed detections. Untouched VED trips are a reference for nuisance-alert measurement, not certified fault-free ground truth. Report small sample sizes and provisional settings explicitly.

Demo-ready means: a real prepared VED trip reaches an actual fitted predictor; chart values come from the API; simulation passes through the detector; an alert exposes immutable evidence; labels remain visible; the app can pause/reset; no fabricated metrics appear.

## 11. Pitch and boundaries

90 seconds: explain the driver problem; show the original trip; introduce labelled simulated deterioration; open the alert evidence; state measured evaluation results and next validation step. Show real results even when imperfect. Do not claim diagnosis, compliance prediction, or production live connectivity.

Sources: [VED repository](https://github.com/gsoh/VED), [VED paper](https://arxiv.org/pdf/1905.02081.pdf). Download sizes are approximate and should be verified at acquisition.
