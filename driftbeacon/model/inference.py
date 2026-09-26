from __future__ import annotations

from pathlib import Path
import joblib
import numpy as np

from .common import FEATURES, read_json, sha256


class Predictor:
    def __init__(self, directory):
        directory = Path(directory)
        self.metadata = read_json(directory / "metadata.json")
        if self.metadata["features"] != FEATURES:
            raise ValueError("Model feature schema differs from this inference code")
        for name, checksum in self.metadata["model_files"].items():
            if sha256(directory / name) != checksum:
                raise ValueError(f"Model checksum mismatch: {name}")
        self.kind = self.metadata["kind"]
        if self.kind == "xgboost":
            from xgboost import XGBRegressor
            self.model = XGBRegressor()
            self.model.load_model(directory / "model.json")
            self.model.set_params(device="cpu")
            self.preprocessor = joblib.load(directory / "preprocessor.joblib")
        else:
            self.model = joblib.load(directory / "model.joblib")

    def predict_frame(self, frame):
        # Names define order; target, IDs and simulation metadata never enter X.
        x = frame[FEATURES].copy()
        x["weight_kg"] = x.weight_kg.where(x.weight_kg.between(500, 6000))
        required = x.drop(columns="weight_kg")
        good = np.isfinite(required).all(axis=1) & x.speed_kph.between(0, 250) & x.rpm.between(400, 8000) & x.load_pct.between(0, 300) & x.acceleration_mps2.abs().le(8) & x.displacement_l.between(0.5, 10)
        values = np.full(len(frame), np.nan)
        if good.any():
            xx = x.loc[good]
            if self.kind == "xgboost":
                xx = self.preprocessor.transform(xx)
            values[good.to_numpy()] = np.maximum(0, self.model.predict(xx))
        return values
