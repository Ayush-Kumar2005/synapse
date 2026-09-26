# DriftBeacon model preparation

**Sensor integration update:** See [SENSOR_FUSION.md](SENSOR_FUSION.md) for the CPU-compatible Colab model, trained EngineFaultDB classifier, timestamped NOx import, local API, combined replay demo, measured results and remaining diagnosis limitations. Run `.venv\Scripts\python.exe -m model.api` for the local demo. The preparation-stage descriptions below are historical: the other chat subsequently reported test evaluation; its detector v2 artifacts are not in this checkout.

If you are the person training on the second laptop, start with [FRIEND_BRIEF.md](FRIEND_BRIEF.md), then follow [TRAINING_HANDOFF.md](TRAINING_HANDOFF.md).

This directory contains the completed laptop-side VED preparation, a fitted CPU sanity model, training scripts for the second laptop, and sequential drift-detection/replay tools. Code lives on the `model-development` branch.

## Current outputs

The complete downloaded source contains **22,436,808 rows across 54 weekly files**. The downloaded revision contains 384 dynamic vehicle IDs; the source paper describes 383. The ICE/HEV workbook has 264 ICE and 93 HEV records. These observed counts are recorded rather than replaced with the publication's counts.

We exclude HEVs and vehicles explicitly described as diesel or flex-fuel. Of 262 eligible metadata vehicles, 135 have enough trips meeting the required signal coverage, duration and row-count criteria. The source has 13,082,347 eligible raw rows and 8,268,474 MAF/trim targets before the other filters. **No directly reported fuel-rate rows exist in this selected gasoline population.**

To keep preparation and training practical on a laptop, we retain at most 24 complete published trips per eligible vehicle, selected deterministically across the full source history. The final prepared collection contains 2,590 trips and 2,110,674 rows, of which 2,102,482 are valid model rows. Invalid rows remain in the files so replay can expose gaps and unavailable readings.

| Split | Vehicles | Trips | All rows | Valid model rows |
|---|---:|---:|---:|---:|
| Train | 94 | 1,779 | 1,437,486 | 1,431,997 |
| Validation | 20 | 429 | 392,179 | 390,786 |
| Test | 21 | 382 | 281,009 | 279,699 |

Every vehicle belongs to one split. Seed: `20260926`. The test split has been checked for data integrity but has not been used for model selection, detector selection or prediction evaluation.

## Inputs and target

Predictors, in order: `speed_kph`, `rpm`, `load_pct`, `acceleration_mps2`, `displacement_l`, `weight_kg`. Model inputs exclude MAF, all fuel trims, reported fuel, vehicle/trip IDs, and the target.

The estimated target follows the MAF branch of [VED Algorithm 1](https://arxiv.org/html/1905.02081v1#S2.SS3), with an explicit dimensional conversion:

```text
estimated fuel L/h = MAF g/s × (1 + mean_STFT/100 + mean_LTFT/100)
                     ÷ 14.08 × 3600 ÷ 745
```

The air-fuel ratio 14.08 assumes E10, as discussed in the paper. Density **745 g/L is our engineering assumption**, not a VED measurement; changing it scales estimated litres inversely. The paper's displayed MAF branch produces a mass flow before this conversion. Bank 1 STFT and LTFT must exist. Each bank-2 value is averaged with bank 1 when present. Missing required trims never become zero. We do not use the load/RPM fallback to manufacture targets.

Preparation keeps supplied row values, without interpolation or forward/backward fill. Acceleration uses the latest past speed reading at least 0.5 s and at most 5 s earlier, within the same trip and uninterrupted speed segment. There is no future look-ahead. Required ranges: speed 0–250 km/h, RPM 400–8,000, absolute load 0–300%, acceleration ±8 m/s², displacement 0.5–10 L, MAF 0–1,000 g/s, trims −99–99%. These are documented engineering filters. Absolute load may exceed 100%. Engine-off observations are unavailable for this model. Optional weight outside 500–6,000 kg becomes missing; its median imputer is fitted on training rows only.

VED repeats asynchronously sampled sensor values but does not provide each sensor's acquisition timestamp. Their true ages cannot be verified. The row label `source_values_age_unverified` carries that limitation. This is recorded-data replay; a future live adapter must supply signal timestamps and enforce freshness.

## Measured CPU baseline

The sanity run fits 119,916 valid rows from 154 selected training trips across 57 training vehicles, then scores all 390,786 valid validation rows. The friend-laptop command uses all 1,431,997 prepared training rows.

| Model | Validation MAE, L/h | Validation RMSE, L/h |
|---|---:|---:|
| Training median | 2.7142 | 4.1991 |
| Ridge regression | 1.4366 | 2.0671 |
| Random Forest, selected CPU baseline | 1.1640 | 1.8814 |

The Random Forest is saved locally under `artifacts/models/cpu_baseline`. CPU reloading reproduced saved predictions. These are errors against a derived target, not physical fuel-measurement accuracy or fault-detection accuracy. The `xgboost_cpu_smoke` artifact is a separate small compatibility check, not the selected demo model.

## Detector and replay

The detector integrates expected and observed fuel with trapezoids over source timestamps. The selected settings use a 60 s window and require both at least 25% and 0.5 L/h excess for 30 s, with expected rate at least 0.5 L/h. Missing rows and gaps longer than 5 s break evidence. Alerts latch with a frozen snapshot until reset; trip changes reset all state. Excess litres are `max(0, total observed − total expected)`, not the sum of positive pointwise errors. The detector never receives scenario labels or ramp parameters.

Threshold selection used 60 validation trips from 20 vehicles (at most 3 per vehicle, deterministic selection, duration 300–1,800 s). It selected from 12 fixed threshold combinations with a prespecified nuisance-alert objective of at most 0.2 per valid hour. On these same calibration trips:

- Original observations: 0 latched alerts over 10.566 valid hours.
- Synthetic +30% ramps: 30/60 detected.
- Synthetic +60% ramps: 57/60 detected.
- Total: 87/120 detected, 33 missed, median detection delay 180.025 source seconds among detections.

These are calibration results and are optimistic for generalization. Zero observed alerts in this small selection does not establish a zero false-alert rate. VED trips are not certified fault-free. Synthetic changes affect observed fuel only; they do not simulate the full engine response. The final test is intentionally reserved for the chosen model returned from the training laptop.

The deterministic default replay uses validation vehicle 575, trip 1790 (5,299 points), chosen by duration and coverage, without checking alert outcomes. Original: no alert. Simulated +60% gradual drift: alert at source time 1941.8 s. The output includes separate original/simulated channels, units, data quality, source-time evidence, and nulls for unavailable JSON values.

## Run locally

Use Python 3.13, tested here on 3.13.2. Open a terminal in this `driftbeacon` directory. The local `.venv` is already installed on Adarsh's laptop. On a fresh checkout:

```powershell
py -3.13 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe -m model.acquire
.venv\Scripts\python.exe -m model.prepare
```

The download uses a pinned official source revision. Raw files and prepared datasets are excluded from Git. `model.acquire` records source checksums, and preparation processes one weekly source at a time. Preparation needs a few GB of free disk space. Re-running preparation recreates the data files; preserve the configuration and source revision for the saved models.

```powershell
.venv\Scripts\python.exe -m model.verify
.venv\Scripts\python.exe -m unittest discover -s tests -v
.venv\Scripts\python.exe -m model.train --jobs 1
.venv\Scripts\python.exe -m model.evaluate --calibrate
.venv\Scripts\python.exe -m model.replay
.venv\Scripts\python.exe -m model.package_handoff
```

`train` refuses to overwrite a model directory. The baseline already exists here: skip training to use it, or supply `--output artifacts/models/new_run` and pass that same directory to evaluation/replay. The local CPU check used `--jobs 1` because the execution sandbox restricted Windows worker pipes. Normal terminals can use the default four workers.

`reports/replays/paired_replay.json` is the real-data output for the dashboard developer. `model.inference.Predictor.predict_frame` accepts a DataFrame with the named features. `model.detector.DriftDetector.step` consumes one ordered observation at a time. Playback pacing belongs in the caller; it must send each source point exactly once. A fresh detector produces a deterministic reset. No FastAPI server or frontend is part of this model-preparation deliverable.

## Files and handoff

- `reports/data_audit.json`: full-source counts, missingness, exclusions, source hashes and selection rules.
- `reports/selected_trips.csv`: exact retained trip IDs.
- `artifacts/split_manifest.json`: vehicle split and prepared file checksums.
- `artifacts/feature_schema.json`: inputs, units, target provenance and exclusions.
- `reports/cpu_baseline_training.json`: measured regression results and library versions.
- `reports/cpu_baseline_detector_validation.json`: calibration trials and per-scenario results.
- `reports/data_verification.json`: integrity checks, including vehicle separation and time order.
- `handoff/driftbeacon-training.zip`: portable training package, generated locally and excluded from Git.
- `TRAINING_HANDOFF.md`: exact instructions for the second laptop and the returned model.

## Sources

Official [VED repository](https://github.com/gsoh/VED), pinned to `6baa4963782d515a67d32a5490bd5d11f5d9bf0d`; [VED paper](https://arxiv.org/html/1905.02081v1). Data is distributed under the source repository's Apache 2.0 license; a copy is included as `VED_LICENSE.txt`. No latitude/longitude fields are carried into the prepared training package.
