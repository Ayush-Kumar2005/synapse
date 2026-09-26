"""Explicit signal units, acquisition times and causal NOx/vehicle alignment."""
from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass
import math

import pandas as pd

UNITS = {
    "speed_kph": "km/h", "rpm": "rpm", "load_pct": "%", "acceleration_mps2": "m/s2",
    "displacement_l": "L", "weight_kg": "kg", "observed_fuel_lph": "L/h", "measured_fuel_lph": "L/h",
    "maf_gps": "g/s", "stft1_pct": "%", "stft2_pct": "%", "ltft1_pct": "%", "ltft2_pct": "%",
    "nox_ppm": "ppm", "co_pct": "%", "hc_ppm": "ppm", "co2_pct": "%", "o2_pct": "%",
    "lambda": "ratio", "map_kpa": "kPa", "throttle_pct": "%", "coolant_c": "degC",
    "battery_v": "V", "afr": "ratio",
}
RANGES = {"speed_kph": (0, 250), "rpm": (0, 8000), "load_pct": (0, 300),
          "acceleration_mps2": (-8, 8), "displacement_l": (.5, 10), "weight_kg": (500, 6000),
          "observed_fuel_lph": (0, 300), "measured_fuel_lph": (0, 300), "maf_gps": (0, 1000),
          "nox_ppm": (0, 1000000), "co_pct": (0, 100), "hc_ppm": (0, 1000000),
          "co2_pct": (0, 100), "o2_pct": (0, 100), "lambda": (.1, 10),
          "stft1_pct": (-99, 99), "stft2_pct": (-99, 99), "ltft1_pct": (-99, 99), "ltft2_pct": (-99, 99),
          "map_kpa": (0, 1000), "throttle_pct": (0, 100), "coolant_c": (-50, 200),
          "battery_v": (0, 60), "afr": (0, 150)}
MODES = {"live", "ved_replay", "enginefaultdb_replay", "synthetic_demo"}
PROVENANCE = {"measured", "derived_maf_trims", "recorded_age_unverified", "laboratory_recording", "synthetic"}
QUALITIES = {"valid", "warming_up", "fault", "unavailable"}
POSITIONS = {"upstream", "downstream", "unknown"}


@dataclass(frozen=True)
class Signal:
    value: float | None
    unit: str
    measured_at_s: float
    provenance: str
    quality: str = "valid"
    sensor_id: str | None = None
    position: str | None = None

    def issue(self, name, now, max_age_s):
        if self.unit != UNITS[name]:
            raise ValueError(f"{name} requires {UNITS[name]}, received {self.unit}")
        if self.provenance not in PROVENANCE or self.quality not in QUALITIES:
            raise ValueError("Unknown signal provenance or quality")
        if not math.isfinite(self.measured_at_s):
            raise ValueError("Signal timestamp must be finite")
        if self.measured_at_s > now:
            raise ValueError("A future sensor reading cannot be used")
        if name == "nox_ppm" and (not self.sensor_id or self.position not in POSITIONS):
            raise ValueError("NOx requires sensor_id and upstream/downstream/unknown position")
        if self.quality != "valid":
            return self.quality
        if self.value is None or isinstance(self.value, bool) or not math.isfinite(self.value):
            return "missing_or_nonfinite"
        if not RANGES[name][0] <= self.value <= RANGES[name][1]:
            return "outside_engineering_range"
        if now - self.measured_at_s > max_age_s:
            return "stale"
        return None


@dataclass(frozen=True)
class Packet:
    vehicle_id: int
    trip_id: int
    source_time_s: float
    mode: str
    signals: dict[str, Signal]

    @classmethod
    def from_dict(cls, payload):
        if payload.get("mode") not in MODES:
            raise ValueError("Explicit live/replay/synthetic mode is required")
        for name in ("vehicle_id", "trip_id"):
            if type(payload.get(name)) is not int or payload[name] < 0:
                raise ValueError(f"{name} must be a nonnegative integer")
        t = float(payload["source_time_s"])
        if not math.isfinite(t) or t < 0:
            raise ValueError("Finite nonnegative source_time_s required")
        signals = {}
        for name, value in payload["signals"].items():
            if name not in UNITS:
                raise ValueError(f"Unknown signal {name}; define its units before importing")
            signals[name] = Signal(**value)
        if payload["mode"] == "live" and any(s.provenance != "measured" for s in signals.values()):
            raise ValueError("Live packets require measured signals; derive fuel within the pipeline")
        return cls(payload["vehicle_id"], payload["trip_id"], t, payload["mode"], signals)

    def available(self, max_age_s=3.0):
        if not math.isfinite(max_age_s) or max_age_s <= 0:
            raise ValueError("Freshness limit must be positive and finite")
        values, quality = {}, {}
        for name, signal in self.signals.items():
            issue = signal.issue(name, self.source_time_s, max_age_s)
            quality[name] = {"status": issue or "available", "provenance": signal.provenance,
                             "age_s": self.source_time_s - signal.measured_at_s,
                             "acquisition_age_verified": signal.provenance == "measured"}
            if issue is None:
                values[name] = float(signal.value)
        return values, quality


def signal_dict(name, value, t, provenance, **extra):
    return {"value": float(value) if value is not None and math.isfinite(value) else None,
            "unit": UNITS[name], "measured_at_s": float(t), "provenance": provenance, **extra}


class NoxFeed:
    """Backward-only lookup keyed by vehicle/trip/sensor, with bounded age.

    CSV timestamps must already share the vehicle stream's clock origin.
    Imported data retains measured/synthetic provenance and sensor quality.
    """
    COLUMNS = {"vehicle_id", "trip_id", "source_time_s", "nox_ppm", "sensor_id", "position", "quality", "provenance"}

    def __init__(self, frame, sensor_id, max_age_s=3.0):
        if not self.COLUMNS <= set(frame):
            raise ValueError(f"NOx CSV missing {sorted(self.COLUMNS - set(frame))}")
        if not sensor_id or not math.isfinite(max_age_s) or max_age_s <= 0:
            raise ValueError("Select a sensor and positive finite maximum age")
        if not np_finite_ids(frame):
            raise ValueError("NOx IDs/timestamps must be finite nonnegative integers/seconds")
        frame = frame.copy()
        for name in ("vehicle_id", "trip_id", "source_time_s", "nox_ppm"):
            frame[name] = pd.to_numeric(frame[name], errors="raise")
        if frame[list(self.COLUMNS - {"nox_ppm"})].isna().any().any():
            raise ValueError("NOx metadata cannot be missing")
        if not set(frame.provenance) <= {"measured", "synthetic"}:
            raise ValueError("NOx readings must be explicitly measured or synthetic")
        if not set(frame.quality) <= QUALITIES or not set(frame.position) <= POSITIONS:
            raise ValueError("Unknown NOx quality or position")
        frame = frame[frame.sensor_id.eq(sensor_id)].copy()
        if frame.empty:
            raise ValueError("Selected sensor has no rows")
        if frame.duplicated(["vehicle_id", "trip_id", "source_time_s"]).any():
            raise ValueError("Duplicate NOx timestamp for selected sensor")
        if frame.position.nunique() != 1:
            raise ValueError("Use distinct sensor IDs for upstream and downstream instruments")
        self.groups = {}
        self.max_age_s = max_age_s
        for key, group in frame.groupby(["vehicle_id", "trip_id"], sort=False):
            group = group.sort_values("source_time_s")
            self.groups[tuple(map(int, key))] = (group.source_time_s.tolist(), group.to_dict("records"))

    @classmethod
    def from_csv(cls, path, sensor_id, max_age_s=3.0):
        return cls(pd.read_csv(path, dtype={"sensor_id": str}), sensor_id, max_age_s)

    def at(self, vehicle_id, trip_id, t):
        times, rows = self.groups.get((vehicle_id, trip_id), ([], []))
        index = bisect_right(times, t) - 1
        if index < 0 or t - times[index] > self.max_age_s:
            return None
        row = rows[index]
        return signal_dict("nox_ppm", row["nox_ppm"], row["source_time_s"], row["provenance"],
                           sensor_id=str(row["sensor_id"]), position=row["position"], quality=row["quality"])


def np_finite_ids(frame):
    try:
        for col in ("vehicle_id", "trip_id", "source_time_s"):
            values = pd.to_numeric(frame[col], errors="raise")
            if not values.map(math.isfinite).all() or (values < 0).any():
                return False
            if col != "source_time_s" and (values != values.astype(int)).any():
                return False
        return True
    except (ValueError, TypeError, OverflowError):
        return False
