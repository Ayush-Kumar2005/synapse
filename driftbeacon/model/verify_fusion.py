"""Verify model/data integration using validation records and labelled test fixtures."""
from __future__ import annotations

import json
from pathlib import Path
import zipfile

import pandas as pd

from .common import ROOT, read_json, sha256, stable_rank, write_json
from .enginefault import DEFAULT_MODEL, RAW, RENAME
from .fusion import FusionEngine
from .sensor_replay import attach_raw_sensors, ved_packets
from .telemetry import signal_dict


def synthetic_packets(engine):
    base = {"speed_kph": 50., "rpm": 2000., "load_pct": 40., "acceleration_mps2": 0.,
            "displacement_l": 2., "weight_kg": 1500.}
    expected = float(engine.fuel_predictor.predict_frame(pd.DataFrame([base]))[0])
    for t in range(241):
        factor = 1 + .8 * max(0, min(1, (t - 80) / 40))
        values = {**base, "observed_fuel_lph": expected * factor,
                  "nox_ppm": 100 + 180 * max(0, min(1, (t - 80) / 40)),
                  "stft1_pct": 3. if t < 100 else 12., "ltft1_pct": 2. if t < 100 else 10.,
                  "coolant_c": 85., "closed_loop": 1.}
        signals = {k: signal_dict(k, v, t, "synthetic",
                                  **({"sensor_id": "demo-nox-1", "position": "downstream"} if k == "nox_ppm" else {}))
                   for k, v in values.items()}
        yield {"vehicle_id": 999999, "trip_id": 1, "source_time_s": t, "mode": "synthetic_demo", "signals": signals,
               "trouble_codes": ["P0171"] if t >= 120 else []}


def main():
    output = ROOT / "reports/replays/fusion"
    output.mkdir(parents=True, exist_ok=True)
    fuel_dir = ROOT / "artifacts/models/gpu_candidate"
    engine = FusionEngine(fuel_dir, DEFAULT_MODEL)
    packets = list(synthetic_packets(engine))
    synthetic = [engine.step(p) for p in packets]
    # Fixtures are visibly synthetic and can exercise the exact JSONL interface.
    examples = ROOT / "examples"
    examples.mkdir(exist_ok=True)
    with (examples / "synthetic_telemetry.jsonl").open("w", encoding="utf-8") as out:
        for packet in packets:
            out.write(json.dumps(packet, allow_nan=False) + "\n")
    pd.DataFrame([{"vehicle_id": p["vehicle_id"], "trip_id": p["trip_id"],
                   "source_time_s": p["source_time_s"], "nox_ppm": p["signals"]["nox_ppm"]["value"],
                   "sensor_id": "demo-nox-1", "position": "downstream", "quality": "valid", "provenance": "synthetic"}
                  for p in packets]).to_csv(examples / "synthetic_nox.csv", index=False)
    pd.DataFrame(columns=["vehicle_id", "trip_id", "source_time_s", "nox_ppm", "sensor_id", "position", "quality", "provenance"]).to_csv(examples / "nox_import_template.csv", index=False)

    data_path = ROOT / "data/prepared/validation.parquet"
    if sha256(data_path) != engine.fuel_predictor.metadata["prepared_checksums"]["validation"]:
        raise ValueError("VED validation fingerprint mismatch")
    data = pd.read_parquet(data_path)
    candidates = [(key, trip) for key, trip in data.groupby(["vehicle_id", "trip_id"], sort=True)
                  if 300 <= trip.source_time_s.max() - trip.source_time_s.min() <= 900]
    key, trip = min(candidates, key=lambda pair: stable_rank(20260926, *pair[0], "fusion_demo"))
    trip = attach_raw_sensors(trip)
    engine.reset()
    real = [engine.step(p) for p in ved_packets(trip)]
    # Source rows are sampled from development validation blocks, never selected
    # for a favourable prediction. The index supplies playback order, not time.
    raw = pd.read_csv(RAW / "EngineFaultDB_Final.csv")
    split = pd.read_csv(DEFAULT_MODEL / "source_row_splits.csv")
    sample_rows = split[split.split.eq("validation")].groupby("fault_label", sort=True).head(8)
    laboratory = []
    for seq, row_id in enumerate(sample_rows.source_row):
        row = raw.iloc[int(row_id)]
        signals = {name: signal_dict(name, row[source], seq, "laboratory_recording") for source, name in RENAME.items()}
        result = engine.step({"vehicle_id": 900000, "trip_id": 0, "source_time_s": seq,
                              "mode": "enginefaultdb_replay", "signals": signals})
        result["reference_label"] = int(row.Fault)
        result["source_row"] = int(row_id)
        result["time_note"] = "Playback index only; dataset has no acquisition timestamps"
        laboratory.append(result)
    metadata = read_json(DEFAULT_MODEL / "metadata.json")
    payload = {"channels": {"VED validation replay": real, "Synthetic combined-sensor scenario": synthetic,
                             "EngineFaultDB laboratory examples": laboratory},
               "fault_model": metadata, "ved_trip": list(map(int, key)),
               "notice": "These are separate sources. The synthetic scenario tests wiring, not detection performance. VED has no measured NOx. Lab rows are not measurements from the VED vehicle."}
    write_json(output / "combined_demo.json", payload)
    summary = {"ved_trip": list(map(int, key)), "ved_points": len(real),
               "ved_nox_available": sum(p["emissions"]["nox_ppm"]["value"] is not None for p in real),
               "synthetic_points": len(synthetic), "synthetic_fuel_alert": synthetic[-1]["fuel"]["alert"],
               "synthetic_nox_alert": synthetic[-1]["nox"]["alert"], "lab_examples": len(laboratory),
               "fault_model_exploratory_accuracy": metadata["exploratory_test"]["accuracy"],
               "ved_test_data_read": False, "uses_detector_v2": False,
               "limitations": payload["notice"]}
    write_json(ROOT / "reports/fusion_demo_verification.json", summary)
    print(json.dumps(summary, indent=2))
    print("Model verification records:", output / "combined_demo.json")


def package():
    """Package code/models/examples/reports; omit raw datasets and VED test data."""
    destination = ROOT / "handoff/driftbeacon-sensor-fusion.zip"
    destination.parent.mkdir(exist_ok=True)
    paths = list((ROOT / "model").glob("*.py")) + list((ROOT / "tests").glob("*.py"))
    paths += list((ROOT / "examples").glob("*"))
    paths += [ROOT / "SENSOR_FUSION.md", ROOT / "README.md", ROOT / "VED_LICENSE.txt", ROOT / "requirements.txt", ROOT / "requirements-gpu.txt", ROOT / "requirements-fusion.txt"]
    paths += [ROOT / "reports" / name for name in ("enginefault_training.json", "gpu_local_compatibility.json", "fusion_demo_verification.json")]
    paths += list((ROOT / "reports/replays/fusion").glob("*.json"))
    for name in ("gpu_candidate", "enginefault_candidate"):
        paths += [p for p in (ROOT / "artifacts/models" / name).glob("*") if p.name != "classifier.joblib"]
    with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as z:
        for path in paths:
            if path.is_file():
                z.write(path, path.relative_to(ROOT))
        z.write(RAW / "LICENSE", "third_party/EngineFaultDB_LICENSE.txt")
        z.write(RAW / "README.md", "third_party/EngineFaultDB_README.md")
    write_json(ROOT / "reports/fusion_package.json", {"file": destination.name, "sha256": sha256(destination), "bytes": destination.stat().st_size,
                                                   "contains_raw_data": False, "contains_ved_test": False})
    print(destination)


if __name__ == "__main__":
    main()
