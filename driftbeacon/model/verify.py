"""Verify prepared-data integrity without evaluating test predictions."""
import argparse
import json

import numpy as np
import pandas as pd

from .common import FEATURES, ROOT, TARGET, read_json, sha256, write_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--training-only", action="store_true", help="For handoff archives, which intentionally omit test data")
    args = parser.parse_args()
    base = ROOT / "data/prepared"
    manifest = read_json(base / "split_manifest.json")
    groups = {name: set(ids) for name, ids in manifest["vehicles"].items()}
    assert not (groups["train"] & groups["validation"] or groups["train"] & groups["test"] or groups["validation"] & groups["test"]), "Vehicle split overlap"
    reports = {}
    for split, meta in manifest["datasets"].items():
        if args.training_only and split == "test":
            continue
        path = base / meta["file"]
        assert sha256(path) == meta["sha256"], f"Checksum mismatch: {split}"
        frame = pd.read_parquet(path)
        assert len(frame) == meta["rows"]
        assert int(frame.model_valid.sum()) == meta["valid_rows"]
        assert set(map(int, frame.vehicle_id.unique())) == groups[split]
        assert not frame.duplicated(["vehicle_id", "trip_id", "timestamp_ms"]).any()
        assert (frame.groupby(["vehicle_id", "trip_id"]).source_time_s.diff().dropna() > 0).all()
        usable = frame[frame.model_valid]
        required = usable[[f for f in FEATURES if f != "weight_kg"] + [TARGET]]
        assert np.isfinite(required.to_numpy()).all()
        assert usable[TARGET].ge(0).all()
        assert not set(["maf_gps", "stft1_pct", "ltft1_pct", "reported_fuel_lph"]) & set(FEATURES)
        assert (frame.groupby(["vehicle_id", "trip_id"]).first().model_valid == False).all(), "Trips should start without an acceleration history"
        reports[split] = {"rows": len(frame), "valid_rows": len(usable), "vehicles": len(groups[split]), "checksum_verified": True, "time_order_verified": True}
    result = {"passed": True, "scope": "Data integrity only; no final-test predictions or model selection", "splits": reports}
    write_json(ROOT / "reports/data_verification.json", result)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
