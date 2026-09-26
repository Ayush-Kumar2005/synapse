import copy
import json
import math
import unittest

import numpy as np
import pandas as pd

from model.common import ROOT
from model.emissions import NoxConfig, NoxMonitor
from model.enginefault import blocked_split, DEFAULT_MODEL, FaultPredictor, FEATURES as FAULT_FEATURES
from model.fusion import FusionEngine, fuel_observation, inspection_evidence
from model.telemetry import NoxFeed, Packet, Signal, signal_dict


def packet(t=0, vehicle=1, trip=1):
    values = {"speed_kph": 50, "rpm": 2000, "load_pct": 40, "acceleration_mps2": 0,
              "displacement_l": 2, "weight_kg": 1500, "observed_fuel_lph": 20}
    return {"vehicle_id": vehicle, "trip_id": trip, "source_time_s": t, "mode": "synthetic_demo",
            "signals": {k: signal_dict(k, v, t, "synthetic") for k, v in values.items()}}


class TelemetryTests(unittest.TestCase):
    def test_wrong_unit_and_future_sample_rejected(self):
        p = packet()
        p["signals"]["rpm"]["unit"] = "Hz"
        with self.assertRaises(ValueError):
            Packet.from_dict(p).available()
        p = packet()
        p["signals"]["rpm"]["measured_at_s"] = 1
        with self.assertRaises(ValueError):
            Packet.from_dict(p).available()

    def test_stale_invalid_and_unavailable_are_not_zero(self):
        p = packet(10)
        p["signals"]["rpm"]["measured_at_s"] = 0
        p["signals"]["speed_kph"]["value"] = -1
        p["signals"]["load_pct"]["value"] = None
        values, quality = Packet.from_dict(p).available()
        self.assertNotIn("rpm", values)
        self.assertNotIn("speed_kph", values)
        self.assertNotIn("load_pct", values)
        self.assertEqual(quality["rpm"]["status"], "stale")

    def test_synthetic_packet_cannot_claim_live_mode(self):
        p = packet()
        p["mode"] = "live"
        with self.assertRaises(ValueError):
            Packet.from_dict(p)

    def test_recorded_lab_playback_time_is_not_a_verified_sensor_age(self):
        p = packet()
        p["mode"] = "enginefaultdb_replay"
        for s in p["signals"].values():
            s["provenance"] = "laboratory_recording"
        _, quality = Packet.from_dict(p).available()
        self.assertFalse(quality["rpm"]["acquisition_age_verified"])

    def test_nox_backward_lookup_no_cross_vehicle_or_future(self):
        rows = [{"vehicle_id": v, "trip_id": 1, "source_time_s": t, "nox_ppm": ppm,
                 "sensor_id": "s", "position": "downstream", "quality": "valid", "provenance": "measured"}
                for v, t, ppm in [(1, 5, 100), (1, 8, 200), (2, 5, 900)]]
        feed = NoxFeed(pd.DataFrame(rows), "s")
        self.assertIsNone(feed.at(1, 1, 4))
        self.assertEqual(feed.at(1, 1, 7)["value"], 100)
        self.assertEqual(feed.at(2, 1, 7)["value"], 900)
        self.assertIsNone(feed.at(1, 2, 7))
        self.assertIsNone(feed.at(1, 1, 12))
        numeric_strings = pd.DataFrame(rows).astype({"vehicle_id": str, "trip_id": str, "source_time_s": str, "nox_ppm": str})
        self.assertEqual(NoxFeed(numeric_strings, "s").at(1, 1, 7)["value"], 100)
        with self.assertRaises(ValueError):
            NoxFeed(pd.DataFrame(rows + rows[:1]), "s")

    def test_target_derivation_and_missing_trims(self):
        value, origin = fuel_observation({"maf_gps": 14.08, "stft1_pct": 0, "ltft1_pct": 0})
        self.assertAlmostEqual(value, 3600 / 745)
        self.assertEqual(origin, "derived_maf_trims")
        self.assertIsNone(fuel_observation({"maf_gps": 14.08})[0])


class NoxTests(unittest.TestCase):
    def setUp(self):
        self.monitor = NoxMonitor(NoxConfig(baseline_s=3, persistence_s=2))
        self.values = {"rpm": 2000, "load_pct": 40}

    def signal(self, t, ppm=100, sensor="s"):
        return Signal(ppm, "ppm", t, "synthetic", sensor_id=sensor, position="downstream")

    def reference(self):
        for t in range(4):
            self.monitor.step(self.signal(t), self.values)

    def test_reference_freezes_then_sustained_change_alerts(self):
        self.reference()
        for t in range(4, 7):
            result = self.monitor.step(self.signal(t, 250), self.values)
        self.assertEqual(result["reference_ppm"], 100)
        self.assertIsNotNone(result["alert"])
        self.assertEqual(result["compliance"], "not_assessed")
        self.assertIsNone(result["mass_rate_g_s"])

    def test_duplicate_sample_does_not_accumulate_evidence(self):
        self.reference()
        for _ in range(10):
            result = self.monitor.step(self.signal(4, 250), self.values)
        self.assertIsNone(result["alert"])

    def test_missing_gap_and_different_conditions_break_persistence(self):
        self.reference()
        self.monitor.step(self.signal(4, 250), self.values)
        self.monitor.step(None, self.values)
        result = self.monitor.step(self.signal(7, 250), self.values)
        self.assertIsNone(result["alert"])
        result = self.monitor.step(self.signal(8, 250), {"rpm": 4000, "load_pct": 80})
        self.assertEqual(result["status"], "different_operating_conditions")
        self.assertIsNone(result["alert"])

    def test_sensor_change_resets_reference(self):
        self.reference()
        result = self.monitor.step(self.signal(5, 250, "other"), self.values)
        self.assertIsNone(result["reference_ppm"])
        self.assertIsNone(result["alert"])


class FaultTests(unittest.TestCase):
    def test_purged_blocks_have_separation_for_each_label(self):
        df = pd.DataFrame({"Fault": np.repeat([0, 1, 2, 3], 1000)})
        split = blocked_split(df, purge=10)
        for label in range(4):
            indexes = df.index[df.Fault.eq(label)]
            parts = [indexes[split.loc[indexes].eq(k)] for k in ("train", "validation", "test")]
            self.assertGreater(parts[1].min() - parts[0].max(), 20)
            self.assertGreater(parts[2].min() - parts[1].max(), 20)

    @unittest.skipUnless((DEFAULT_MODEL / "metadata.json").exists(), "Trained artifact not available")
    def test_classifier_gates_missing_outside_and_external_domain(self):
        predictor = FaultPredictor()
        self.assertEqual(predictor.predict({}, "ved_replay")["status"], "unavailable")
        values = {k: sum(predictor.metadata["bounds"][k]) / 2 for k in FAULT_FEATURES}
        self.assertEqual(predictor.predict(values, "live")["status"], "unsupported_sensor_domain")
        values["rpm"] = 100000
        self.assertEqual(predictor.predict(values, "enginefaultdb_lab")["status"], "outside_training_ranges")

    def test_no_evidence_does_not_invent_components(self):
        result = inspection_evidence({}, {}, {}, {})
        self.assertEqual(result["systems_to_check"], [])
        self.assertIsNone(result["drift_explanation"])
        self.assertIsNone(result["confirmed_component"])

    def test_drift_reason_is_quantitative_without_claiming_component(self):
        alert = {"duration_s": 60, "evidence_start_s": 10, "detected_at_s": 70,
                 "window_expected_l": 0.1, "window_observed_l": 0.15,
                 "excess_l": 0.05, "excess_pct": 50,
                 "explanation": "Sustained excess fuel estimate."}
        fuel = {"alert": alert, "observation_method": "derived_maf_trims",
                "observation_provenance": ["recorded_age_unverified"]}
        result = inspection_evidence({"stft1_pct": 20, "ltft1_pct": 0}, fuel,
                                     {"alert": None}, {"top_label": "1", "top_label_name": "Fault type 1",
                                                       "status": "experimental_classification"})
        reason = result["drift_explanation"]
        self.assertAlmostEqual(reason["expected_mean_lph"], 6)
        self.assertAlmostEqual(reason["observed_mean_lph"], 9)
        self.assertEqual(reason["excess_l"], 0.05)
        self.assertFalse(reason["fuel_measurement_independent_of_maf_trims"])
        self.assertIsNone(reason["root_cause"])
        self.assertFalse(result["causal_diagnosis"])
        self.assertFalse(result["systems_to_check"][1]["independent_of_fuel_estimate"])


@unittest.skipUnless((ROOT / "artifacts/models/gpu_candidate/metadata.json").exists(), "Fuel model not available")
class FusionIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = FusionEngine(ROOT / "artifacts/models/gpu_candidate", DEFAULT_MODEL)

    def setUp(self):
        self.engine.reset()

    def test_combined_fixture_triggers_both_channels_and_serializes(self):
        from model.verify_fusion import synthetic_packets
        for p in synthetic_packets(self.engine):
            result = self.engine.step(p)
        self.assertIsNotNone(result["fuel"]["alert"])
        self.assertIsNotNone(result["nox"]["alert"])
        self.assertIsNone(result["diagnosis"]["confirmed_component"])
        self.assertEqual(result["fault_classifier"]["status"], "unavailable")
        json.dumps(result, allow_nan=False)

    def test_missing_nox_and_field_classifier_remain_unavailable(self):
        result = self.engine.step(packet())
        self.assertIsNone(result["emissions"]["nox_ppm"]["value"])
        self.assertEqual(result["nox"]["status"], "unavailable")
        self.assertEqual(result["fault_classifier"]["status"], "unavailable")

    def test_out_of_order_rejected_and_vehicles_are_isolated(self):
        self.engine.step(packet(10))
        with self.assertRaises(ValueError):
            self.engine.step(packet(10))
        for t in range(11, 140):
            self.engine.step(packet(t))
        result = self.engine.step(packet(0, vehicle=2))
        self.assertIsNone(result["fuel"]["alert"])
        self.assertEqual(result["fuel"]["valid_seconds"], 0)

    def test_invalid_packet_does_not_advance_clock(self):
        bad = packet()
        bad["signals"]["rpm"]["unit"] = "Hz"
        with self.assertRaises(ValueError):
            self.engine.step(bad)
        self.engine.step(packet())

    def test_stale_required_signal_breaks_fuel_evidence(self):
        for t in range(100):
            self.engine.step(packet(t))
        p = packet(100)
        p["signals"]["rpm"]["measured_at_s"] = 0
        result = self.engine.step(p)
        self.assertIsNone(result["fuel"]["expected_fuel_lph"])
        self.assertEqual(result["fuel"]["window_coverage"], 0)

if __name__ == "__main__":
    unittest.main()
