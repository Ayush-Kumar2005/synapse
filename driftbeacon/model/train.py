"""Fit a CPU sanity model or an optional CUDA XGBoost candidate; never read test."""
from __future__ import annotations

import argparse
from importlib.metadata import version
import json
from pathlib import Path
import platform
import time

import joblib
import numpy as np
import pandas as pd
from sklearn.dummy import DummyRegressor
from sklearn.ensemble import RandomForestRegressor
from sklearn.impute import SimpleImputer
from sklearn.linear_model import Ridge
from sklearn.metrics import mean_absolute_error, mean_squared_error
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from threadpoolctl import threadpool_limits

from .common import FEATURES, ROOT, TARGET, read_json, sha256, stable_rank, write_json


def metrics(y, prediction):
    return {"mae_lph": float(mean_absolute_error(y, prediction)), "rmse_lph": float(np.sqrt(mean_squared_error(y, prediction))), "bias_lph": float(np.mean(prediction - y))}


def bounded_training_trips(data, max_rows, seed):
    if max_rows == 0 or len(data) <= max_rows:
        return data
    groups = data.groupby(["vehicle_id", "trip_id"], sort=False).indices
    chosen, rows = [], 0
    for key in sorted(groups, key=lambda k: stable_rank(seed, *k)):
        indexes = groups[key]
        if rows + len(indexes) > max_rows and rows:
            continue
        chosen.append(indexes)
        rows += len(indexes)
    return data.iloc[np.concatenate(chosen)].copy()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--data", type=Path, default=ROOT / "data/prepared")
    p.add_argument("--output", type=Path, default=ROOT / "artifacts/models/cpu_baseline")
    p.add_argument("--algorithm", choices=["random_forest", "xgboost"], default="random_forest")
    p.add_argument("--device", choices=["cpu", "cuda"], default="cpu")
    p.add_argument("--max-train-rows", type=int, default=120000, help="0 uses every prepared training row")
    p.add_argument("--jobs", type=int, default=4)
    args = p.parse_args()
    if args.output.exists():
        raise FileExistsError("Choose a new output directory to preserve previous model and detector versions")
    if args.device == "cuda" and args.algorithm != "xgboost":
        p.error("CUDA applies to XGBoost; scikit-learn RandomForest uses CPU")
    manifest = read_json(args.data / "split_manifest.json")
    frames = {}
    for split in ("train", "validation"):
        path = args.data / manifest["datasets"][split]["file"]
        if sha256(path) != manifest["datasets"][split]["sha256"]:
            raise ValueError(f"{split} checksum does not match the frozen split")
        df = pd.read_parquet(path)
        if set(map(int, df.vehicle_id.unique())) != set(manifest["vehicles"][split]):
            raise ValueError(f"Unexpected vehicle IDs in {split}")
        frames[split] = df[df.model_valid].reset_index(drop=True)
    if set(frames["train"].vehicle_id) & set(frames["validation"].vehicle_id):
        raise ValueError("Vehicle leakage")
    train = bounded_training_trips(frames["train"], args.max_train_rows, manifest["seed"])
    val = frames["validation"]
    x, y, vx, vy = train[FEATURES], train[TARGET], val[FEATURES], val[TARGET]
    if x.drop(columns="weight_kg").isna().any().any() or y.isna().any():
        raise ValueError("Invalid prepared training rows")
    candidates = {
        "median": make_pipeline(SimpleImputer(strategy="median", keep_empty_features=True), DummyRegressor(strategy="median")),
        "ridge": make_pipeline(SimpleImputer(strategy="median", keep_empty_features=True), StandardScaler(), Ridge(alpha=10)),
    }
    if args.algorithm == "random_forest":
        candidates["random_forest"] = make_pipeline(SimpleImputer(strategy="median", keep_empty_features=True), RandomForestRegressor(n_estimators=64, max_depth=16, min_samples_leaf=20, n_jobs=args.jobs, random_state=manifest["seed"]))
    else:
        from xgboost import XGBRegressor
        if args.device == "cuda":
            import subprocess
            # A graphics card alone does not prove CUDA support.
            subprocess.run(["nvidia-smi"], check=True)
        candidates["xgboost"] = make_pipeline(SimpleImputer(strategy="median", keep_empty_features=True), XGBRegressor(n_estimators=400, max_depth=6, learning_rate=0.05, subsample=0.9, colsample_bytree=1, tree_method="hist", device=args.device, n_jobs=args.jobs, random_state=manifest["seed"], objective="reg:squarederror"))
    scores = {}
    with threadpool_limits(limits=4):
        for name, model in candidates.items():
            start = time.perf_counter()
            model.fit(x, y)
            pred = np.maximum(0, model.predict(vx))
            scores[name] = {**metrics(vy, pred), "fit_and_validation_seconds": time.perf_counter() - start}
            if name == "xgboost" and args.device == "cuda":
                actual_device = json.loads(model[-1].get_booster().save_config())["learner"]["generic_param"]["device"]
                if not actual_device.startswith("cuda"):
                    raise RuntimeError("XGBoost fell back to CPU; GPU training was not performed")
            print(name, json.dumps(scores[name]), flush=True)
    winner = min(scores, key=lambda name: scores[name]["mae_lph"])
    selected = candidates[winner]
    args.output.mkdir(parents=True)
    model_kind = "sklearn"
    if winner == "xgboost":
        # Save portable trees separately; explicitly switch inference to CPU.
        selected[-1].set_params(device="cpu")
        selected[-1].save_model(args.output / "model.json")
        joblib.dump(selected[0], args.output / "preprocessor.joblib")
        model_kind = "xgboost"
    else:
        joblib.dump(selected, args.output / "model.joblib")
    info = {
        "selected": winner, "kind": model_kind, "requested_candidate": args.algorithm,
        "training_device": args.device if winner == "xgboost" else "cpu",
        "features": FEATURES, "target": TARGET, "seed": manifest["seed"],
        "training_rows": len(train), "training_vehicles": int(train.vehicle_id.nunique()),
        "training_trips": int(train.groupby(["vehicle_id", "trip_id"]).ngroups),
        "validation_rows": len(val), "validation_vehicles": int(val.vehicle_id.nunique()),
        "validation_scores": scores, "test_evaluated": False,
        "prediction_policy": "clip negative predictions to zero; reject invalid required sensors",
        "feature_schema_sha256": sha256(ROOT / "artifacts/feature_schema.json"),
        "prepared_checksums": {s: v["sha256"] for s, v in manifest["datasets"].items()},
        "versions": {lib: version(lib) for lib in ["numpy", "pandas", "scikit-learn", "joblib"] + (["xgboost"] if args.algorithm == "xgboost" else [])},
        "python": platform.python_version(),
        "model_files": {p.name: sha256(p) for p in args.output.iterdir() if p.is_file()},
        "scope": "CPU baseline is a sanity check. Error is against a derived fuel target, not measured fuel or labelled mechanical faults.",
    }
    write_json(args.output / "metadata.json", info)
    from .inference import Predictor
    loaded = Predictor(args.output)
    expected = np.maximum(0, selected.predict(vx.iloc[:100]))
    np.testing.assert_allclose(loaded.predict_frame(val.iloc[:100]), expected, rtol=1e-6, atol=1e-6)
    start = time.perf_counter()
    loaded.predict_frame(val.iloc[:min(1000, len(val))])
    info["cpu_reload_verified"] = True
    info["cpu_prediction_batch_ms"] = (time.perf_counter() - start) * 1000
    write_json(args.output / "metadata.json", info)
    write_json(ROOT / "reports" / (args.output.name + "_training.json"), info)
    print(f"Selected {winner}; saved to {args.output}; CPU reload verified; test untouched", flush=True)


if __name__ == "__main__":
    main()
