# Training on the second laptop

Read [FRIEND_BRIEF.md](FRIEND_BRIEF.md) first for the problem, model target, data split, completed work, and your exact deliverables.

Adarsh's preparation tasks are complete. Transfer the local `handoff/driftbeacon-training.zip` to the training laptop and extract it. The archive includes code, configuration, train/validation data, the exact feature schema, checksums, source license, and baseline results. The raw 3.2 GB dataset and the final test data are not required for training and are omitted from the package.

The archive's top-level directory is `driftbeacon`. Run the following commands from inside it, in PowerShell. Use Python 3.13, matching the tested environment.

## 1. Install and verify

```powershell
py -3.13 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements-gpu.txt
.venv\Scripts\python.exe -m model.verify --training-only
.venv\Scripts\python.exe -m unittest discover -s tests -v
nvidia-smi
```

The pinned XGBoost 3.1.3 GPU path requires a compatible NVIDIA CUDA GPU and driver. AMD/Intel graphics do not satisfy this CUDA path. The training command checks GPU availability and rejects a silent CPU fallback. The CPU code path and XGBoost model export/reload were tested here; CUDA execution remains to be verified on the training laptop. See [XGBoost's GPU requirements and device interoperability](https://xgboost.readthedocs.io/en/release_3.1.0/gpu/index.html) and [Windows installation notes](https://xgboost.readthedocs.io/en/release_3.1.0/install.html).

The metadata-specific source test is skipped in the portable archive because the raw source workbook is omitted; it passed on the preparation laptop.

## 2. Train on the frozen training data

```powershell
.venv\Scripts\python.exe -m model.train --algorithm xgboost --device cuda --max-train-rows 0 --output artifacts/models/gpu_candidate
```

This fits a training-median baseline, Ridge regression and XGBoost, compares validation MAE/RMSE, and saves whichever achieves the lowest validation MAE. The output may therefore select a CPU baseline if XGBoost does not improve it. The pipeline uses the same six feature names and the same vehicle split as Adarsh's baseline. All preprocessing is fitted on training rows. It records the winning model, package versions, model hashes, data hashes, validation results, and CPU reload verification.

The default XGBoost candidate uses 400 trees, depth 6, learning rate 0.05 and histogram training. Extensive tuning is deliberately excluded from this handoff. `--max-train-rows 0` uses all prepared training rows. If resources are limited, set a documented cap such as `--max-train-rows 300000`; the script retains whole selected trips and records the actual row/trip/vehicle counts.

If the laptop has no compatible NVIDIA GPU, use `--device cpu`. A GPU is optional for these tabular models. For a new run use a different output directory; the script preserves existing model artifacts.

## 3. Calibrate the detector for this specific model

```powershell
.venv\Scripts\python.exe -m model.evaluate --model artifacts/models/gpu_candidate --calibrate
.venv\Scripts\python.exe -m model.replay --model artifacts/models/gpu_candidate --output reports/replays/gpu_candidate_replay.json
```

Calibration uses validation trips only and records every tried threshold. It labels settings provisional if the nuisance-alert objective is not met. A new predictor changes the residual distribution, so do not reuse the CPU baseline's detector settings. The saved detector checks the exact model-metadata hash.

## 4. Return the full artifact directory

Copy `artifacts/models/gpu_candidate/` back to the same directory under Adarsh's `driftbeacon` checkout. It contains `metadata.json`, `detector.json`, and either `model.json` plus `preprocessor.joblib` (XGBoost) or `model.joblib` (a selected scikit-learn baseline). Also return `reports/gpu_candidate_training.json` and `reports/gpu_candidate_detector_validation.json`.

Keep the directory contents together. Model files and data are excluded from Git; use the ZIP or a shared drive/USB for these artifacts. Source changes belong only on `model-development`.

## 5. On Adarsh's laptop, run the final frozen evaluation

The final test data remains local to the preparation laptop. After choosing the final model using validation results, install the same pinned packages, retain its frozen detector settings, and run:

```powershell
.venv\Scripts\python.exe -m pip install -r requirements-gpu.txt
.venv\Scripts\python.exe -m model.verify
.venv\Scripts\python.exe -m model.evaluate --model artifacts/models/gpu_candidate --final-test
```

GPU drivers are not needed for CPU inference. The final command scores all held-out test vehicles and exercises synthetic ramps at +15%, +30%, +60% with two onset positions. It saves regression error, nuisance alerts per valid original hour, detection rates, early alerts, missed scenarios and source-time delays. It refuses to overwrite a previous final evaluation, and calibration is blocked once that model's final test exists.

Use the test report to describe generalization, not to pick a different model or retune settings. No real mechanical-fault labels exist in VED. Regression measures agreement with a MAF/trim fuel estimate, while simulated ramps measure sensitivity to changed fuel observations.
