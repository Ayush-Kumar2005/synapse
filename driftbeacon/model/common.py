from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FEATURES = ["speed_kph", "rpm", "load_pct", "acceleration_mps2", "displacement_l", "weight_kg"]
TARGET = "observed_fuel_lph"
RENAME = {
    "DayNum": "day_num", "VehId": "vehicle_id", "Trip": "trip_id",
    "Timestamp(ms)": "timestamp_ms", "Vehicle Speed[km/h]": "speed_kph",
    "MAF[g/sec]": "maf_gps", "Engine RPM[RPM]": "rpm", "Absolute Load[%]": "load_pct",
    "Fuel Rate[L/hr]": "reported_fuel_lph",
    "Short Term Fuel Trim Bank 1[%]": "stft1_pct",
    "Short Term Fuel Trim Bank 2[%]": "stft2_pct",
    "Long Term Fuel Trim Bank 1[%]": "ltft1_pct",
    "Long Term Fuel Trim Bank 2[%]": "ltft2_pct",
}


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def stable_rank(seed, *parts):
    return hashlib.sha256(":".join(map(str, (seed, *parts))).encode()).hexdigest()
