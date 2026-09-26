"""Adapt prepared VED trips and an optional NOx CSV into fusion JSONL packets."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from .common import FEATURES, RENAME, ROOT, TARGET
from .telemetry import NoxFeed, signal_dict


def attach_raw_sensors(trip):
    """Recover MAF/trims by exact original row identity; never nearest-match them."""
    extras = ["maf_gps", "stft1_pct", "stft2_pct", "ltft1_pct", "ltft2_pct"]
    chunks = []
    for name in trip.source_file.unique():
        path = ROOT / "data/raw/dynamic" / str(name)
        if not path.exists():
            continue
        for chunk in pd.read_csv(path, chunksize=100000):
            chunk = chunk.rename(columns=RENAME)
            selected = chunk[chunk.vehicle_id.eq(trip.vehicle_id.iloc[0]) & chunk.trip_id.eq(trip.trip_id.iloc[0])]
            if not selected.empty:
                chunks.append(selected[["vehicle_id", "trip_id", "timestamp_ms", *extras]])
    if not chunks:
        return trip
    raw = pd.concat(chunks).drop_duplicates(["vehicle_id", "trip_id", "timestamp_ms"], keep="last")
    return trip.merge(raw, on=["vehicle_id", "trip_id", "timestamp_ms"], how="left", validate="one_to_one")


def ved_packets(trip, nox_feed=None):
    if trip[["vehicle_id", "trip_id"]].drop_duplicates().shape[0] != 1:
        raise ValueError("Pass one vehicle/trip per adapter invocation")
    for row in trip.sort_values("source_time_s").to_dict("records"):
        t = float(row["source_time_s"])
        signal_names = FEATURES + ["maf_gps", "stft1_pct", "stft2_pct", "ltft1_pct", "ltft2_pct"]
        signals = {k: signal_dict(k, row[k], t, "recorded_age_unverified") for k in signal_names if k in row}
        # Keep preparation's invalid-row policy even when a subset looks usable.
        signals[TARGET] = signal_dict(TARGET, row[TARGET], t, "derived_maf_trims",
                                     quality="valid" if row["model_valid"] else "unavailable")
        if nox_feed:
            nox = nox_feed.at(int(row["vehicle_id"]), int(row["trip_id"]), t)
            if nox is not None:
                signals["nox_ppm"] = nox
        yield {"vehicle_id": int(row["vehicle_id"]), "trip_id": int(row["trip_id"]),
               "source_time_s": t, "mode": "ved_replay", "signals": signals}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--vehicle", type=int, required=True)
    p.add_argument("--trip", type=int, required=True)
    p.add_argument("--nox-csv", type=Path)
    p.add_argument("--sensor-id")
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    if args.output.exists():
        p.error("Choose a new output path")
    if args.nox_csv and not args.sensor_id:
        p.error("--sensor-id is required with --nox-csv")
    # Development adapters use validation only; do not reopen the VED test set.
    data = pd.read_parquet(ROOT / "data/prepared/validation.parquet")
    trip = data[data.vehicle_id.eq(args.vehicle) & data.trip_id.eq(args.trip)]
    if trip.empty:
        p.error("Vehicle/trip not in validation")
    feed = NoxFeed.from_csv(args.nox_csv, args.sensor_id) if args.nox_csv else None
    trip = attach_raw_sensors(trip)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as out:
        for packet in ved_packets(trip, feed):
            out.write(json.dumps(packet, allow_nan=False) + "\n")
    print(f"Exported {len(trip)} VED packets to {args.output}")


if __name__ == "__main__":
    main()
