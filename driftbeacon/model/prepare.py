"""Audit every source row; build a bounded set of whole trips, split by vehicle."""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import subprocess

import numpy as np
import pandas as pd

from .common import FEATURES, RENAME, ROOT, TARGET, read_json, sha256, stable_rank, write_json


def metadata(path):
    df = pd.read_excel(path)
    if df.VehId.duplicated().any():
        raise ValueError("Static metadata has duplicate vehicle IDs")
    engine = df["Engine Configuration & Displacement"].fillna("").astype(str)
    exclusion = pd.Series("", index=df.index)
    exclusion.loc[df["Vehicle Type"].ne("ICE")] = "not_ICE"
    exclusion.loc[df["Vehicle Type"].eq("ICE") & engine.str.contains("DSL|DIESEL", case=False)] = "diesel"
    exclusion.loc[df["Vehicle Type"].eq("ICE") & engine.str.contains("FLEX|E85", case=False)] = "flex_fuel_unknown_blend"
    displacement = pd.to_numeric(engine.str.extract(r"(\d+(?:\.\d+)?)\s*L", expand=False), errors="coerce")
    exclusion.loc[exclusion.eq("") & ~displacement.between(0.5, 10)] = "unknown_displacement_or_fuel"
    df["exclusion"] = exclusion
    df["displacement_l"] = displacement
    df["weight_kg"] = pd.to_numeric(df["Generalized_Weight"], errors="coerce") * 0.45359237
    df.loc[~df.weight_kg.between(500, 6000), "weight_kg"] = np.nan
    return df


def fuel_target(df, config):
    """VED Algorithm 1's MAF branch, with explicit g/s -> L/h conversion.

    Bank 1 STFT and LTFT are required. Average bank 2 when supplied;
    malformed supplied trims invalidate that row rather than disappearing.
    """
    trims = df[["stft1_pct", "stft2_pct", "ltft1_pct", "ltft2_pct"]]
    valid_trims = (trims.isna() | (trims.ge(-99) & trims.le(99))).all(axis=1)
    required = df.stft1_pct.notna() & df.ltft1_pct.notna() & valid_trims
    stft = df[["stft1_pct", "stft2_pct"]].mean(axis=1)
    ltft = df[["ltft1_pct", "ltft2_pct"]].mean(axis=1)
    factor = 1 + stft / 100 + ltft / 100
    value = df.maf_gps * factor / config["air_fuel_ratio"] * 3600 / config["fuel_density_g_per_l"]
    good = required & df.maf_gps.between(0, 1000) & factor.gt(0) & np.isfinite(value)
    return value.where(good)


def dynamic_valid(df):
    return df.speed_kph.between(0, 250) & df.rpm.between(400, 8000) & df.load_pct.between(0, 300)


def prepare_trip(frame, config):
    # Duplicate policy: retain the last source row; never combine fields from rows.
    frame = frame.sort_values("timestamp_ms", kind="stable").drop_duplicates("timestamp_ms", keep="last").copy()
    frame["source_time_s"] = frame.timestamp_ms / 1000.0
    dt = frame.source_time_s.diff()
    speed_ok = frame.speed_kph.between(0, 250)
    times = frame.source_time_s.to_numpy()
    previous = np.searchsorted(times, times - config["min_acceleration_dt_s"], side="right") - 1
    indexes = np.maximum(previous, 0)
    elapsed = times - times[indexes]
    # Latest earlier source observation at least 0.5 s away reduces event-timestamp noise.
    segments = (dt.gt(config["max_gap_s"]) | ~speed_ok).cumsum().to_numpy()
    with np.errstate(divide="ignore", invalid="ignore"):
        accel = pd.Series((frame.speed_kph.to_numpy() - frame.speed_kph.to_numpy()[indexes]) / 3.6 / elapsed, index=frame.index)
    accel_ok = (previous >= 0) & (elapsed >= config["min_acceleration_dt_s"]) & (elapsed <= config["max_gap_s"]) & speed_ok & speed_ok.to_numpy()[indexes] & (segments == segments[indexes])
    frame["acceleration_mps2"] = accel.where(accel_ok & accel.abs().le(config["max_abs_acceleration_mps2"]))
    frame[TARGET] = fuel_target(frame, config)
    frame["model_valid"] = dynamic_valid(frame) & frame.acceleration_mps2.notna() & frame.displacement_l.notna() & frame[TARGET].notna()
    frame["data_quality"] = np.select(
        [frame[TARGET].isna(), ~dynamic_valid(frame), frame.acceleration_mps2.isna()],
        ["missing_or_invalid_target", "invalid_required_sensor", "invalid_time_or_acceleration"],
        default="source_values_age_unverified",
    )
    frame["fuel_provenance"] = "estimated_maf_trim_e10"
    return frame


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw", type=Path, default=ROOT / "data/raw")
    parser.add_argument("--config", type=Path, default=ROOT / "config/preparation.json")
    parser.add_argument("--out", type=Path, default=ROOT / "data/prepared")
    args = parser.parse_args()
    cfg = read_json(args.config)
    source = args.raw / "ved-source"
    meta = metadata(source / "Data/VED_Static_Data_ICE&HEV.xlsx")
    eligible_meta = meta[meta.exclusion.eq("")].set_index("VehId")
    files = sorted((args.raw / "dynamic").glob("VED_*_week.csv"))
    if not files:
        raise FileNotFoundError("No extracted weekly CSVs. Run python -m model.acquire first.")
    args.out.mkdir(parents=True, exist_ok=True)
    interim = ROOT / "data/interim"
    interim.mkdir(parents=True, exist_ok=True)
    report_dir = ROOT / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    vehicle_rows, raw_missing, eligible_missing, powertrain_rows = Counter(), Counter(), Counter(), Counter()
    raw_columns = None
    trip_parts, file_audit, total, eligible_total, target_total = [], [], 0, 0, 0
    direct_gas_rows = 0
    types = meta.set_index("VehId")["Vehicle Type"].to_dict()
    for n, path in enumerate(files, 1):
        # One week at a time bounds peak memory; all source rows contribute to audit.
        raw = pd.read_csv(path)
        if not set(RENAME).issubset(raw.columns):
            raise ValueError(f"Missing required columns in {path.name}")
        if raw_columns is None:
            raw_columns = raw.columns.tolist()
        elif raw_columns != raw.columns.tolist():
            raise ValueError("Source schemas differ")
        total += len(raw)
        raw_missing.update(raw.isna().sum().to_dict())
        vehicle_rows.update(raw.VehId.value_counts().to_dict())
        powertrain_rows.update(raw.VehId.map(types).fillna("outside_ICE_HEV_metadata").value_counts().to_dict())
        df = raw.loc[raw.VehId.isin(eligible_meta.index), list(RENAME)].rename(columns=RENAME).copy()
        if df[["vehicle_id", "trip_id", "timestamp_ms", "day_num"]].isna().any().any():
            raise ValueError("Missing source identity/time")
        eligible_total += len(df)
        eligible_missing.update(df.isna().sum().to_dict())
        df["source_file"] = path.name
        df[TARGET] = fuel_target(df, cfg)
        target_total += int(df[TARGET].notna().sum())
        direct_gas_rows += int(df.reported_fuel_lph.notna().sum())
        df["candidate_valid"] = df[TARGET].notna() & dynamic_valid(df)
        trip_parts.append(df.groupby(["vehicle_id", "trip_id"]).agg(
            rows=("timestamp_ms", "size"), valid_rows=("candidate_valid", "sum"),
            min_ms=("timestamp_ms", "min"), max_ms=("timestamp_ms", "max"), day_num=("day_num", "min"),
        ).reset_index())
        df.drop(columns=["candidate_valid", TARGET]).to_parquet(interim / (path.stem + ".parquet"), index=False)
        file_audit.append({"file": path.name, "bytes": path.stat().st_size, "sha256": sha256(path), "rows": len(raw), "eligible_rows": len(df), "target_rows": int(df[TARGET].notna().sum())})
        print(f"audit {n}/{len(files)}: {path.name}, {len(df):,} eligible rows", flush=True)
    trips = pd.concat(trip_parts, ignore_index=True).groupby(["vehicle_id", "trip_id"], as_index=False).agg(
        rows=("rows", "sum"), valid_rows=("valid_rows", "sum"), min_ms=("min_ms", "min"), max_ms=("max_ms", "max"), day_num=("day_num", "min"))
    trips["duration_s"] = (trips.max_ms - trips.min_ms) / 1000
    candidate = trips[(trips.duration_s >= cfg["min_trip_duration_s"]) & (trips.valid_rows >= cfg["min_valid_rows_per_trip"]) & (trips.valid_rows / trips.rows >= cfg["min_valid_fraction_per_trip"])].copy()
    counts = candidate.groupby("vehicle_id").size()
    candidate = candidate[candidate.vehicle_id.isin(counts[counts >= cfg["min_trips_per_vehicle"]].index)]
    candidate["rank"] = [stable_rank(cfg["seed"], int(v), int(t)) for v, t in zip(candidate.vehicle_id, candidate.trip_id)]
    selected = candidate.sort_values("rank").groupby("vehicle_id", sort=True).head(cfg["max_trips_per_vehicle"])
    if selected.vehicle_id.nunique() < 10:
        raise ValueError(f"Only {selected.vehicle_id.nunique()} eligible vehicles; inspect audit and explicitly revise scope")
    selected_keys = pd.MultiIndex.from_frame(selected[["vehicle_id", "trip_id"]])
    pieces = []
    for path in files:
        df = pd.read_parquet(interim / (path.stem + ".parquet"))
        chosen = pd.MultiIndex.from_frame(df[["vehicle_id", "trip_id"]]).isin(selected_keys)
        pieces.append(df.loc[chosen])
    chosen = pd.concat(pieces, ignore_index=True)
    chosen = chosen.merge(eligible_meta[["displacement_l", "weight_kg"]], left_on="vehicle_id", right_index=True, validate="many_to_one")
    prepared = []
    duplicates = 0
    for _, frame in chosen.groupby(["vehicle_id", "trip_id"], sort=True):
        if frame.day_num.nunique() != 1:
            raise ValueError("Unexpected different trip start days for the same vehicle/trip key")
        trip = prepare_trip(frame, cfg)
        duplicates += len(frame) - len(trip)
        prepared.append(trip)
    data = pd.concat(prepared, ignore_index=True).sort_values(["vehicle_id", "day_num", "trip_id", "timestamp_ms"])
    vehicles = sorted(map(int, data.vehicle_id.unique()))
    order = np.random.default_rng(cfg["seed"]).permutation(vehicles).tolist()
    n_train, n_val = int(len(order) * 0.70), max(1, int(len(order) * 0.15))
    splits = {"train": sorted(order[:n_train]), "validation": sorted(order[n_train:n_train+n_val]), "test": sorted(order[n_train+n_val:])}
    manifest = {"seed": cfg["seed"], "method": "vehicle_disjoint_70_15_15", "vehicles": splits, "datasets": {}}
    keep = ["vehicle_id", "trip_id", "day_num", "timestamp_ms", "source_time_s", *FEATURES, TARGET, "model_valid", "data_quality", "fuel_provenance", "source_file"]
    for split, ids in splits.items():
        part = data[data.vehicle_id.isin(ids)][keep]
        path = args.out / f"{split}.parquet"
        part.to_parquet(path, index=False, compression="zstd")
        manifest["datasets"][split] = {"file": path.name, "sha256": sha256(path), "rows": len(part), "valid_rows": int(part.model_valid.sum()), "vehicles": len(ids), "trips": int(part.groupby(["vehicle_id", "trip_id"]).ngroups)}
    union = set().union(*map(set, splits.values()))
    assert sum(map(len, splits.values())) == len(union) == len(vehicles)
    selected.drop(columns="rank").sort_values(["vehicle_id", "day_num", "trip_id"]).to_csv(report_dir / "selected_trips.csv", index=False)
    meta.to_csv(report_dir / "vehicle_metadata_audit.csv", index=False)
    audit = {
        "source_repo": "https://github.com/gsoh/VED",
        "source_commit": subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip() if (source / ".git").exists() else "see acquisition manifest",
        "raw_rows": total, "dynamic_vehicle_count": len(vehicle_rows), "vehicle_rows": dict(sorted(vehicle_rows.items())),
        "metadata_vehicle_count": len(meta), "metadata_powertrains": meta["Vehicle Type"].value_counts().to_dict(),
        "powertrain_rows": dict(powertrain_rows), "excluded_metadata": meta[meta.exclusion.ne("")][["VehId", "exclusion"]].to_dict("records"),
        "eligible_metadata_vehicles": len(eligible_meta), "eligible_raw_rows": eligible_total,
        "eligible_target_rows_before_other_filters": target_total, "eligible_reported_fuel_rows": direct_gas_rows,
        "raw_missing_counts": dict(raw_missing), "eligible_missing_counts": dict(eligible_missing),
        "source_columns": raw_columns, "source_files": file_audit,
        "raw_trips_in_eligible_population": len(trips), "candidate_trips": len(candidate), "selected_trips": len(selected),
        "selected_vehicles": len(vehicles), "duplicate_timestamps_removed": duplicates,
        "prepared_quality_counts": data.data_quality.value_counts().to_dict(),
        "source_day_min": float(trips.day_num.min()), "source_day_max": float(trips.day_num.max()),
        "config": cfg, "splits": manifest["datasets"],
        "limitations": ["Derived targets are not calibrated fuel measurements", "No mechanical-fault labels", "No per-signal acquisition ages", "Metadata ICE count includes known diesel/flex-fuel exclusions; other fuel blends are unobserved", "Only complete selected trip records are retained; original publication already removes trip portions for privacy", "Test rows are prepared but not used for model or threshold selection"],
    }
    schema = {"features": FEATURES, "target": TARGET, "units": {"speed_kph": "km/h", "rpm": "rev/min", "load_pct": "% absolute load (may exceed 100)", "acceleration_mps2": "m/s^2", "displacement_l": "L", "weight_kg": "kg, generalized from source lb", TARGET: "estimated L/h"}, "excluded_predictors": ["maf_gps", "stft1_pct", "stft2_pct", "ltft1_pct", "ltft2_pct", "reported_fuel_lph", TARGET, "vehicle_id", "trip_id", "day_num", "source_file"], "target_provenance": cfg, "missing_policy": "Reject missing target/required dynamic features. Training-only median imputation for optional vehicle weight."}
    write_json(args.out / "split_manifest.json", manifest)
    write_json(ROOT / "artifacts/split_manifest.json", manifest)
    write_json(ROOT / "artifacts/feature_schema.json", schema)
    write_json(report_dir / "data_audit.json", audit)
    print(json.dumps(manifest["datasets"], indent=2), flush=True)


if __name__ == "__main__":
    main()
