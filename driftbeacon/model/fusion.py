"""Combine fuel drift, measured emission channels and experimental fault evidence.

CLI: python -m model.fusion --input telemetry.jsonl --output results.jsonl
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

from .common import FEATURES, ROOT
from .detector import DriftDetector
from .emissions import NoxConfig, NoxMonitor
from .enginefault import DEFAULT_MODEL, FaultPredictor
from .inference import Predictor
from .replay import load_detector
from .telemetry import Packet, UNITS


def fuel_observation(values):
    if "measured_fuel_lph" in values:
        return values["measured_fuel_lph"], "measured_fuel_sensor"
    if "observed_fuel_lph" in values:
        return values["observed_fuel_lph"], "supplied_observation"
    if all(k in values for k in ("maf_gps", "stft1_pct", "ltft1_pct")):
        stft = np.mean([values[k] for k in ("stft1_pct", "stft2_pct") if k in values])
        ltft = np.mean([values[k] for k in ("ltft1_pct", "ltft2_pct") if k in values])
        multiplier = 1 + stft / 100 + ltft / 100
        if multiplier >= 0:
            return float(values["maf_gps"] * multiplier / 14.08 * 3600 / 745), "derived_maf_trims"
    return None, "unavailable"


def inspection_evidence(values, fuel, nox, fault):
    evidence, checks = [], []
    if fuel.get("alert"):
        evidence.append({"kind": "fuel_drift", "detail": fuel["alert"]["explanation"]})
        checks.append({"system": "Operating conditions and fuel measurement", "support": "sustained_fuel_residual",
                       "next_check": "Compare route, payload, cold operation, sensor validity and maintenance history."})
    if "stft1_pct" in values and "ltft1_pct" in values:
        trim = values["stft1_pct"] + values["ltft1_pct"]
        if abs(trim) >= 15:
            direction = "positive" if trim > 0 else "negative"
            evidence.append({"kind": "fuel_correction", "bank1_combined_pct": trim,
                             "detail": f"Large {direction} fuel correction in this reading; persistence and closed-loop conditions are unverified."})
            checks.append({"system": "Air measurement, intake and fuel delivery",
                           "support": "fuel_trim_screen_15pct_engineering_heuristic",
                           "next_check": "Check fuel trims across operating conditions, inspect intake/MAF signals and compare fuel pressure with manufacturer specifications.",
                           "independent_of_fuel_estimate": False})
    if nox.get("alert"):
        evidence.append({"kind": "nox_change", "detail": nox["alert"]["explanation"]})
        checks.append({"system": "NOx sensor and exhaust treatment",
                       "support": "same_sensor_concentration_change",
                       "next_check": "Verify warm-up, calibration and sensor position; collect exhaust temperature and relevant diagnostic codes before attributing a catalyst or control-system fault."})
    if fault.get("top_label") is not None:
        evidence.append({"kind": "laboratory_classifier", "detail": fault["top_label_name"],
                         "status": fault["status"], "numeric_label_mapping_verified": False})
    return {"status": "inspection_suggestions" if checks else "insufficient_evidence_for_cause",
            "evidence": evidence, "systems_to_check": checks, "confirmed_component": None,
            "causal_diagnosis": False,
            "limitations": "Suggestions are heuristic inspection directions. MAF and fuel trims generate the VED fuel target, so they are not independent corroboration. EngineFaultDB classes are experimental conditions, not component repair labels."}


class FusionEngine:
    def __init__(self, fuel_model, fault_model=None, freshness_s=3., nox_config=None):
        self.fuel_predictor = Predictor(fuel_model)
        self.detector_config, self.detector_bundle = load_detector(Path(fuel_model))
        self.fault_predictor = FaultPredictor(fault_model) if fault_model else None
        if not math.isfinite(freshness_s) or freshness_s <= 0:
            raise ValueError("Positive finite freshness_s required")
        self.freshness_s = freshness_s
        self.nox_config = nox_config or NoxConfig()
        self.states = {}

    def reset(self):
        self.states.clear()

    def step(self, payload):
        packet = Packet.from_dict(payload)
        values, quality = packet.available(self.freshness_s)
        nox_signal = packet.signals.get("nox_ppm") if "nox_ppm" in values else None
        key = (packet.vehicle_id, packet.trip_id, packet.mode)
        state = self.states.get(key)
        if state is None:
            state = {"last_time": None, "fuel": DriftDetector(self.detector_config),
                     "nox": NoxMonitor(self.nox_config), "fuel_source": None, "fuel_provenance": None}
            self.states[key] = state
        if state["last_time"] is not None and packet.source_time_s <= state["last_time"]:
            raise ValueError("Each stream requires strictly increasing source_time_s")
        monitor = state["nox"]
        if (nox_signal is not None and monitor.last is not None and
                (nox_signal.sensor_id, nox_signal.position, nox_signal.provenance) == monitor.identity and
                nox_signal.measured_at_s < monitor.last[0]):
            raise ValueError("NOx acquisition times cannot move backwards")
        frame = pd.DataFrame([{k: values.get(k, np.nan) for k in FEATURES}])
        expected = float(self.fuel_predictor.predict_frame(frame)[0])
        expected = expected if math.isfinite(expected) else None
        observed, fuel_source = fuel_observation(values)
        if "observed_fuel_lph" in packet.signals and "observed_fuel_lph" not in values and "measured_fuel_lph" not in values:
            observed, fuel_source = None, "unavailable"
        names = (["measured_fuel_lph"] if fuel_source == "measured_fuel_sensor" else
                 ["observed_fuel_lph"] if fuel_source == "supplied_observation" else
                 [k for k in ("maf_gps", "stft1_pct", "stft2_pct", "ltft1_pct", "ltft2_pct") if k in values])
        provenance = sorted({packet.signals[k].provenance for k in names})
        # Changing observation method/provenance must not masquerade as drift.
        if fuel_source != "unavailable" and (fuel_source != state["fuel_source"] or provenance != state["fuel_provenance"]):
            state["fuel"].reset()
        if fuel_source != "unavailable":
            state["fuel_source"], state["fuel_provenance"] = fuel_source, provenance
        fuel = state["fuel"].step(packet.vehicle_id, packet.trip_id, packet.source_time_s,
                                  expected, observed, expected is not None and observed is not None)
        fuel.update(expected_fuel_lph=expected, observed_fuel_lph=observed,
                    observation_method=fuel_source, observation_provenance=provenance,
                    predictor="xgboost" if self.fuel_predictor.kind == "xgboost" else self.fuel_predictor.kind,
                    detector_version="original_threshold_detector", detector_config=asdict(self.detector_config))
        nox = state["nox"].step(nox_signal, values)
        domain = "enginefaultdb_lab" if packet.mode == "enginefaultdb_replay" else packet.mode
        if domain == "enginefaultdb_lab" and any(s.provenance not in {"measured", "laboratory_recording"} for s in packet.signals.values()):
            domain = "unverified_lab_provenance"
        # Derived/synthetic fuel cannot be relabelled as a measured lab input.
        fault_values = dict(values)
        if "measured_fuel_lph" in fault_values and packet.signals["measured_fuel_lph"].provenance not in {"measured", "laboratory_recording"}:
            fault_values.pop("measured_fuel_lph")
        fault = (self.fault_predictor.predict(fault_values, domain) if self.fault_predictor else
                 {"status": "model_not_loaded", "confirmed_component": None})
        emissions = {name: {"value": values.get(name), "unit": UNITS[name],
                            "provenance": packet.signals[name].provenance if name in packet.signals else "unavailable"}
                     for name in ("nox_ppm", "co_pct", "hc_ppm", "co2_pct", "o2_pct")}
        state["last_time"] = packet.source_time_s
        return {"schema_version": 1, "vehicle_id": packet.vehicle_id, "trip_id": packet.trip_id,
                "source_time_s": packet.source_time_s, "mode": packet.mode,
                "data_quality": quality, "fuel": fuel, "emissions": emissions, "nox": nox,
                "fault_classifier": fault, "diagnosis": inspection_evidence(values, fuel, nox, fault),
                "signal_values": values, "compliance": "not_assessed"}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--input", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--fuel-model", type=Path, default=ROOT / "artifacts/models/gpu_candidate")
    p.add_argument("--fault-model", type=Path, default=DEFAULT_MODEL)
    args = p.parse_args()
    if args.input.resolve() == args.output.resolve() or args.output.exists():
        p.error("Choose a new output path to preserve input and previous results")
    engine = FusionEngine(args.fuel_model, args.fault_model)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.input.open(encoding="utf-8") as source, args.output.open("x", encoding="utf-8") as output:
        for line_no, line in enumerate(source, 1):
            if not line.strip():
                continue
            try:
                result = engine.step(json.loads(line))
            except (ValueError, TypeError, KeyError) as exc:
                raise ValueError(f"Input line {line_no}: {exc}") from exc
            output.write(json.dumps(result, allow_nan=False) + "\n")
    print(f"Saved combined monitoring output to {args.output}")


if __name__ == "__main__":
    main()
