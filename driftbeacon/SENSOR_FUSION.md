# DriftBeacon sensor integration

This implementation combines a VED fuel predictor, a separate experimental EngineFaultDB classifier, and an import interface for measured NOx. It runs on the CPU. No further Colab training is required to run these artifacts.

## What is implemented

- The downloaded Colab XGBoost fuel model is installed under `artifacts/models/gpu_candidate`. Its saved median imputer is applied numerically, avoiding a scikit-learn 1.6.1/1.9.1 compatibility failure. Validation MAE reproduces Colab within 0.00001 L/h: **1.03303055 L/h**. No VED test rows were read during this work.
- `model.telemetry` defines sensor units, timestamps, provenance, quality and causal NOx alignment. `model.sensor_replay` imports a VED validation trip and recovers available MAF/fuel trims from exact raw source rows.
- `model.fusion.FusionEngine.step` returns one JSON object containing fuel expectations, fuel drift, exhaust measurements, NOx screening, data quality, experimental fault classification and suggested inspection directions.
- `model.enginefault` downloads a pinned dataset, audits it, trains Random Forest and XGBoost on the CPU, selects using validation, and evaluates the frozen selection on an exploratory block holdout.
- `model.api` serves a local JSON API and browser demonstration. `model.demo_fusion` builds a standalone HTML demo, JSON results and clearly labelled synthetic import fixtures.
- `tests/test_sensor_fusion.py` covers invalid units, future/stale readings, stream isolation, missing channels, NOx reference/persistence, source-block splitting, fault prediction gates, JSON serialization and the HTTP API. All **31 tests** including the original suite passed.

## Start the demo

Open PowerShell in the `driftbeacon` directory. The existing local environment and model artifacts are ready:

```powershell
.venv\Scripts\python.exe -m model.api --port 8765
```

Open `http://127.0.0.1:8765/`. The API binds to your laptop's loopback address. It has no hardware connection, authentication or production deployment configuration. It retains at most 256 vehicle/trip/mode streams in memory; reset between sessions. Restarting clears state. The dashboard uses saved replay results; POSTing live telemetry returns API results and does not automatically replace the dashboard's saved scenarios.

You can also open `reports/replays/fusion/index.html` directly without a server or internet connection. Use **Source**, **Play**, **Reset**, and the timeline slider.

The three sources are deliberately labelled:

1. **VED validation replay:** vehicle 575, trip 1651, 752 records. Selected deterministically by duration and a hash, without selecting for an alert outcome. NOx is unavailable; optional MAF/fuel trims remain available for evidence.
2. **Synthetic combined-sensor scenario:** a 241-point fixture exercises rising fuel observations, rising NOx and changing fuel trims. The fuel screen alerts at 149 s; the NOx screen alerts at 122 s. These times verify wiring, not real-world accuracy.
3. **EngineFaultDB laboratory examples:** 32 rows from development validation blocks. The displayed order is a playback index because the source supplies no timestamps. These rows are not paired measurements from the VED vehicle.

## API and Python integration

| Route | Method | Purpose |
|---|---|---|
| `/health` | GET | Model availability and local-demo status |
| `/v1/schema` | GET | Supported units and a minimal packet example |
| `/v1/telemetry` | POST | Process one ordered sensor packet |
| `/v1/reset` | POST | Reset all in-memory detector state; send `{}` |

POST requests require `Content-Type: application/json`; the size limit is 1 MiB. Invalid packets return HTTP 400, while missing or stale values produce explicit unavailable channels. Send records in strictly increasing `source_time_s` for each `(vehicle_id, trip_id, mode)`. Measurements and packet times must use one agreed clock origin. There is no automatic clock-offset estimation.

Example partial live packet:

```json
{
  "vehicle_id": 7,
  "trip_id": 1,
  "source_time_s": 100.0,
  "mode": "live",
  "signals": {
    "rpm": {"value": 2000, "unit": "rpm", "measured_at_s": 100.0, "provenance": "measured"},
    "load_pct": {"value": 40, "unit": "%", "measured_at_s": 100.0, "provenance": "measured"},
    "nox_ppm": {
      "value": 125, "unit": "ppm", "measured_at_s": 99.6,
      "provenance": "measured", "quality": "valid",
      "sensor_id": "vehicle-7-nox-downstream", "position": "downstream"
    }
  }
}
```

This partial packet can contribute to NOx screening; fuel prediction remains unavailable until its required predictors arrive. `Signal.measured_at_s` records acquisition time, not upload time. Default maximum age is three seconds. Valid qualities are `valid`, `warming_up`, `fault`, and `unavailable`. Positions are `upstream`, `downstream`, or `unknown`. Changing NOx sensor identity, position or provenance resets its reference.

```python
from pathlib import Path
from model.fusion import FusionEngine

engine = FusionEngine(
    Path("artifacts/models/gpu_candidate"),
    Path("artifacts/models/enginefault_candidate"),
)
result = engine.step(packet)  # same dictionary schema as HTTP
```

For file import, supply one packet per line:

```powershell
.venv\Scripts\python.exe -m model.fusion --input examples/synthetic_telemetry.jsonl --output reports/replays/my_fusion_run.jsonl
```

The CLI preserves existing output files; choose a new name for a subsequent run.

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

`diagnosis.systems_to_check` provides inspection directions from actual available evidence: operating conditions and measurement validity after a fuel alert; intake/MAF/fuel delivery checks when large fuel correction is observed; sensor/exhaust investigation after a NOx change. The 15% combined fuel-trim screen is an engineering heuristic, not an OEM fault threshold. A single trim reading does not establish persistence or closed-loop operation.

`confirmed_component` remains null. Neither VED nor EngineFaultDB supplies mechanic-confirmed component/repair labels suitable for learning the exact reason for a VED alert. A NOx increase alone cannot establish a failed catalyst or other component. Required future evidence includes synchronized recordings, operating-temperature context, diagnostic codes, confirmed faults and repair outcomes.

## Existing work from the other chat

That chat reported a final VED evaluation and a detector v2 candidate using baseline correction/CUSUM. Its v2 code/model bundle is not present in this local checkout or the downloaded model ZIP. This implementation explicitly uses the **original threshold detector** and does not claim the other chat's 61.4% validation result. It preserves the downloaded Colab metadata; its historical `test_evaluated: false` field predates the evaluation reported in the other chat. No new final VED evaluation or detector tuning occurred here.

## Files to share

`handoff/driftbeacon-sensor-fusion.zip` includes the two model artifacts, inference code, local API, tests, documentation, examples, reports, standalone demo and EngineFaultDB attribution. It excludes raw datasets and VED test data. Extract into a folder, create a Python environment, install `requirements-fusion.txt`, and run `python -m model.api`.

To rebuild the demo on this prepared laptop:

```powershell
.venv\Scripts\python.exe -m model.demo_fusion
.venv\Scripts\python.exe -c "from model.demo_fusion import package; package()"
```

## Sources

- EngineFaultDB official repository and its bundled GPL-3.0 license: <https://github.com/leoxthomas/EngineFaultDB>.
- Original paper: <https://doi.org/10.1109/ACCESS.2023.3331316>. Its gas analyzer's capabilities do not imply every gas is present in the released CSV.
- Bosch NOx sensor overview: <https://www.bosch-mobility.com/en/solutions/sensors/nox-sensor/>. Outputs depend on the exact sensor; separate instrumentation is required for other gas channels.
- Ford fuel-trim investigation example: <https://www.fordservicecontent.com/Ford_Content/pubs/content/~WT/~MUS~LEN/3602/tsb04-17-04.htm>. Manufacturer-specific procedures motivate inspection directions, not a universal diagnosis rule.
- VED provenance and target assumptions remain documented in `README.md` and `config/preparation.json`.
