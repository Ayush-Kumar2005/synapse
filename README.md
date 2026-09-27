# synapse

DriftBeacon: fuel-efficiency drift detection from VED telemetry.

Model preparation, measured baseline results, detector and replay tools are in
[driftbeacon/README.md](driftbeacon/README.md).

For training on the second laptop, follow
[TRAINING_HANDOFF.md](driftbeacon/TRAINING_HANDOFF.md).
For the complete context to send that person, see
[FRIEND_BRIEF.md](driftbeacon/FRIEND_BRIEF.md).

Model work is on `model-development`. Raw data, prepared Parquet files, local
Python environments and model binaries are excluded from Git. The preparation
scripts reproduce the data; a local training ZIP supports the laptop handoff.

The working Stitch-style phone demo and its local run instructions are in
[DEMO_README.md](driftbeacon/DEMO_README.md).
