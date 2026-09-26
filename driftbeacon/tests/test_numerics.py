import unittest
import json

import numpy as np
import pandas as pd

from model.common import FEATURES, ROOT, TARGET, read_json
from model.detector import DetectorConfig, DriftDetector
from model.prepare import fuel_target, prepare_trip, metadata
from model.replay import simulate_observations, run_channel
from model.evaluate import score_detector


class PreparationChecks(unittest.TestCase):
    def setUp(self):
        self.config = read_json(ROOT / "config/preparation.json")

    def test_fuel_mass_to_volume_and_bank_average(self):
        frame = pd.DataFrame({"maf_gps": [14.08, 14.08, 14.08, 14.08], "stft1_pct": [0, 10, np.nan, 0], "stft2_pct": [np.nan, 30, 0, 999], "ltft1_pct": [0, 0, 0, 0], "ltft2_pct": [np.nan, 0, 0, np.nan]})
        y = fuel_target(frame, self.config)
        self.assertAlmostEqual(y.iloc[0], 3600 / 745)
        self.assertAlmostEqual(y.iloc[1], 1.2 * 3600 / 745)
        self.assertTrue(np.isnan(y.iloc[2]))
        self.assertTrue(np.isnan(y.iloc[3]))

    def test_acceleration_is_causal_and_resets_at_gap(self):
        frame = pd.DataFrame({"timestamp_ms": [0, 100, 1000, 1100, 20000, 21000], "speed_kph": [0, 0, 3.6, 3.6, 72, 75.6], "rpm": 1500, "load_pct": 30, "maf_gps": 14.08, "stft1_pct": 0, "stft2_pct": np.nan, "ltft1_pct": 0, "ltft2_pct": np.nan, "displacement_l": 2})
        result = prepare_trip(frame, self.config)
        self.assertTrue(np.isnan(result.acceleration_mps2.iloc[0]))
        self.assertAlmostEqual(result.acceleration_mps2.iloc[2], 1 / 0.9)
        self.assertAlmostEqual(result.acceleration_mps2.iloc[3], 1)
        self.assertTrue(np.isnan(result.acceleration_mps2.iloc[4]))
        prefix = prepare_trip(frame.iloc[:4], self.config)
        np.testing.assert_allclose(prefix.acceleration_mps2, result.acceleration_mps2.iloc[:4], equal_nan=True)

    def test_target_generators_are_not_features(self):
        self.assertFalse(set(FEATURES) & {TARGET, "maf_gps", "stft1_pct", "stft2_pct", "ltft1_pct", "ltft2_pct", "reported_fuel_lph", "vehicle_id", "trip_id"})

    def test_metadata_excludes_known_other_fuels(self):
        path = ROOT / "data/raw/ved-source/Data/VED_Static_Data_ICE&HEV.xlsx"
        if not path.exists():
            self.skipTest("Source metadata not included in training-only handoff")
        meta = metadata(path).set_index("VehId")
        self.assertEqual(meta.loc[119, "exclusion"], "diesel")
        self.assertEqual(meta.loc[588, "exclusion"], "flex_fuel_unknown_blend")


class DetectorChecks(unittest.TestCase):
    def test_constant_rate_integrates_litres(self):
        d = DriftDetector()
        for t in range(3601):
            out = d.step(1, 1, t, 3.6, 7.2)
        self.assertAlmostEqual(out["integrated_expected_l"], 3.6)
        self.assertAlmostEqual(out["integrated_observed_l"], 7.2)
        self.assertAlmostEqual(out["excess_l"], 3.6)
        self.assertEqual(out["status"], "Inspection recommended")

    def test_irregular_window_uses_trapezoids_and_exact_cutoff(self):
        d = DriftDetector(DetectorConfig(window_s=10))
        for t in (0, 3, 7, 11, 14):
            out = d.step(1, 1, t, 2, 2 + t)
        # Last 10s [4,14], integral(2+t) = 110 L/h*s.
        self.assertAlmostEqual(out["window_observed_l"], 110 / 3600)
        self.assertAlmostEqual(out["window_expected_l"], 20 / 3600)

    def test_gap_and_missing_data_break_persistence(self):
        d = DriftDetector(DetectorConfig(window_s=10, persistence_s=10))
        for t in range(16):
            d.step(1, 1, t, 3, 9)
        out = d.step(1, 1, 100, 3, 9)
        self.assertIsNone(out["alert"])
        self.assertAlmostEqual(out["valid_seconds"], 15)
        out = d.step(1, 1, 101, np.nan, 9)
        self.assertEqual(out["status"], "Insufficient data")
        self.assertEqual(out["window_coverage"], 0)

    def test_alert_evidence_is_frozen_and_reset_clears_it(self):
        d = DriftDetector(DetectorConfig(window_s=5, persistence_s=2))
        for t in range(10):
            out = d.step(1, 1, t, 3, 9)
        saved = dict(out["alert"])
        out["alert"]["excess_l"] = 999
        later = d.step(1, 1, 10, 3, 30)
        self.assertEqual(later["alert"], saved)
        d.reset()
        self.assertIsNone(d.alert)
        d.step(1, 2, 0, 3, 3)
        self.assertEqual(d.valid_seconds, 0)

    def test_net_excess_does_not_sum_positive_noise(self):
        d = DriftDetector()
        for t, o in enumerate([2, 4, 2, 4, 2]):
            out = d.step(1, 1, t, 3, o)
        self.assertAlmostEqual(out["excess_l"], 0)

    def test_simulation_and_replay_are_independent_and_deterministic(self):
        times = np.arange(100.0)
        observed = np.full(100, 3.0)
        changed = simulate_observations(times, observed, 20, 20, 0.6)
        np.testing.assert_array_equal(changed[:21], observed[:21])
        np.testing.assert_array_equal(observed, np.full(100, 3.0))
        trip = pd.DataFrame({"vehicle_id": 1, "trip_id": 1, "source_time_s": times, "model_valid": True})
        config = DetectorConfig(window_s=10, persistence_s=5, absolute_threshold_lph=0.5)
        original = run_channel(trip, observed, observed, config)
        simulated = run_channel(trip, observed, changed, config)
        self.assertIsNone(original["alert"])
        self.assertIsNotNone(simulated["alert"])
        self.assertEqual(original, run_channel(trip, observed, observed, config))
        # Playback pacing is external; source samples wholly determine the results.
        self.assertEqual(simulated, run_channel(trip, observed, changed, config))

    def test_duplicate_or_out_of_order_samples_are_rejected(self):
        d = DriftDetector()
        d.step(1, 1, 10, 3, 3)
        with self.assertRaises(ValueError):
            d.step(1, 1, 10, 3, 3)

    def test_evaluation_outputs_strict_json_including_early_alerts(self):
        times = np.arange(100.0)
        trip = pd.DataFrame({"vehicle_id": 1, "trip_id": 1, "source_time_s": times, "model_valid": True, TARGET: 9.0})
        summary, ids, scenarios = score_detector([trip], [np.full(100, 3.0)], DetectorConfig(window_s=5, persistence_s=2))
        self.assertEqual(summary["early_alert_scenarios"], 2)
        json.dumps({"summary": summary, "ids": ids, "scenarios": scenarios}, allow_nan=False)


if __name__ == "__main__":
    unittest.main()
