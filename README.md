# synapse

DriftBeacon: fuel-efficiency drift detection from VED telemetry.

Model preparation, measured baseline results, detector and replay tools are in
[driftbeacon/README.md](driftbeacon/README.md).

For training on the second laptop, follow
[TRAINING_HANDOFF.md](driftbeacon/TRAINING_HANDOFF.md).

Model work is on `model-development`. Raw data, prepared Parquet files, local
Python environments and model binaries are excluded from Git. The preparation
scripts reproduce the data; a local training ZIP supports the laptop handoff.
