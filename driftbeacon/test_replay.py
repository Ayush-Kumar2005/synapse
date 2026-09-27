import unittest

from server import DURATION_SECONDS, Detector, PREPARED, Replay, TRIP, build_sections, trip_efficiency


class ReplayTests(unittest.TestCase):
    def finished(self, severity):
        replay = Replay(severity, 18 * 60, 6 * 60)
        replay.source_cursor = DURATION_SECONDS
        replay.response()
        return replay

    def test_clean_trip_has_no_alert(self):
        replay = self.finished(0)
        self.assertEqual(replay.original.summary()["status"], "within_range")
        self.assertEqual(replay.simulated.summary()["status"], "within_range")
        self.assertFalse(replay.simulated.alerts)

    def test_ramp_alert_does_not_change_original(self):
        clean = self.finished(0)
        drift = self.finished(.15)
        self.assertEqual(clean.original.summary(), drift.original.summary())
        self.assertEqual(len(drift.simulated.alerts), 1)
        alert = drift.simulated.alerts[0]
        self.assertGreater(alert["detected_at_s"], 18 * 60)
        self.assertGreater(alert["window_observed_l"], alert["window_expected_l"])

    def test_gap_breaks_integration_and_coverage(self):
        detector = Detector()
        before = dict(TRIP[0], source_time_s=0)
        after = dict(TRIP[1], source_time_s=120)
        detector.feed(before)
        detector.feed(after)
        self.assertEqual(detector.summary()["valid_seconds"], 0)
        self.assertEqual(detector.summary()["status"], "collecting")

    def test_missing_reading_is_not_healthy_assessment(self):
        detector = Detector()
        detector.feed(dict(TRIP[0], expected_fuel_lph=None, observed_fuel_lph=None))
        self.assertEqual(detector.summary()["status"], "insufficient")

    def test_sections_reconcile_with_detector_integrals(self):
        replay = self.finished(.15)
        sections = build_sections(replay.points, "simulated", DURATION_SECONDS)
        self.assertAlmostEqual(sum(s["expected_l"] or 0 for s in sections),
                               replay.simulated.expected_l, places=3)
        self.assertAlmostEqual(sum(s["observed_l"] or 0 for s in sections),
                               replay.simulated.observed_l, places=3)
        self.assertTrue(all(s["coordinates"] is None for s in sections))
        self.assertTrue(any(s["coverage"] < 1 for s in sections))

    def test_partial_section_coverage_uses_elapsed_time(self):
        replay = Replay(.15, 18 * 60, 6 * 60)
        replay.source_cursor = 25 * 60
        replay.response()
        sections = build_sections(replay.points, "simulated", 25 * 60)
        self.assertEqual(sections[-1]["end_s"], 25 * 60)
        self.assertEqual(sections[-1]["coverage"], 1)
        self.assertGreater(trip_efficiency(replay.points, "simulated")["distance_km"], 0)

    @unittest.skipUnless(PREPARED, "Prepared sample route unavailable")
    def test_expert_scenarios_and_puc_advice(self):
        trip_id = PREPARED["metadata"]["id"]
        clean = Replay(0, 240, 120, trip_id)
        clean.source_cursor = 800
        self.assertEqual(clean.response()["puc_advisory"]["status"], "not_assessed")
        drift = Replay(.15, 240, 120, trip_id)
        drift.source_cursor = 600
        self.assertEqual(drift.response()["puc_advisory"]["status"], "not_assessed")
        drift.source_cursor = 800
        first = drift.response()
        self.assertEqual(first["simulated"]["status"], "inspection")
        self.assertEqual(first["puc_advisory"]["status"], "not_assessed")
        self.assertEqual(first["emissions"]["co_provenance"], "synthetic demo")
        self.assertEqual(first["fault_analysis"]["expert_system"]["likely_reasons"][0]["reason_code"], "fuel_delivery_pressure")
        high = Replay(.28, 240, 120, trip_id)
        high.source_cursor = 600
        self.assertEqual(high.response()["puc_advisory"]["status"], "not_assessed")
        high.source_cursor = 800
        later = high.response()
        self.assertEqual(later["fault_analysis"]["expert_system"]["likely_reasons"][0]["reason_code"], "rich_mixture_pattern")
        self.assertEqual(later["puc_advisory"]["status"], "consider_test")
        self.assertGreaterEqual(later["emissions"]["co_pct"], later["emissions"]["demo_markers"]["co_pct"])


if __name__ == "__main__":
    unittest.main()
