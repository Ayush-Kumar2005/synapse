"""Local DriftBeacon replay server. No external packages required."""

from __future__ import annotations

import json
import bisect
import math
import mimetypes
import re
import threading
import time
import uuid
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from model.reasoning import ReasoningConfig, rank_reasons


ROOT = Path(__file__).parent
SAMPLE_SECONDS = 15
DURATION_SECONDS = 42 * 60
WINDOW_SECONDS = 4 * 60
MAX_GAP_SECONDS = 45
CO2_KG_PER_L = 8.887 / 3.785411784
CO2_SOURCE = "US EPA: 8,887 g CO₂ per US gallon gasoline; converted using 3.785411784 L/US gallon"
SESSIONS: dict[str, "Replay"] = {}
LOCK = threading.RLock()


def source_trip() -> list[dict]:
    """Deterministic development fixture, deliberately not represented as VED data."""
    rows = []
    for seq, second in enumerate(range(0, DURATION_SECONDS + 1, SAMPLE_SECONDS)):
        minute = second / 60
        if minute < 8:
            speed = 30 + 13 * math.sin(minute * 1.3)
            context = "Urban driving"
        elif minute < 31:
            speed = 76 + 9 * math.sin(minute * .42)
            context = "Steady cruise"
        else:
            speed = 44 + 19 * math.sin(minute * .71)
            context = "Mixed roads"
        speed = max(7, speed)
        rpm = 800 + speed * 28 + 110 * math.sin(minute * 1.8)
        load = max(17, min(79, 30 + speed * .32 + 7 * math.sin(minute * 1.1)))
        expected = 1.9 + speed * .039 + load * .035 + max(0, rpm - 2400) * .00048
        observed = expected * (1 + .014 * math.sin(minute * 1.8) + .008 * math.cos(minute * 3.2))
        valid = not (64 <= seq <= 67 or 139 <= seq <= 141)
        rows.append({
            "seq": seq, "source_time_s": second, "speed_kph": round(speed, 1),
            "rpm": round(rpm), "load_pct": round(load, 1), "context": context,
            "expected_fuel_lph": round(expected, 3) if valid else None,
            "observed_fuel_lph": round(observed, 3) if valid else None,
            "data_quality": "adequate" if valid else "insufficient",
            "fuel_provenance": "generated development fixture",
        })
    return rows


TRIP = source_trip()
GENERATED_META = {"id": "generated-42-minute-trip", "duration_s": DURATION_SECONDS,
    "vehicle_id": "demo-gasoline-car", "source": "Generated development fixture; not VED",
    "target_provenance": "Generated expected and observed fuel rates; no trained model",
    "gps_available": False, "route_deidentified": False, "comparable_trip_count": 0}
PREPARED_PATH = ROOT / "data/prepared/ved_trip.json"
PREPARED = json.loads(PREPARED_PATH.read_text(encoding="utf-8")) if PREPARED_PATH.exists() else None
TRIPS = ([PREPARED["metadata"]] if PREPARED else []) + [GENERATED_META]


def driving_context(points: list[dict]) -> str:
    """Describe the vehicle's own motion, not outside traffic or a fault."""
    speeds = [p["speed_kph"] for p in points if p.get("speed_kph") is not None]
    if not speeds:
        return "Driving context unavailable"
    mean = sum(speeds) / len(speeds)
    if mean < 12:
        return "Low-speed / idle"
    if mean >= 65 and max(speeds) - min(speeds) < 30:
        return "Steady cruise"
    return "Variable-speed driving"


def build_sections(points: list[dict], channel: str, duration_s: int, section_s: int = 420) -> list[dict]:
    """Integrate the same source-time samples used by replay; missing intervals stay gaps."""
    sections = []
    for start in range(0, math.ceil(duration_s), section_s):
        end = min(duration_s, start + section_s)
        relevant = [item[channel] for item in points if start <= item["source_time_s"] <= end]
        expected_l = observed_l = distance_km = valid_s = 0.0
        weighted_speed = weighted_rpm = weighted_load = 0.0
        coordinates = []
        for pair_before, pair_after in zip(points, points[1:]):
            before, after = pair_before[channel], pair_after[channel]
            full_dt = after["source_time_s"] - before["source_time_s"]
            left = max(start, before["source_time_s"])
            right = min(end, after["source_time_s"])
            dt = right - left
            if not 0 < full_dt <= MAX_GAP_SECONDS or dt <= 0:
                continue
            if any(p.get(key) is None for p in (before, after) for key in ("expected_fuel_lph", "observed_fuel_lph", "speed_kph")):
                continue
            fractions = ((left - before["source_time_s"]) / full_dt, (right - before["source_time_s"]) / full_dt)
            def average(field):
                return sum(before[field] + (after[field] - before[field]) * f for f in fractions) / 2
            expected_l += average("expected_fuel_lph") * dt / 3600
            observed_l += average("observed_fuel_lph") * dt / 3600
            distance_km += average("speed_kph") * dt / 3600
            weighted_speed += average("speed_kph") * dt
            weighted_rpm += average("rpm") * dt
            weighted_load += average("load_pct") * dt
            valid_s += dt
            if all(p.get("latitude") is not None and p.get("longitude") is not None for p in (before, after)):
                for f in fractions:
                    coordinate = [before["latitude"] + (after["latitude"] - before["latitude"]) * f,
                                  before["longitude"] + (after["longitude"] - before["longitude"]) * f]
                    if not coordinates or coordinates[-1] != coordinate:
                        coordinates.append(coordinate)
        coverage = min(1.0, valid_s / (end - start))
        route_available = len(coordinates) >= 2 and coverage >= .999
        sections.append({
            "id": f"section-{start // section_s + 1}", "start_s": start, "end_s": end,
            "coordinates": coordinates if route_available else None, "route_available": route_available,
            "distance_km": round(distance_km, 3) if valid_s else None,
            "distance_method": "integrated vehicle speed over valid source-time intervals",
            "expected_l": round(expected_l, 4) if valid_s else None,
            "observed_l": round(observed_l, 4) if valid_s else None,
            "excess_l": round(max(0, observed_l - expected_l), 4) if valid_s else None,
            "coverage": round(coverage, 3),
            "mean_speed_kph": round(weighted_speed / valid_s, 1) if valid_s else None,
            "mean_rpm": round(weighted_rpm / valid_s) if valid_s else None,
            "mean_load_pct": round(weighted_load / valid_s, 1) if valid_s else None,
            "context": driving_context(relevant),
        })
    return sections


def trip_efficiency(points: list[dict], channel: str) -> dict:
    elapsed = points[-1]["source_time_s"] if points else 0
    sections = build_sections(points, channel, elapsed)
    distance = sum(s["distance_km"] or 0 for s in sections)
    observed = sum(s["observed_l"] or 0 for s in sections)
    return {"observed_l_per_100km": round(observed / distance * 100, 2) if distance > 0 else None,
            "distance_km": round(distance, 3), "distance_method": "integrated vehicle speed over valid source-time intervals"}


class Detector:
    def __init__(self):
        self.window = deque()
        self.last = None
        self.status = "collecting"
        self.alerts = []
        self.persist = 0
        self.expected_l = 0.0
        self.observed_l = 0.0
        self.valid_seconds = 0

    def feed(self, point: dict) -> dict:
        second = point["source_time_s"]
        expected = point["expected_fuel_lph"]
        observed = point["observed_fuel_lph"]
        if expected is None or observed is None:
            self.window.clear()
            self.last = None
            self.persist = 0
            if not self.alerts:
                self.status = "insufficient"
            return self.summary()

        if self.last is not None:
            dt = second - self.last["source_time_s"]
            if 0 < dt <= MAX_GAP_SECONDS:
                exp_l = (expected + self.last["expected_fuel_lph"]) / 2 * dt / 3600
                obs_l = (observed + self.last["observed_fuel_lph"]) / 2 * dt / 3600
                self.window.append((second, dt, exp_l, obs_l))
                self.expected_l += exp_l
                self.observed_l += obs_l
                self.valid_seconds += dt
            else:
                self.window.clear()
                self.persist = 0
        self.last = point

        while self.window and self.window[0][0] <= second - WINDOW_SECONDS:
            self.window.popleft()
        seconds = sum(item[1] for item in self.window)
        exp_window = sum(item[2] for item in self.window)
        obs_window = sum(item[3] for item in self.window)
        coverage = min(1.0, seconds / WINDOW_SECONDS)

        if self.alerts:
            self.status = "inspection"
        elif coverage < .8 or exp_window < .15:
            self.status = "collecting"
            self.persist = 0
        else:
            excess_pct = (obs_window - exp_window) / exp_window * 100
            if excess_pct >= 8:
                self.persist += 1
                self.status = "watch"
                if self.persist >= 2:
                    self.status = "inspection"
                    self.alerts.append({
                        "id": "alert-1", "detected_at_s": second,
                        "evidence_start_s": second - seconds,
                        "duration_s": seconds,
                        "window_expected_l": round(exp_window, 3),
                        "window_observed_l": round(obs_window, 3),
                        "excess_l": round(max(0, obs_window - exp_window), 3),
                        "excess_pct": round(excess_pct, 1),
                        "valid_coverage": round(coverage, 2),
                        "explanation": "Estimated fuel use exceeded the expected amount over a sustained four-minute window.",
                    })
            else:
                self.persist = 0
                self.status = "within_range"
        return self.summary()

    def summary(self) -> dict:
        excess = max(0, self.observed_l - self.expected_l)
        return {
            "status": self.status,
            "expected_l": round(self.expected_l, 3),
            "observed_l": round(self.observed_l, 3),
            "excess_l": round(excess, 3),
            "excess_pct": round(excess / self.expected_l * 100, 1) if self.expected_l >= .15 else None,
            "valid_seconds": self.valid_seconds,
            "alerts": self.alerts.copy(),
        }


class Replay:
    def __init__(self, severity: float, onset_s: int, ramp_s: int, trip_id: str = GENERATED_META["id"]):
        self.id = uuid.uuid4().hex
        self.severity = severity
        self.onset_s = onset_s
        self.ramp_s = ramp_s
        self.trip_id = trip_id
        if PREPARED and trip_id == PREPARED["metadata"]["id"]:
            self.trip = PREPARED["rows"]
            self.meta = PREPARED["metadata"]
        else:
            self.trip = TRIP
            self.meta = GENERATED_META
        self.duration_s = self.meta["duration_s"]
        self.sample_times = [row["source_time_s"] for row in self.trip]
        self.speed = 1
        self.playing = False
        self.last_wall = time.monotonic()
        self.source_cursor = 0.0
        self.index = -1
        self.points = []
        self.puc_latched = False
        self.original = Detector()
        self.simulated = Detector()

    def advance(self):
        now = time.monotonic()
        if self.playing:
            self.source_cursor = min(self.duration_s, self.source_cursor + (now - self.last_wall) * 30 * self.speed)
        self.last_wall = now
        target = bisect.bisect_right(self.sample_times, self.source_cursor) - 1
        while self.index < target:
            self.index += 1
            base = self.trip[self.index].copy()
            altered = base.copy()
            if altered["observed_fuel_lph"] is not None:
                factor = self.severity * max(0, min(1, (base["source_time_s"] - self.onset_s) / self.ramp_s))
                altered["observed_fuel_lph"] = round(altered["observed_fuel_lph"] * (1 + factor), 3)
            original_summary = self.original.feed(base)
            simulated_summary = self.simulated.feed(altered)
            self.points.append({"seq": self.index, "source_time_s": base["source_time_s"],
                                "original": {**base, "status": original_summary["status"]},
                                "simulated": {**altered, "status": simulated_summary["status"]}})
        if self.source_cursor >= self.duration_s:
            self.playing = False

    def response(self, after_seq: int = -1):
        self.advance()
        active = self.simulated if self.severity else self.original
        alert = active.alerts[0] if active.alerts else None
        point = self.points[-1]["simulated" if self.severity else "original"] if self.points else None
        reasoning = None
        if alert:
            high_drift = self.severity >= .25
            samples = [{"time_s": p["source_time_s"],
                        "values": {key: value for key, value in {
                            "rpm": p["original"].get("rpm"),
                            "stft1_pct": (-12.0 if high_drift else 12.0) if self.severity and p["source_time_s"] >= self.onset_s else p["original"].get("stft1_pct"),
                            "ltft1_pct": (-8.0 if high_drift else 7.0) if self.severity and p["source_time_s"] >= self.onset_s else p["original"].get("ltft1_pct"),
                            "coolant_c": 88.0 if self.severity else None,
                            "closed_loop": 1.0 if self.severity else None,
                            "lambda": .92 if high_drift and p["source_time_s"] >= self.onset_s else None,
                            "fuel_pressure_kpa": 270.0 if self.severity and not high_drift and p["source_time_s"] >= self.onset_s else None}.items() if value is not None},
                        "provenance": {"diagnostic_signals": "synthetic" if self.severity else "recorded_age_unverified"},
                        "trip_elapsed_s": p["source_time_s"]} for p in self.points]
            reasoning = rank_reasons(samples, alert,
                                     "derived_maf_trims" if self.meta["gps_available"] else "supplied_observation",
                                     ReasoningConfig(max_sample_gap_s=7.0,
                                                     fuel_pressure_min_kpa=300.0 if self.severity and not high_drift else None))
        # Explicitly synthetic exhaust channels for the presentation. These are
        # scenario values, never VED measurements or legal PUC thresholds.
        progress = max(0.0, min(1.0, (self.source_cursor - 600) / 120)) if self.severity else 0.0
        co_pct = round(.18 + (.65 if self.severity >= .25 else .15) * progress, 3)
        nox_ppm = round(85 + (150 if self.severity >= .25 else 35) * progress)
        extra_co2 = round(active.summary()["excess_l"] * CO2_KG_PER_L, 3)
        markers_reached = bool(alert and extra_co2 >= .03 and co_pct >= .50 and nox_ppm >= 150)
        self.puc_latched = self.puc_latched or markers_reached
        if self.puc_latched:
            puc = {"status": "consider_test", "basis": "synthetic_demo_markers",
                   "message": "CO, NOx and estimated extra CO₂ crossed this demo's advisory markers. Consider an authorised PUC test; this is not a PUC result."}
        else:
            puc = {"status": "not_assessed", "basis": "no_verified_emission_threshold",
                   "message": "PUC assessment requires measured emissions and the applicable vehicle limits."}
        return {
            "id": self.id, "cursor": self.index, "playing": self.playing,
            "completed": self.source_cursor >= self.duration_s,
            "source_time_s": self.trip[max(0, self.index)]["source_time_s"],
            "duration_s": self.duration_s, "speed": self.speed,
            "scenario": {"severity_pct": round(self.severity * 100), "onset_s": self.onset_s,
                         "ramp_s": self.ramp_s, "is_synthetic": True},
            "points": self.points[after_seq + 1:],
            "original": self.original.summary(), "simulated": self.simulated.summary(),
            "co2": {"kg_per_l": CO2_KG_PER_L, "source": CO2_SOURCE,
                    "scope": "estimated extra tailpipe CO₂ from excess gasoline"},
            "route_available": self.meta["gps_available"],
            "emissions": {"co2_extra_kg": extra_co2,
                          "co_pct": co_pct, "co_provenance": "synthetic demo",
                          "nox_ppm": nox_ppm, "nox_provenance": "synthetic demo",
                          "demo_markers": {"extra_co2_kg": .03, "co_pct": .50, "nox_ppm": 150}},
            "puc_advisory": puc,
            "fault_analysis": {"available": bool(reasoning and reasoning["likely_reasons"]),
                "case_id": self.meta["id"], "expert_system": reasoning,
                "scenario_label": ("Possible rich-mixture issue" if self.severity >= .25 else "Possible fuel-delivery pressure issue") if self.severity and alert else None,
                "reason": "Expert rules need warm closed-loop and corroborating diagnostic signals" if reasoning and not reasoning["likely_reasons"] else None},
        }


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        if not self.path.startswith('/api/replays/'):
            super().log_message(format, *args)

    def send_json(self, body, status=200):
        encoded = json.dumps(body, allow_nan=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(encoded)

    def read_json(self):
        length = int(self.headers.get("Content-Length", 0))
        if length > 20_000:
            raise ValueError("Request too large")
        return json.loads(self.rfile.read(length) or b"{}")

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/health":
            return self.send_json({"ready": True, "model_loaded": PREPARED is not None,
                                   "main_model_artifact_loaded": (ROOT / "artifacts/models/cpu_baseline/metadata.json").exists(),
                                   "expert_system_available": True,
                                   "mode": "VED model replay available" if PREPARED else "generated development fixture",
                                   "detector_version": "demo-1", "route_available": PREPARED is not None,
                                   "model_validation": PREPARED["model"]["metrics"] if PREPARED else None,
                                   "co2": {"kg_per_l": CO2_KG_PER_L, "source": CO2_SOURCE}})
        if parsed.path == "/api/trips":
            return self.send_json({"trips": TRIPS})
        match = re.fullmatch(r"/api/replays/([0-9a-f]{32})(/report|/sections)?", parsed.path)
        if match:
            with LOCK:
                replay = SESSIONS.get(match.group(1))
                if not replay:
                    return self.send_json({"error": "Replay not found"}, 404)
                if match.group(2):
                    if match.group(2) == "/sections":
                        replay.advance()
                        elapsed = replay.points[-1]["source_time_s"] if replay.points else 0
                        return self.send_json({"id": replay.id, "cursor": replay.index,
                            "source": replay.meta["source"], "route_available": replay.meta["gps_available"],
                            "sections": {channel: build_sections(replay.points, channel, elapsed)
                                         for channel in ("original", "simulated")},
                            "efficiency": {channel: trip_efficiency(replay.points, channel)
                                           for channel in ("original", "simulated")}})
                    result = replay.response()
                    result["points"] = replay.points
                    result["provenance"] = replay.meta["source"] + "; " + replay.meta["target_provenance"]
                    result["detector_config"] = {"window_s": WINDOW_SECONDS, "threshold_pct": 8,
                        "required_coverage": .8, "persistence_samples": 2, "max_gap_s": MAX_GAP_SECONDS,
                        "validation": "Provisional demo settings; not validated on real faults"}
                    elapsed = replay.points[-1]["source_time_s"] if replay.points else 0
                    result["sections"] = {channel: build_sections(replay.points, channel, elapsed)
                                          for channel in ("original", "simulated")}
                    result["efficiency"] = {channel: trip_efficiency(replay.points, channel)
                                            for channel in ("original", "simulated")}
                    return self.send_json(result)
                after = int(parse_qs(parsed.query).get("after_seq", ["-1"])[0])
                return self.send_json(replay.response(after))
        path = ROOT / "web" / (parsed.path.lstrip("/") or "index.html")
        if not path.resolve().is_relative_to((ROOT / "web").resolve()) or not path.is_file():
            return self.send_error(404)
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mimetypes.guess_type(path)[0] or "application/octet-stream")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        try:
            body = self.read_json()
            if self.path == "/api/replays":
                trip_id = body.get("trip_id", TRIPS[0]["id"])
                matching = next((trip for trip in TRIPS if trip["id"] == trip_id), None)
                if matching is None:
                    raise ValueError("Unknown trip")
                severity = float(body.get("severity_pct", 15))
                onset = int(body.get("onset_s", min(4 * 60, matching["duration_s"] // 3)))
                ramp = int(body.get("ramp_s", 2 * 60))
                if not (0 <= severity <= 30 and 0 <= onset < matching["duration_s"] and 60 <= ramp <= 15 * 60):
                    raise ValueError("Scenario setting out of range")
                with LOCK:
                    replay = Replay(severity / 100, onset, ramp, trip_id)
                    SESSIONS[replay.id] = replay
                    return self.send_json(replay.response(), 201)
            match = re.fullmatch(r"/api/replays/([0-9a-f]{32})/control", self.path)
            if match:
                with LOCK:
                    replay = SESSIONS.get(match.group(1))
                    if not replay:
                        return self.send_json({"error": "Replay not found"}, 404)
                    replay.advance()
                    action = body.get("action")
                    if action == "play":
                        replay.playing = True
                    elif action == "pause":
                        replay.playing = False
                    elif action == "reset":
                        replacement = Replay(replay.severity, replay.onset_s, replay.ramp_s, replay.trip_id)
                        replacement.id = replay.id
                        SESSIONS[replay.id] = replay = replacement
                    elif action == "seek":
                        target_s = int(body.get("source_time_s", 0))
                        if not 0 <= target_s <= replay.duration_s:
                            raise ValueError("Seek time out of range")
                        replacement = Replay(replay.severity, replay.onset_s, replay.ramp_s, replay.trip_id)
                        replacement.id = replay.id
                        replacement.speed = replay.speed
                        replacement.source_cursor = target_s
                        SESSIONS[replay.id] = replay = replacement
                    elif action == "speed" and body.get("speed") in (1, 2, 4):
                        replay.speed = body["speed"]
                    else:
                        raise ValueError("Unknown control")
                    return self.send_json(replay.response())
            return self.send_json({"error": "Not found"}, 404)
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            return self.send_json({"error": str(exc)}, 400)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    print(f"DriftBeacon local preview: http://127.0.0.1:{args.port}", flush=True)
    ThreadingHTTPServer(("127.0.0.1", args.port), Handler).serve_forever()
