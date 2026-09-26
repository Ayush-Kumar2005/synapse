"""Time-based residual detector. Inputs contain no simulation settings or labels."""
from __future__ import annotations

from collections import deque
from copy import deepcopy
from dataclasses import asdict, dataclass
import math


@dataclass(frozen=True)
class DetectorConfig:
    window_s: float = 60.0
    persistence_s: float = 30.0
    relative_threshold: float = 0.25
    absolute_threshold_lph: float = 1.0
    min_expected_lph: float = 0.5
    max_gap_s: float = 5.0

    def __post_init__(self):
        if any(not math.isfinite(v) or v <= 0 for v in asdict(self).values()):
            raise ValueError("Detector settings must be finite and positive")


class DriftDetector:
    def __init__(self, config=DetectorConfig()):
        self.config = config
        self.reset()

    def reset(self):
        self.key = None
        self.last_time = None
        self.previous = None
        self.intervals = deque()
        self.window_expected = self.window_observed = 0.0
        self.total_expected = self.total_observed = self.valid_seconds = 0.0
        self.above_since = None
        self.alert = None

    def _break_evidence(self):
        self.previous = None
        self.intervals.clear()
        self.window_expected = self.window_observed = 0.0
        self.above_since = None

    @staticmethod
    def _litres(interval):
        start, end, e0, e1, o0, o1 = interval
        hours = (end - start) / 3600
        return hours * (e0 + e1) / 2, hours * (o0 + o1) / 2

    def step(self, vehicle_id, trip_id, source_time_s, expected_lph, observed_lph, valid=True):
        key = (vehicle_id, trip_id)
        if key != self.key:
            self.reset()
            self.key = key
        t = float(source_time_s)
        if not math.isfinite(t) or (self.last_time is not None and t <= self.last_time):
            raise ValueError("Each trip must arrive once in strictly increasing source time")
        self.last_time = t
        good = valid and all(v is not None and math.isfinite(v) and v >= 0 for v in (expected_lph, observed_lph))
        if not good:
            self._break_evidence()
            return self._result("Insufficient data", t)
        current = (t, float(expected_lph), float(observed_lph))
        if self.previous is not None and t - self.previous[0] > self.config.max_gap_s:
            self._break_evidence()
        if self.previous is not None:
            pt, pe, po = self.previous
            interval = (pt, t, pe, current[1], po, current[2])
            e, o = self._litres(interval)
            self.intervals.append(interval)
            self.window_expected += e
            self.window_observed += o
            self.total_expected += e
            self.total_observed += o
            self.valid_seconds += t - pt
        self.previous = current
        cutoff = t - self.config.window_s
        while self.intervals and self.intervals[0][1] <= cutoff:
            e, o = self._litres(self.intervals.popleft())
            self.window_expected -= e
            self.window_observed -= o
        if self.intervals and self.intervals[0][0] < cutoff:
            old = self.intervals.popleft()
            start, end, e0, e1, o0, o1 = old
            fraction = (cutoff - start) / (end - start)
            trimmed = (cutoff, end, e0 + fraction * (e1 - e0), e1, o0 + fraction * (o1 - o0), o1)
            oe, oo = self._litres(old)
            ne, no = self._litres(trimmed)
            self.window_expected += ne - oe
            self.window_observed += no - oo
            self.intervals.appendleft(trimmed)
        duration = t - self.intervals[0][0] if self.intervals else 0.0
        if duration < self.config.window_s - 1e-6:
            self.above_since = None
            return self._result("Collecting baseline", t)
        expected_rate = self.window_expected / duration * 3600
        if expected_rate < self.config.min_expected_lph:
            self.above_since = None
            return self._result("Insufficient data", t)
        residual_rate = (self.window_observed - self.window_expected) / duration * 3600
        relative = residual_rate / expected_rate
        above = relative >= self.config.relative_threshold and residual_rate >= self.config.absolute_threshold_lph
        if above:
            if self.above_since is None:
                self.above_since = t
            if t - self.above_since >= self.config.persistence_s and self.alert is None:
                self.alert = {
                    "vehicle_id": int(vehicle_id), "trip_id": int(trip_id), "detected_at_s": t,
                    "evidence_start_s": self.intervals[0][0], "duration_s": duration,
                    "threshold_exceeded_for_s": t - self.above_since,
                    "window_expected_l": self.window_expected, "window_observed_l": self.window_observed,
                    "excess_l": max(0.0, self.window_observed - self.window_expected),
                    "excess_pct": relative * 100, "valid_coverage": 1.0,
                    "explanation": "Estimated fuel use remained above the model's expected range. Inspection may be warranted; this is not a component diagnosis.",
                }
        else:
            self.above_since = None
        status = "Inspection recommended" if self.alert is not None else "Watch" if above else "Within expected range"
        return self._result(status, t)

    def _result(self, status, t):
        seconds = t - self.intervals[0][0] if self.intervals else 0.0
        return {
            "status": status,
            "smoothed_excess_lph": (self.window_observed - self.window_expected) / seconds * 3600 if seconds else None,
            "window_coverage": min(1.0, seconds / self.config.window_s),
            "window_expected_l": self.window_expected, "window_observed_l": self.window_observed,
            "valid_seconds": self.valid_seconds,
            "integrated_expected_l": self.total_expected, "integrated_observed_l": self.total_observed,
            "excess_l": max(0.0, self.total_observed - self.total_expected),
            "alert": deepcopy(self.alert),
        }
