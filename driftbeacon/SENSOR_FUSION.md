# DriftBeacon sensor integration

This implementation combines a VED fuel predictor, a separate experimental EngineFaultDB classifier, and an import interface for measured NOx. It runs on the CPU. No further Colab training is required to run these artifacts.

## What is implemented

- The downloaded Colab XGBoost fuel model is installed under `artifacts/models/gpu_candidate`. Its saved median imputer is applied numerically, avoiding a scikit-learn 1.6.1/1.9.1 compatibility failure. Validation MAE reproduces Colab within 0.00001 L/h: **1.03303055 L/h**. No VED test rows were read during this work.
- `model.telemetry` defines sensor units, timestamps, provenance, quality and causal NOx alignment. `model.sensor_replay` imports a VED validation trip and recovers available MAF/fuel trims from exact raw source rows.
- `model.fusion.FusionEngine.step` returns one JSON object containing fuel expectations, fuel drift, exhaust measurements, NOx screening, data quality, experimental fault classification and suggested inspection directions.
- `model.enginefault` downloads a pinned dataset, audits it, trains Random Forest and XGBoost on the CPU, selects using validation, and evaluates the frozen selection on an exploratory block holdout.
- `model.verify_fusion` checks model/data integration using VED validation records, laboratory examples and explicitly synthetic test fixtures. Outputs are JSON reports; no dashboard or server is included.
- `tests/test_sensor_fusion.py` covers invalid units, future/stale readings, stream isolation, missing channels, NOx reference/persistence, source-block splitting, fault prediction gates, forward-chaining safeguards, and JSON serialization. All **36 tests** including the original suite passed.

## Run model processing

Use Python in the `driftbeacon` directory. The local environment and model artifacts are ready. To process a file of ordered sensor packets:

```powershell
.venv\Scripts\python.exe -m model.fusion --input examples/synthetic_telemetry.jsonl --output reports/replays/my_fusion_run.jsonl
```

The example input is synthetic and exists solely to test data processing. Outputs are JSON records; no graphical interface or HTTP API is included. The CLI preserves existing output files, so choose a new name for each run.

Each input line has `vehicle_id`, `trip_id`, `source_time_s`, `mode` and a `signals` dictionary. Each signal supplies `value`, `unit`, `measured_at_s`, `provenance`, and optionally `quality`. NOx additionally requires `sensor_id` and `position`. Accepted units are defined in `model.telemetry.UNITS`. Live signals require measured provenance; historical records and test fixtures retain their distinct provenance.

Timestamps must share one clock origin. Data older than three seconds is unavailable by default. Future samples and invalid units are rejected. Records must arrive in increasing time per vehicle/trip/mode. Signal quality can be `valid`, `warming_up`, `fault` or `unavailable`.

For Python integration, instantiate `model.fusion.FusionEngine` with the fuel and fault model directories, then call `engine.step(packet)`. Each result contains fuel estimates, sensor quality, emission measurements, experimental classifier output and inspection evidence.

## NOx CSV import

Use `examples/nox_import_template.csv`. Required columns:

```text
vehicle_id,trip_id,source_time_s,nox_ppm,sensor_id,position,quality,provenance
```

Use `provenance=measured` for genuine sensor output; `synthetic` is reserved for fixtures. `examples/synthetic_nox.csv` demonstrates the format and is not real hardware data. Select one sensor explicitly; upstream and downstream instruments require separate IDs. Duplicate timestamps for the selected sensor are rejected.

```powershell
.venv\Scripts\python.exe -m model.sensor_replay --vehicle 575 --trip 1651 --nox-csv my_nox.csv --sensor-id vehicle-575-nox --output reports/replays/imported_packets.jsonl
.venv\Scripts\python.exe -m model.fusion --input reports/replays/imported_packets.jsonl --output reports/replays/imported_results.jsonl
```

Only align readings captured from the **same vehicle, trip and clock**. A present-day recording cannot be attached to a historical VED trip as authentic paired evidence. The adapter matches backwards within three seconds, never across vehicles/trips or to a future sample. A repeated NOx measurement can remain visible while fresh but cannot advance the detector's evidence clock.

The NOx screen freezes an assumed reference after 60 seconds of contiguous observations at comparable RPM/load. It flags an increase of both 50% and at least 50 ppm sustained for 30 seconds. A gap over five seconds or substantially different RPM/load breaks persistence. These are explicit engineering settings, **not calibrated performance claims or emissions limits**. RPM/load matching does not control temperature, catalyst state or every confounder. Initial reference readings do not establish a healthy engine.

NOx is reported in ppm. CO, HC, CO2 and O2 are separate optional channels with their own units. Neither available training dataset has a NOx column. No NOx regression model has been trained, no absent gases are invented, and ppm is not converted to g/km without exhaust-flow and distance information.

## Inputs for each model

| Channel | Inputs | Output |
|---|---|---|
| VED fuel regressor | Speed, RPM, load, acceleration, displacement, weight | Expected fuel rate, L/h |
| Fuel observation | Measured fuel when available; otherwise supplied estimated fuel or MAF + bank 1 trims with optional bank 2 trims | Observed/derived rate with provenance |
| Fuel drift detector | Ordered expected and observed rates | Sustained excess fuel evidence |
| NOx screen | Timestamped NOx, sensor position/identity, RPM and load | Experimental concentration-change flag |
| EngineFaultDB classifier | RPM, speed, **measured** fuel L/h, CO %, HC ppm, CO2 %, O2 %, lambda | Uncalibrated class scores with uncertainty/range/domain gates |

VED target assumptions remain E10 AFR 14.08 and density 745 g/L. MAF and trims are excluded from fuel predictors because they generate the target. They can appear in the inspection evidence, with their dependence on the target disclosed. Optional missing weight uses the saved training median. Invalid required predictors never produce a fuel estimate.

We preserve the source CSV's 14 sensor variables, but choose eight for the classifier. Force/power are dynamometer-related; consumption per 100 km and AFR overlap other selected measurements. MAP/TPS are omitted pending clarification of their unusual published numeric ranges and sensor scaling. Adding columns to live telemetry does not automatically add them to a trained model.

## EngineFaultDB results and reproducibility

Official source: <https://github.com/leoxthomas/EngineFaultDB>, revision `c63f23ba048761d9c2a2c95d8fb4c0392a3c63d3`.

CSV SHA256: `70021a35b2b2efb294e49f4c78b80a391a05414d44d4564c429645eb32260763`.

The CSV contains 55,999 rows, 14 numeric sensor columns and four labels. It has no NOx column, acquisition timestamp, vehicle ID or experiment/run ID. Two rows sharing identical classifier predictors are removed before splitting. Within each label's published row order, the first 60% is training, the next 20% validation and final 20% test, with 100-row purges on both sides of each boundary. This reduces immediate row adjacency and exact overlap, but source row order is not verified chronology and blocks are not verified independent experiments.

Random Forest (128 trees) and CPU XGBoost (250 trees) use identical partitions. XGBoost is selected by validation macro F1 (0.426 versus 0.409 for Random Forest). On the frozen exploratory holdout:

- Accuracy: **53.56%**.
- Balanced accuracy: **50.04%**.
- Macro F1: **0.421**.

The report includes the complete confusion matrix and per-class recall. Some classes have poor recall. These results do not justify dependable component diagnosis. They also do not replace the separate VED fuel-regression or simulated drift-detection metrics.

The source paper discusses rich mixture, lean mixture and low voltage; its accessible text does not verify the numeric label mapping from Table 4. Attempts to retrieve the rendered table were blocked. Labels 1–3 therefore remain literal source IDs; label 0 means no fault in that laboratory experiment. Do not guess the component from an ID. A future verified mapping can add names without retraining, but it will not turn induced condition labels into mechanic-confirmed repair labels.

Classifier probabilities are not calibrated. A maximum score under 0.65 or top-two margin under 0.15 returns `uncertain`. Values outside training minima/maxima return `outside_training_ranges`. External/live packets return `unsupported_sensor_domain` even when all fields are present, until sensor scaling and transfer performance have been validated. Missing required inputs return `unavailable`; a VED-derived fuel estimate is never silently substituted for measured laboratory fuel.

To reproduce in a new environment, install `requirements-fusion.txt`, then:

```powershell
python -m model.enginefault --acquire-only
python -m model.enginefault --output artifacts/models/new_fault_run
python -m unittest discover -s tests -v
```

Training refuses to overwrite an existing model. The local selected classifier is `artifacts/models/enginefault_candidate`; XGBoost is stored as portable JSON. Raw data and model weights are excluded from Git. The handoff ZIP contains the model artifacts, source code, examples and reports.

## Evidence and possible causes

### Ranked inspection reasons

The reason module is a **prototype forward-chaining expert system**. It has a fact base built from the alert-window sensor readings, an explicit `FORWARD_RULES` knowledge base, and an inference engine that repeatedly fires enabled IF–THEN rules until no new facts can be added. For example, `warm_closed_loop + positive_trim` asserts `positive_correction_pattern`; that fact plus `lean_dtc` asserts `hypothesis:lean_system_evidence`. The JSON output includes `inference_method`, `asserted_facts`, `knowledge_base_version`, and `rule_trace` so the evaluator can inspect the chain. It does not start from a desired diagnosis or query for evidence to prove it. The rule base is an engineering prototype, not a mechanic-certified diagnostic standard.

`model.reasoning` now evaluates only the frozen 60-second fuel-alert window, using a bounded history of valid, fresh signals. `diagnosis.reasoning.likely_reasons` lists possible reasons in an explicit inspection order. Each entry has its supporting readings, missing checks, whether fuel-estimate dependence weakens the evidence, and a next check. `confirmed_cause` always remains null. The order is a screening heuristic based on independent supporting clues; it is not a fault probability.

The current rules screen for an intake-air-leak pattern (warm closed-loop positive trims stronger at idle), a fuel-delivery pressure issue (positive trims and pressure below a **vehicle-specific configured minimum**), a lean-system pattern (positive trims and a reported lean code), a rich-mixture pattern (negative trims with a rich code or sustained low lambda), a cold-running pattern after at least ten minutes of observed trip time, and a low-voltage pattern with a reported code. These are possible inspection directions. `ReasoningConfig` in `model/reasoning.py` holds the provisional thresholds; only set `fuel_pressure_min_kpa` from the particular vehicle's service specification. Missing or sparse readings do not trigger a rule.

Packets can include `trouble_codes` as a list of uppercase five-character codes such as `P0171`. Optional numeric signals include `closed_loop` (`flag`, 0 or 1) and `fuel_pressure_kpa` (`kPa`). Codes are attributed to the packet timestamp; the feed does not yet distinguish pending, stored, or active codes. The reasoning module needs `closed_loop=1` and sustained warm coolant readings before using fuel trims. No codes or extra diagnostic signals are required for the fuel detector itself.

The synthetic combined-sensor fixture includes warm closed-loop readings and a scripted `P0171` code, so it demonstrates one ranked reason. It is not a real-world validation. The selected VED validation trip has no drift alert and lacks these diagnostic signals, so its `diagnosis.reasoning` remains null. The EngineFaultDB classifier is neither retrained nor used to infer VED causes.

When the detector flags fuel drift, `diagnosis.drift_explanation` reports the frozen evidence window, mean expected and observed or estimated fuel rates, excess litres and percentage, and the fuel observation method. It records whether fuel came from an independent measured sensor or was estimated from MAF and trims. This explains **why the alert fired**, not why the vehicle's fuel use changed. No alert yields `drift_explanation: null`.

The VED fuel model predicts fuel rate from operating inputs, while EngineFaultDB classifies separate laboratory fault states from exhaust and fuel measurements. The classifier cannot turn a VED residual into a named fault: VED lacks its required exhaust features, its numeric fault-state mapping is unverified, and its labels do not identify failed components or repairs. The `root_cause` field therefore remains null even when the laboratory classifier returns a class. Fuel trims used to construct a VED fuel observation cannot independently corroborate the resulting drift.

`diagnosis.systems_to_check` provides inspection directions from actual available evidence: operating conditions and measurement validity after a fuel alert; intake/MAF/fuel delivery checks when large fuel correction is observed; sensor/exhaust investigation after a NOx change. The 15% combined fuel-trim screen is an engineering heuristic, not an OEM fault threshold. A single trim reading does not establish persistence or closed-loop operation.

`confirmed_component` remains null. Neither VED nor EngineFaultDB supplies mechanic-confirmed component/repair labels suitable for learning the exact reason for a VED alert. A NOx increase alone cannot establish a failed catalyst or other component. Required future evidence includes synchronized recordings, operating-temperature context, diagnostic codes, confirmed faults and repair outcomes.

## Existing work from the other chat

That chat reported a final VED evaluation and a detector v2 candidate using baseline correction/CUSUM. Its v2 code/model bundle is not present in this local checkout or the downloaded model ZIP. This implementation explicitly uses the **original threshold detector** and does not claim the other chat's 61.4% validation result. It preserves the downloaded Colab metadata; its historical `test_evaluated: false` field predates the evaluation reported in the other chat. No new final VED evaluation or detector tuning occurred here.

## Files to share

`handoff/driftbeacon-sensor-fusion.zip` includes the two model artifacts, inference code, tests, documentation, examples, JSON reports and EngineFaultDB attribution. It excludes raw datasets and VED test data. Extract into a folder, create a Python environment, install `requirements-fusion.txt`, and run the model-processing command above.

To rerun model verification and rebuild the package on this prepared laptop:

```powershell
.venv\Scripts\python.exe -m model.verify_fusion
.venv\Scripts\python.exe -c "from model.verify_fusion import package; package()"
```

## Sources

- EngineFaultDB official repository and its bundled GPL-3.0 license: <https://github.com/leoxthomas/EngineFaultDB>.
- Original paper: <https://doi.org/10.1109/ACCESS.2023.3331316>. Its gas analyzer's capabilities do not imply every gas is present in the released CSV.
- Bosch NOx sensor overview: <https://www.bosch-mobility.com/en/solutions/sensors/nox-sensor/>. Outputs depend on the exact sensor; separate instrumentation is required for other gas channels.
- Ford fuel-trim investigation example: <https://www.fordservicecontent.com/Ford_Content/pubs/content/~WT/~MUS~LEN/3602/tsb04-17-04.htm>. Manufacturer-specific procedures motivate inspection directions, not a universal diagnosis rule.
- VED provenance and target assumptions remain documented in `README.md` and `config/preparation.json`.

## NOx data plan

No measured NOx training data has been downloaded or used. The current NOx screen is uncalibrated and its test fixtures are synthetic. The next data candidate to audit is RWTH Aachen's gasoline-vehicle dataset, *Real Driving Emissions—Event Detection for Efficient Emission Calibration*: https://zenodo.org/records/11094761 . The repository describes synchronized 1 Hz time traces with speed, measured NOx mass flow in g/s, downstream lambda sensor voltage, and a fuel-cutoff flag. Event files also provide lambda. These are mass-flow readings, not ppm, and must have their own schema and evaluation; they cannot be passed into `nox_ppm`.

Before selecting it for training, inspect the actual files, available full trips, overlap between event extracts, and train/validation/test separation. Its described signals do not include the full VED RPM/load feature set, so it is a candidate for a separate NOx task, not a verified substitute for a synchronized VED-plus-NOx training table. It contains measurements from a different vehicle. No rows will be joined to VED as though they were simultaneous.
