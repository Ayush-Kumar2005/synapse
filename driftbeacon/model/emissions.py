"""Experimental NOx concentration-change screen; no compliance inference."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import math


@dataclass(frozen=True)
class NoxConfig:
    baseline_s: float = 60
    persistence_s: float = 30
    relative_increase: float = .5
    absolute_increase_ppm: float = 50
    max_gap_s: float = 5
    rpm_relative_tolerance: float = .15
    load_tolerance_pct: float = 10

    def __post_init__(self):
        if any(not math.isfinite(x) or x <= 0 for x in asdict(self).values()):
            raise ValueError("NOx settings must be finite and positive")


class NoxMonitor:
    def __init__(self, config=None):
        self.config = config or NoxConfig()
        self.identity = None
        self.last = None
        self.reference = None
        self.anchor = None
        self.baseline_seconds = self.baseline_area = 0.
        self.above_since = None
        self.alert = None

    def step(self, signal, values):
        output = {"status": "unavailable", "current_ppm": None, "reference_ppm": self.reference,
                  "alert": self.alert, "validation": "experimental_uncalibrated",
                  "compliance": "not_assessed", "mass_rate_g_s": None,
                  "limitation": "Concentration change at comparable RPM/load only. No exhaust mass flow, legal limit or confirmed fault."}
        if signal is None or "rpm" not in values or "load_pct" not in values:
            self.last = None
            self.above_since = None
            if self.reference is None:
                self.anchor = None
                self.baseline_seconds = self.baseline_area = 0.
            return output
        identity = (signal.sensor_id, signal.position, signal.provenance)
        if identity != self.identity:
            self.__init__(self.config)
            self.identity = identity
            output.update(reference_ppm=None, alert=None)
        t, ppm = float(signal.measured_at_s), float(signal.value)
        rpm, load = values["rpm"], values["load_pct"]
        output.update(current_ppm=ppm, sensor_id=signal.sensor_id, position=signal.position,
                      provenance=signal.provenance)
        if self.last and t < self.last[0]:
            raise ValueError("NOx acquisition times cannot move backwards")
        if self.last and t == self.last[0]:
            # Reusing a fresh reading is allowed for display, never new evidence.
            return {**output, "status": "awaiting_new_sensor_sample"}
        if self.anchor is None:
            self.anchor = (rpm, load)
        comparable = (abs(rpm - self.anchor[0]) <= max(100, self.anchor[0] * self.config.rpm_relative_tolerance)
                      and abs(load - self.anchor[1]) <= self.config.load_tolerance_pct)
        gap = self.last is not None and t - self.last[0] > self.config.max_gap_s
        if gap or not comparable:
            self.last = None
            self.above_since = None
            if self.reference is None:
                self.baseline_seconds = self.baseline_area = 0.
                self.anchor = (rpm, load)
            elif not comparable:
                return {**output, "status": "different_operating_conditions"}
        if self.reference is None:
            if self.last:
                dt = t - self.last[0]
                self.baseline_seconds += dt
                self.baseline_area += dt * (ppm + self.last[1]) / 2
            self.last = (t, ppm)
            if self.baseline_seconds >= self.config.baseline_s:
                self.reference = self.baseline_area / self.baseline_seconds
            return {**output, "status": "collecting_assumed_reference", "reference_ppm": self.reference,
                    "reference_seconds": self.baseline_seconds}
        self.last = (t, ppm)
        change = ppm - self.reference
        above = change >= max(self.config.absolute_increase_ppm, self.reference * self.config.relative_increase)
        if above:
            if self.above_since is None:
                self.above_since = t
            if t - self.above_since >= self.config.persistence_s and self.alert is None:
                self.alert = {"detected_at_s": t, "change_ppm": change, "reference_ppm": self.reference,
                              "sensor_id": signal.sensor_id, "position": signal.position,
                              "provenance": signal.provenance,
                              "explanation": "Sustained NOx concentration increase versus an assumed reference at comparable RPM/load. Investigate sensor validity and operating conditions."}
        else:
            self.above_since = None
        return {**output, "status": "change_flagged" if self.alert else "watch" if above else "within_reference",
                "reference_ppm": self.reference, "change_ppm": change, "alert": self.alert}
