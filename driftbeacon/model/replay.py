"""Original and explicitly simulated replay, with independent detector state."""
from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path

import numpy as np
import pandas as pd

from .common import ROOT, TARGET, read_json, sha256, write_json
from .detector import DetectorConfig, DriftDetector
from .inference import Predictor


def simulate_observations(times, observed, onset_s, ramp_s, severity):
    if ramp_s <= 0 or severity < 0:
        raise ValueError("Positive ramp duration and nonnegative severity required")
    return np.asarray(observed, dtype=float) * (1 + severity * np.clip((np.asarray(times) - onset_s) / ramp_s, 0, 1))


def run_channel(trip, expected, observed, config, keep_points=False):
    detector = DriftDetector(config)
    points = []
    for row, predicted, fuel in zip(trip.itertuples(index=False), expected, observed):
        result = detector.step(row.vehicle_id, row.trip_id, row.source_time_s, float(predicted), float(fuel), bool(row.model_valid))
        if keep_points:
            point = {"seq": len(points), "source_time_s": float(row.source_time_s), "vehicle_id": int(row.vehicle_id), "trip_id": int(row.trip_id), "speed_kph": float(row.speed_kph), "rpm": float(row.rpm), "load_pct": float(row.load_pct), "expected_fuel_lph": float(predicted), "observed_fuel_lph": float(fuel), "data_quality": row.data_quality, "fuel_provenance": row.fuel_provenance, **result}
            points.append(point)
    return {"alert": detector.alert, "valid_seconds": detector.valid_seconds, "expected_l": detector.total_expected, "observed_l": detector.total_observed, "excess_l": max(0, detector.total_observed - detector.total_expected), "points": points}


def json_safe(value):
    if isinstance(value, dict):
        return {k: json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [json_safe(v) for v in value]
    if isinstance(value, float) and not np.isfinite(value):
        return None
    return value


def load_detector(model_dir):
    bundle = read_json(model_dir / "detector.json")
    if bundle["model_metadata_sha256"] != sha256(model_dir / "metadata.json"):
        raise ValueError("Detector settings belong to another model. Calibrate again on validation.")
    return DetectorConfig(**bundle["config"]), bundle


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--model", type=Path, default=ROOT / "artifacts/models/cpu_baseline")
    p.add_argument("--split", choices=["validation", "test"], default="validation")
    p.add_argument("--vehicle", type=int)
    p.add_argument("--trip", type=int)
    p.add_argument("--severity", type=float, default=0.60)
    p.add_argument("--onset-fraction", type=float, default=0.25)
    p.add_argument("--output", type=Path, default=ROOT / "reports/replays/paired_replay.json")
    args = p.parse_args()
    if not 0 <= args.onset_fraction < 1:
        p.error("onset-fraction must be between 0 and 1")
    if args.split == "test" and not (args.model / "final_test.json").exists():
        p.error("Run the explicit final-test evaluation after freezing model and detector before exploring test replays")
    predictor = Predictor(args.model)
    config, calibration = load_detector(args.model)
    path = ROOT / "data/prepared" / (args.split + ".parquet")
    if sha256(path) != predictor.metadata["prepared_checksums"][args.split]:
        raise ValueError("Prepared data differs from model metadata")
    data = pd.read_parquet(path)
    if args.vehicle is not None:
        data = data[data.vehicle_id.eq(args.vehicle)]
    if args.trip is not None:
        data = data[data.trip_id.eq(args.trip)]
    # Deterministic selection by valid driving duration, independent of alert outcome.
    groups = list(data.groupby(["vehicle_id", "trip_id"], sort=True))
    if not groups:
        raise ValueError("No matching trips")
    key, trip = max(groups, key=lambda pair: float(pair[1].source_time_s.max() - pair[1].source_time_s.min()) * float(pair[1].model_valid.mean()))
    trip = trip.sort_values("source_time_s")
    expected = predictor.predict_frame(trip)
    observed = trip[TARGET].to_numpy()
    start, end = trip.source_time_s.iloc[[0, -1]].to_numpy()
    onset = start + (end - start) * args.onset_fraction
    ramp = max(30, (end - start) * 0.25)
    simulated = simulate_observations(trip.source_time_s, observed, onset, ramp, args.severity)
    original = run_channel(trip, expected, observed, config, True)
    modified = run_channel(trip, expected, simulated, config, True)
    payload = {"split": args.split, "vehicle_id": int(key[0]), "trip_id": int(key[1]), "model": predictor.metadata["selected"], "detector": asdict(config), "calibration_status": calibration["status"], "scenario": {"label": "SIMULATED fuel-observation drift", "onset_s": float(onset), "ramp_s": float(ramp), "severity": args.severity}, "original": original, "simulated": modified, "limitations": "Fuel rate is estimated from MAF/trims. Simulation changes observed fuel only. VED has no labelled engine faults. Signal ages cannot be verified."}
    write_json(args.output, json_safe(payload))
    print(f"Exported {len(trip)} paired points from vehicle {key[0]}, trip {key[1]} to {args.output}")
    print(json.dumps({"original_alert": original["alert"], "simulated_alert": modified["alert"]}, indent=2))


if __name__ == "__main__":
    main()
