"""Prepare one bounded, GPS-aligned VED gasoline replay from the official first-week CSV.

Uses only Python's standard library. The model is a small ridge baseline fitted on
other ICE vehicles; the observed target is an estimated MAF/fuel-trim fuel rate.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
from collections import defaultdict
from pathlib import Path
from zipfile import ZipFile
import xml.etree.ElementTree as ET


ROOT = Path(__file__).parent
CSV = ROOT / "data/raw/VED_171101_week.csv"
STATIC = ROOT / "data/raw/VED_Static_Data_ICE_HEV.xlsx"
OUT = ROOT / "data/prepared/ved_trip.json"
SEED = "driftbeacon-week1-v1"
DEMO_VEHICLE = "494"
DEMO_TRIP = "1023"
AFR_E10 = 14.08
GASOLINE_DENSITY_G_PER_L = 749.0


def vehicle_metadata():
    ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    with ZipFile(STATIC) as archive:
        strings = ["".join(item.itertext()) for item in ET.fromstring(archive.read("xl/sharedStrings.xml")).findall("m:si", ns)]
        sheet = ET.fromstring(archive.read("xl/worksheets/sheet1.xml"))
        vehicles = {}
        for row in sheet.findall(".//m:sheetData/m:row", ns):
            cells = []
            for cell in row.findall("m:c", ns):
                raw = cell.find("m:v", ns)
                value = raw.text if raw is not None else ""
                if cell.get("t") == "s":
                    value = strings[int(value)]
                cells.append(value)
            if len(cells) >= 7 and cells[0].isdigit():
                vehicles[cells[0]] = {"type": cells[1], "class": cells[2], "engine": cells[3],
                                      "weight_lb": cells[6]}
    return vehicles


def number(value):
    try:
        parsed = float(value)
        return parsed if math.isfinite(parsed) else None
    except (ValueError, TypeError):
        return None


def target(row):
    maf = number(row["MAF[g/sec]"])
    stft = number(row["Short Term Fuel Trim Bank 1[%]"])
    ltft = number(row["Long Term Fuel Trim Bank 1[%]"])
    if maf is None or stft is None or ltft is None or not (0 < maf < 250 and -50 < stft < 50 and -50 < ltft < 50):
        return None
    rate = maf * (1 + stft / 100 + ltft / 100) / AFR_E10 * 3600 / GASOLINE_DENSITY_G_PER_L
    return rate if 0 < rate < 60 else None


def features(row):
    speed = number(row["Vehicle Speed[km/h]"])
    rpm = number(row["Engine RPM[RPM]"])
    load = number(row["Absolute Load[%]"])
    if speed is None or rpm is None or load is None or not (0 <= speed <= 180 and 0 <= rpm <= 7000 and 0 <= load <= 100):
        return None
    s, r, l = speed / 100, rpm / 3000, load / 100
    return [1.0, s, r, l, r * l]


def split(vehicle_id):
    if vehicle_id == DEMO_VEHICLE:
        return "test"
    value = int(hashlib.sha256(f"{SEED}:{vehicle_id}".encode()).hexdigest()[:8], 16) % 100
    return "train" if value < 70 else "validation" if value < 85 else "test"


def solve(matrix, vector):
    size = len(vector)
    augmented = [matrix[i][:] + [vector[i]] for i in range(size)]
    for column in range(size):
        pivot = max(range(column, size), key=lambda r: abs(augmented[r][column]))
        augmented[column], augmented[pivot] = augmented[pivot], augmented[column]
        divisor = augmented[column][column]
        if abs(divisor) < 1e-12:
            raise ValueError("Model matrix is singular")
        for j in range(column, size + 1):
            augmented[column][j] /= divisor
        for row in range(size):
            if row == column:
                continue
            factor = augmented[row][column]
            for j in range(column, size + 1):
                augmented[row][j] -= factor * augmented[column][j]
    return [augmented[i][-1] for i in range(size)]


def fit(vehicles):
    size = 5
    xtx = [[0.0] * size for _ in range(size)]
    xty = [0.0] * size
    counts = defaultdict(int)
    split_vehicles = defaultdict(set)
    with CSV.open(newline="", encoding="utf-8") as file:
        for row in csv.DictReader(file):
            vid = row["VehId"]
            if vehicles.get(vid, {}).get("type") != "ICE":
                continue
            x, y = features(row), target(row)
            if x is None or y is None:
                continue
            group = split(vid)
            counts[group] += 1
            split_vehicles[group].add(vid)
            if group == "train":
                for i in range(size):
                    xty[i] += x[i] * y
                    for j in range(size):
                        xtx[i][j] += x[i] * x[j]
    for i in range(1, size):
        xtx[i][i] += .1
    coefficients = solve(xtx, xty)
    return coefficients, dict(counts), {k: sorted(v, key=int) for k, v in split_vehicles.items()}


def prediction(x, coefficients):
    return max(.1, sum(a * b for a, b in zip(x, coefficients)))


def evaluate(vehicles, coefficients):
    stats = {group: [0, 0.0, 0.0] for group in ("train", "validation", "test")}
    with CSV.open(newline="", encoding="utf-8") as file:
        for row in csv.DictReader(file):
            vid = row["VehId"]
            if vehicles.get(vid, {}).get("type") != "ICE":
                continue
            x, y = features(row), target(row)
            if x is None or y is None:
                continue
            error = prediction(x, coefficients) - y
            stat = stats[split(vid)]
            stat[0] += 1; stat[1] += abs(error); stat[2] += error * error
    return {group: {"rows": s[0], "mae_lph": round(s[1] / s[0], 3) if s[0] else None,
                    "rmse_lph": round(math.sqrt(s[2] / s[0]), 3) if s[0] else None}
            for group, s in stats.items()}


def prepare_demo(vehicles, coefficients):
    if vehicles.get(DEMO_VEHICLE, {}).get("type") != "ICE":
        raise ValueError("Selected VED vehicle is not classified as ICE")
    rows = []
    with CSV.open(newline="", encoding="utf-8") as file:
        for row in csv.DictReader(file):
            if row["VehId"] == DEMO_VEHICLE and row["Trip"] == DEMO_TRIP:
                rows.append(row)
    rows.sort(key=lambda row: number(row["Timestamp(ms)"]) or -1)
    if not rows:
        raise ValueError("Selected VED trip missing")
    # Use the longest continuous part of this published trip. Its first few
    # records are separated from the main drive by a 17-minute logging gap.
    segments = []
    segment = []
    previous_ms = None
    for row in rows:
        ms = number(row["Timestamp(ms)"])
        if ms is None:
            continue
        if previous_ms is not None and ms - previous_ms > 45_000:
            if segment:
                segments.append(segment)
            segment = []
        segment.append(row)
        previous_ms = ms
    if segment:
        segments.append(segment)
    rows = max(segments, key=lambda part: number(part[-1]["Timestamp(ms)"]) - number(part[0]["Timestamp(ms)"]))
    first_ms = number(rows[0]["Timestamp(ms)"])
    end_ms = number(rows[-1]["Timestamp(ms)"])
    chosen = []
    last_selected = float("-inf")
    for row in rows:
        ms = number(row["Timestamp(ms)"])
        lat = number(row["Latitude[deg]"])
        lon = number(row["Longitude[deg]"])
        if ms is None or not first_ms <= ms <= end_ms or ms - last_selected < 5000:
            continue
        if lat is None or lon is None or not (41 < lat < 43 and -85 < lon < -82):
            continue
        x = features(row)
        y = target(row)
        if x is None or y is None:
            continue
        chosen.append({"source_time_s": round((ms - first_ms) / 1000, 1),
                       "latitude": round(lat, 7), "longitude": round(lon, 7),
                       "speed_kph": round(number(row["Vehicle Speed[km/h]"]), 1),
                       "rpm": round(number(row["Engine RPM[RPM]"])),
                       "load_pct": round(number(row["Absolute Load[%]"]), 1),
                       "stft1_pct": number(row["Short Term Fuel Trim Bank 1[%]"]),
                       "ltft1_pct": number(row["Long Term Fuel Trim Bank 1[%]"]),
                       "expected_fuel_lph": round(prediction(x, coefficients), 3),
                       "observed_fuel_lph": round(y, 3), "data_quality": "adequate",
                       "fuel_provenance": "VED MAF + bank-1 short/long fuel trims; estimated L/h"})
        last_selected = ms
    if len(chosen) < 100:
        raise ValueError("Selected trip has too few aligned samples")
    offset = chosen[0]["source_time_s"]
    for seq, row in enumerate(chosen):
        row["seq"] = seq
        row["source_time_s"] = round(row["source_time_s"] - offset, 1)
        row["context"] = "Recorded driving"
    # Calibrate the held-out vehicle's fuel scale on its opening four minutes.
    # This uses observed fuel only before the demonstration ramp begins.
    calibration = [row for row in chosen if row["source_time_s"] <= 240]
    scale = sum(row["observed_fuel_lph"] for row in calibration) / sum(row["expected_fuel_lph"] for row in calibration)
    for row in chosen:
        row["expected_fuel_lph"] = round(row["expected_fuel_lph"] * scale, 3)
    prepare_demo.calibration_scale = round(scale, 4)
    return chosen


def main():
    vehicles = vehicle_metadata()
    coefficients, counts, split_vehicles = fit(vehicles)
    metrics = evaluate(vehicles, coefficients)
    rows = prepare_demo(vehicles, coefficients)
    metadata = {
        "id": "ved-vehicle-494-trip-1023", "vehicle_id": DEMO_VEHICLE,
        "source_trip_id": DEMO_TRIP, "duration_s": math.ceil(rows[-1]["source_time_s"]),
        "source": "VED recorded ICE trip, 2017-11-01 week; longest continuous part of published de-identified GPS trajectory",
        "target_provenance": "MAF + bank-1 short/long fuel trims; estimated observed gasoline L/h",
        "expected_provenance": "Fitted ridge baseline using speed, RPM, load and RPM×load from other ICE vehicles; scale calibrated on first four minutes of this drive",
        "gps_available": True, "route_deidentified": True, "comparable_trip_count": 0,
        "vehicle_description": vehicles[DEMO_VEHICLE]["engine"],
    }
    model = {
        "type": "ridge linear baseline", "coefficients": coefficients,
        "features": ["intercept", "speed_kph/100", "rpm/3000", "load_pct/100", "rpm/3000 × load_pct/100"],
        "split_seed": SEED, "split_vehicles": split_vehicles, "usable_rows": counts,
        "metrics": metrics,
        "target_formula": "MAF[g/s] × (1+STFT_B1/100+LTFT_B1/100) / 14.08 × 3600 / 749[g/L]",
        "demo_vehicle_calibration_scale": prepare_demo.calibration_scale,
        "target_assumptions": "E10 stoichiometric AFR 14.08 (VED paper); gasoline density 0.749 kg/L from US DOE AFDC reference fuel. Estimated fuel, not direct measurement.",
        "source_csv": "VED_171101_week.csv", "source_static": "VED_Static_Data_ICE&HEV.xlsx",
        "validation_warning": "One-week subset, estimated target, provisional detector; not validated for real faults.",
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"metadata": metadata, "model": model, "rows": rows}, separators=(",", ":")), encoding="utf-8")
    print(json.dumps({"rows": len(rows), "duration_s": metadata["duration_s"], "metrics": metrics,
                      "model_vehicles": {k: len(v) for k, v in split_vehicles.items()},
                      "output": str(OUT)}, indent=2))


if __name__ == "__main__":
    main()
