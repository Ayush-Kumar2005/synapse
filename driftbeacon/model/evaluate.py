"""Calibrate on validation only; explicitly evaluate a frozen model on test later."""
from __future__ import annotations

import argparse
from dataclasses import asdict
from itertools import product
from pathlib import Path

import numpy as np
import pandas as pd

from .common import ROOT, TARGET, read_json, sha256, stable_rank, write_json
from .detector import DetectorConfig
from .inference import Predictor
from .replay import load_detector, run_channel, simulate_observations
from .train import metrics


def validation_trips(data, seed):
    selected = []
    for _, vehicle in data.groupby("vehicle_id", sort=True):
        groups = [(key, trip.sort_values("source_time_s")) for key, trip in vehicle.groupby("trip_id", sort=True) if 300 <= trip.source_time_s.max() - trip.source_time_s.min() <= 1800]
        groups.sort(key=lambda pair: stable_rank(seed, int(vehicle.vehicle_id.iloc[0]), int(pair[0]), "detector"))
        selected.extend(trip for _, trip in groups[:3])
    if not selected:
        raise ValueError("No validation trips meet the prespecified detector duration criteria")
    return selected


def score_detector(trips, predictions, config, severities=(0.3, 0.6), onsets=(0.25,)):
    alerts, seconds = 0, 0.0
    scenarios = []
    ids = []
    for trip, expected in zip(trips, predictions):
        observed = trip[TARGET].to_numpy()
        times = trip.source_time_s.to_numpy()
        normal = run_channel(trip, expected, observed, config)
        alerts += int(normal["alert"] is not None)
        seconds += normal["valid_seconds"]
        ids.append({"vehicle_id": int(trip.vehicle_id.iloc[0]), "trip_id": int(trip.trip_id.iloc[0])})
        for severity, fraction in product(severities, onsets):
            onset = float(times[0] + (times[-1] - times[0]) * fraction)
            ramp = float(max(30.0, (times[-1] - times[0]) * 0.25))
            changed = simulate_observations(times, observed, onset, ramp, severity)
            replay = run_channel(trip, expected, changed, config)
            alert = replay["alert"]
            early = bool(alert is not None and alert["detected_at_s"] < onset)
            detected = alert is not None and not early
            # Also report newly surfaced alerts absent from the original trip.
            incremental = detected and normal["alert"] is None
            scenarios.append({**ids[-1], "severity": severity, "onset_fraction": fraction, "onset_s": float(onset), "ramp_s": float(ramp), "detected": detected, "incremental_detection": incremental, "original_alerted": normal["alert"] is not None, "early_alert": early, "delay_s": float(alert["detected_at_s"] - onset) if detected else None})
    delays = [s["delay_s"] for s in scenarios if s["detected"]]
    clean = [s for s in scenarios if not s["original_alerted"]]
    summary = {
        "trips": len(trips), "vehicles": len({i["vehicle_id"] for i in ids}),
        "valid_original_hours": seconds / 3600, "original_trips_with_alert": alerts,
        "nuisance_alerts_per_valid_hour": alerts / (seconds / 3600) if seconds else None,
        "synthetic_scenarios": len(scenarios), "synthetic_detection_rate": sum(s["detected"] for s in scenarios) / len(scenarios),
        "missed_or_preempted_scenarios": sum(not s["detected"] for s in scenarios),
        "early_alert_scenarios": sum(s["early_alert"] for s in scenarios),
        "incremental_detection_rate_on_original_no_alert_trips": sum(s["incremental_detection"] for s in clean) / len(clean) if clean else None,
        "original_no_alert_scenarios": len(clean),
        "median_detection_delay_s": float(np.median(delays)) if delays else None,
        "max_detection_delay_s": float(max(delays)) if delays else None,
        "per_severity": {str(sev): {"scenarios": sum(s["severity"] == sev for s in scenarios), "detections": sum(s["detected"] and s["severity"] == sev for s in scenarios)} for sev in severities},
    }
    return summary, ids, scenarios


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--model", type=Path, default=ROOT / "artifacts/models/cpu_baseline")
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--calibrate", action="store_true")
    mode.add_argument("--final-test", action="store_true")
    args = p.parse_args()
    predictor = Predictor(args.model)
    split = "validation" if args.calibrate else "test"
    path = ROOT / "data/prepared" / f"{split}.parquet"
    if sha256(path) != predictor.metadata["prepared_checksums"][split]:
        raise ValueError("Data fingerprint differs from the training run")
    if args.final_test and (args.model / "final_test.json").exists():
        raise FileExistsError("Final evaluation already recorded for this model. Preserve it; do not tune on test.")
    if args.calibrate and (args.model / "final_test.json").exists():
        raise ValueError("Cannot recalibrate a model after its final test has been opened")
    data = pd.read_parquet(path)
    manifest = read_json(ROOT / "data/prepared/split_manifest.json")
    if set(map(int, data.vehicle_id.unique())) != set(manifest["vehicles"][split]):
        raise ValueError("Split vehicle identity mismatch")
    if args.calibrate:
        trips = validation_trips(data, predictor.metadata["seed"])
        predictions = [predictor.predict_frame(trip) for trip in trips]
        trials = []
        objective_limit = 0.2  # prespecified demo objective, not a compliance threshold
        for relative, absolute in product((0.15, 0.25, 0.40, 0.60), (0.5, 1.0, 2.0)):
            config = DetectorConfig(relative_threshold=relative, absolute_threshold_lph=absolute)
            summary, ids, scenarios = score_detector(trips, predictions, config)
            trials.append({"config": asdict(config), "metrics": summary})
            print(f"validation thresholds {relative:.0%}/{absolute:.1f} L/h: nuisance={summary['nuisance_alerts_per_valid_hour']:.3f}/h detection={summary['synthetic_detection_rate']:.3f}", flush=True)
        feasible = [r for r in trials if r["metrics"]["nuisance_alerts_per_valid_hour"] <= objective_limit]
        if feasible:
            best = min(feasible, key=lambda r: (-r["metrics"]["synthetic_detection_rate"], r["metrics"]["nuisance_alerts_per_valid_hour"], r["metrics"]["median_detection_delay_s"] or float("inf")))
            status = "validation_selected"
        else:
            best = min(trials, key=lambda r: (r["metrics"]["nuisance_alerts_per_valid_hour"], -r["metrics"]["synthetic_detection_rate"]))
            status = "provisional_objective_not_met"
        config = DetectorConfig(**best["config"])
        selected_summary, ids, scenarios = score_detector(trips, predictions, config)
        bundle = {"config": best["config"], "status": status, "model_metadata_sha256": sha256(args.model / "metadata.json"), "validation_sha256": sha256(path), "selection": "At most 3 trips per validation vehicle, 300-1800 s, deterministic SHA256 order; thresholds selected using validation only", "objective_nuisance_alerts_per_hour": objective_limit, "metrics": selected_summary, "trials": trials, "trips": ids, "scenarios": scenarios, "test_evaluated": False, "limits": "Metrics are on calibration data and optimistic for generalization. Original trips are not certified fault-free. One latched alert maximum per trip. Synthetic observed-fuel ramps do not validate real fault detection."}
        write_json(args.model / "detector.json", bundle)
        write_json(ROOT / "reports" / (args.model.name + "_detector_validation.json"), bundle)
        print(f"Detector status: {status}; saved alongside model; test unopened", flush=True)
    else:
        config, bundle = load_detector(args.model)
        valid = data[data.model_valid]
        regression = metrics(valid[TARGET], predictor.predict_frame(valid))
        trips = [t.sort_values("source_time_s") for _, t in data.groupby(["vehicle_id", "trip_id"], sort=True)]
        predictions = [predictor.predict_frame(trip) for trip in trips]
        summary, ids, scenarios = score_detector(trips, predictions, config, severities=(0.15, 0.30, 0.60), onsets=(0.25, 0.50))
        report = {"split": "test", "regression": regression, "rows": len(valid), "detector": summary, "detector_status": bundle["status"], "model_metadata_sha256": sha256(args.model / "metadata.json"), "detector_sha256": sha256(args.model / "detector.json"), "test_sha256": sha256(path), "trips": ids, "scenarios": scenarios, "scope": "Frozen final evaluation; derived fuel target and simulated fuel-observation drift only"}
        write_json(args.model / "final_test.json", report)
        write_json(ROOT / "reports" / (args.model.name + "_final_test.json"), report)
        print(regression, summary, flush=True)


if __name__ == "__main__":
    main()
