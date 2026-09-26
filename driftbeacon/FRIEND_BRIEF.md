# DriftBeacon: model training brief

**Start here if you have not seen our chat.** You are helping ANKOR train the model for the hackathon project DriftBeacon. The problem statement is:

> Emissions & Fuel Efficiency Anomaly Detector: Build an AI system that monitors live engine sensor data and flags vehicles showing gradual performance drift (rising fuel consumption, abnormal emissions) before it becomes a compliance or cost issue.

Our current demo scope is a gasoline vehicle **fuel efficiency drift detector** using recorded VED sensor data. It predicts the fuel rate expected from driving conditions, compares that prediction with an independently derived fuel observation, and alerts when the excess persists. The demo replays the same trip twice: original observations and a clearly labelled, simulated gradual fuel increase. Neither stream is a live feed. We have not built a pollutant-emissions model, proven compliance prediction, diagnosed components, or trained on real mechanical-fault labels. Any later CO₂ estimate would be derived from excess fuel using a stated conversion factor.

Your assignment is to train a stronger fuel-rate predictor on your laptop, compare it with the supplied baseline using the **same validation vehicles**, calibrate a detector for the selected predictor, and return the saved artifacts. The complete command sequence is in [TRAINING_HANDOFF.md](TRAINING_HANDOFF.md). The model and data details are in [README.md](README.md). The code lives on the [`model-development` branch](https://github.com/Ayush-Kumar2005/synapse/tree/model-development); source changes go there. The training ZIP is a separate local file that Adarsh will transfer to you because the dataset and trained models are excluded from Git.

## What has already been done

- Downloaded the official [VED dataset](https://github.com/gsoh/VED) at commit `6baa4963782d515a67d32a5490bd5d11f5d9bf0d`; audited 22,436,808 raw rows across 54 weekly files and the static ICE/HEV workbook.
- Filtered to ICE vehicles with a known engine displacement, excluding explicitly diesel and flex-fuel records. From 262 eligible metadata vehicles, 135 had enough signal coverage and complete trips for this prepared dataset.
- Selected at most 24 whole trips per eligible vehicle, yielding 2,590 trips. There are **2,102,482 valid model rows**. The prepared ZIP carries the train and validation portions; it does not carry raw CSVs, GPS locations, or final test rows.
- Split by **vehicle ID**, not by random row: 94 vehicles / 1,431,997 valid rows for training; 20 / 390,786 for validation; 21 / 279,699 for final test. The test set stays on Adarsh's laptop and must be used once after the model and detector are frozen.
- Trained a CPU sanity baseline using 119,916 rows from 57 training vehicles: Random Forest validation MAE **1.1640 L/h**, RMSE **1.8814 L/h** against the derived fuel target. The same 390,786 validation rows were used to score each CPU baseline candidate. Your command with `--max-train-rows 0` uses **all prepared training rows**.
- Built a time-aware detector and reproducible paired replay. On the 60 trips used to select thresholds, 87/120 synthetic +30%/+60% ramps were detected and original observations produced no alerts over 10.566 valid hours. These are **calibration results**, not untouched test results or demonstrated sensitivity to real engine faults.
- Verified source hashes, vehicle-disjoint splits, time order, unit conversion, model reload on CPU, detector gap/reset behavior, and the extracted training ZIP. The numerical suite has 12 tests; one test that reads the raw metadata workbook is skipped in the ZIP because that workbook is omitted.

## What the model learns

Each row predicts **estimated fuel use in litres per hour** from these six inputs, in this exact order:

| Feature | Meaning |
|---|---|
| `speed_kph` | Vehicle speed in km/h |
| `rpm` | Engine revolutions per minute |
| `load_pct` | Absolute engine load, which may exceed 100% |
| `acceleration_mps2` | Acceleration computed from earlier readings within the same trip |
| `displacement_l` | Engine displacement in litres from static metadata |
| `weight_kg` | Generalized vehicle weight converted from pounds; missing values are imputed using training data only |

VED almost never provides a direct fuel-rate reading for our selected vehicles. The training target is a **fuel-rate estimate** made from MAF and short/long fuel trims using the [VED paper's MAF branch](https://arxiv.org/html/1905.02081v1#S2.SS3). We then explicitly convert fuel mass flow to L/h with assumed density **745 g/L** and the paper's E10 air/fuel ratio **14.08**. MAF and fuel trims are excluded from the six predictors so the model cannot simply reproduce its target formula. The density is an engineering assumption, not a fuel measurement. The data also lacks acquisition timestamps for each repeated sensor value, so signal freshness cannot be proven from VED.

Training minimizes regression error against this estimated target. It is **not a supervised fault classifier**: there are no known “healthy” or “failing” labels. The alert is a separate sequential detector of sustained `observed estimated fuel rate − model expected fuel rate`. Synthetic ramps modify only the observed side after a specified onset; driving inputs stay fixed. The detector must not see scenario labels or severity.

## Files you receive

Adarsh will send **`driftbeacon-training.zip`** (about 17 MB). Extract it; its single top-level folder is `driftbeacon`. It contains:

- `FRIEND_BRIEF.md` (this file), `TRAINING_HANDOFF.md` (commands), `README.md` (methods and measured results), and `VED_LICENSE.txt`.
- `data/prepared/train.parquet` and `validation.parquet`, plus `split_manifest.json`. The manifest lists the reserved test vehicle IDs and checksum, but the test file is absent.
- `model/` with training, inference, detector, replay, evaluation, verification, and ZIP creation code; `tests/`; `config/preparation.json`; `requirements.txt`; `requirements-gpu.txt`.
- `artifacts/feature_schema.json` and `artifacts/split_manifest.json` for exact feature order, units and population identity.
- `reports/data_audit.json`, `source_manifest.json`, and `cpu_baseline_training.json` for provenance and baseline comparison.
- `handoff_manifest.json` containing hashes for the ZIP's individual files.

If the Windows GPU setup is causing problems, open `colab_train.ipynb` in Google Colab, select an NVIDIA GPU runtime, upload the same ZIP, and run the cells from top to bottom. It installs Colab-compatible dependencies, verifies the train/validation package, runs the same GPU command, calibrates the detector, and downloads the model artifact. Return the artifact ZIP plus the two JSON reports requested below. Colab GPU availability and session duration vary, so record whether the CUDA run actually completed.

There is no need to download VED again or train from the full raw CSVs. Avoid retraining or evaluating on the reserved test vehicles. The current project has model preparation and replay code but **no API endpoints or frontend integration yet**; those are separate project tasks.

## Your steps

1. Extract the ZIP and follow [TRAINING_HANDOFF.md](TRAINING_HANDOFF.md) from inside the extracted `driftbeacon` folder. The commands target **Windows PowerShell and Python 3.13**.
2. Run `nvidia-smi`. XGBoost's `device=cuda` needs a compatible **NVIDIA** GPU and driver. An AMD/Intel GPU cannot use this CUDA path; the same script can run on CPU with `--device cpu`. The code checks that a requested CUDA run did not silently fall back to CPU.
3. Run `model.verify --training-only` and the numerical suite, then `model.train --algorithm xgboost --device cuda --max-train-rows 0 --output artifacts/models/gpu_candidate` as documented. The script scores median, Ridge and XGBoost on the frozen validation set, saves the lowest-MAE candidate, and checks that it reloads for CPU inference. The winning model might be a CPU baseline.
4. Run `model.evaluate --model artifacts/models/gpu_candidate --calibrate`; detector settings must match your newly selected model. Run `model.replay` to check the paired demonstration. Record the actual validation metrics, run duration, training row count, GPU name and any resource constraints. Do not claim the GPU improved accuracy unless the numbers show it.
5. Return the entire `artifacts/models/gpu_candidate/` folder and the matching `reports/gpu_candidate_training.json` and `reports/gpu_candidate_detector_validation.json`. Include your GPU name/driver, Python version, training time and any changed row cap. Do not send only `model.json`: the preprocessor, metadata and detector settings must stay together.

Adarsh then loads your returned artifact on his CPU laptop, runs the **final test evaluation** against the reserved 21 vehicles, and integrates the chosen predictor/detector with the application. If your candidate is worse than the current CPU baseline on validation, report that honestly; the team can keep the baseline.
