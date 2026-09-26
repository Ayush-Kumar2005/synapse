"""Acquire, audit, train and serve an experimental EngineFaultDB classifier.

The published CSV has no vehicle/run/time identifiers. Source-order holdouts
with purged boundaries are an exploratory benchmark, not independent engines.
"""
from __future__ import annotations

import argparse
from importlib.metadata import version
from pathlib import Path
import urllib.request

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import accuracy_score, balanced_accuracy_score, classification_report, confusion_matrix, f1_score
from threadpoolctl import threadpool_limits

from .common import ROOT, read_json, sha256, write_json

REVISION = "c63f23ba048761d9c2a2c95d8fb4c0392a3c63d3"
CSV_SHA256 = "70021a35b2b2efb294e49f4c78b80a391a05414d44d4564c429645eb32260763"
SOURCE = "https://github.com/leoxthomas/EngineFaultDB"
PAPER = "https://doi.org/10.1109/ACCESS.2023.3331316"
RAW = ROOT / "data/raw/enginefaultdb"
DEFAULT_MODEL = ROOT / "artifacts/models/enginefault_candidate"
RENAME = {"RPM": "rpm", "Speed": "speed_kph", "Consumption L/H": "measured_fuel_lph",
          "CO": "co_pct", "HC": "hc_ppm", "CO2": "co2_pct", "O2": "o2_pct", "Lambda": "lambda"}
FEATURES = list(RENAME.values())
# The paper names rich mixture, lean mixture and low voltage, but the text
# available to us does not verify the numeric mapping in its Table 4.
LABELS = {"0": "No fault in laboratory experiment", "1": "Fault type 1",
          "2": "Fault type 2", "3": "Fault type 3"}
LIMITATION = ("Single C14NE engine; no run IDs or timestamps. Source-row blocks are not verified "
              "independent recordings. Scores do not establish cross-vehicle diagnosis or a cause of VED drift.")


def acquire():
    RAW.mkdir(parents=True, exist_ok=True)
    for name in ("EngineFaultDB_Final.csv", "LICENSE", "README.md"):
        path = RAW / name
        if not path.exists():
            url = f"https://raw.githubusercontent.com/leoxthomas/EngineFaultDB/{REVISION}/{name}"
            with urllib.request.urlopen(url, timeout=60) as response:
                path.write_bytes(response.read())
    if sha256(RAW / "EngineFaultDB_Final.csv") != CSV_SHA256:
        raise ValueError("EngineFaultDB checksum differs from pinned source")


def blocked_split(frame, purge=100):
    """Hold out later source-row blocks of each label; no random row mixing."""
    if purge < 1:
        raise ValueError("A positive purge is required")
    assigned = pd.Series("purged", index=frame.index, dtype=object)
    for _, part in frame.groupby("Fault", sort=True):
        n = len(part)
        a, b = int(n * .6), int(n * .8)
        if min(a, b - a, n - b) <= 2 * purge:
            raise ValueError("Insufficient rows for purged class blocks")
        assigned.loc[part.index[:a - purge]] = "train"
        assigned.loc[part.index[a + purge:b - purge]] = "validation"
        assigned.loc[part.index[b + purge:]] = "test"
    return assigned


def scores(y, predicted):
    return {"accuracy": float(accuracy_score(y, predicted)),
            "balanced_accuracy": float(balanced_accuracy_score(y, predicted)),
            "macro_f1": float(f1_score(y, predicted, average="macro")),
            "confusion_matrix_labels": [0, 1, 2, 3],
            "confusion_matrix": confusion_matrix(y, predicted, labels=[0, 1, 2, 3]).tolist(),
            "per_class": classification_report(y, predicted, labels=[0, 1, 2, 3], output_dict=True, zero_division=0)}


def train(output=DEFAULT_MODEL):
    from xgboost import XGBClassifier
    output = Path(output)
    if output.exists():
        raise FileExistsError("Preserve the previous model; choose a new --output")
    acquire()
    raw = pd.read_csv(RAW / "EngineFaultDB_Final.csv")
    if set(raw.Fault.unique()) != {0, 1, 2, 3} or not np.isfinite(raw.to_numpy()).all():
        raise ValueError("Unexpected labels or nonfinite source data")
    # Remove identical predictor rows globally, including inconsistent labels.
    # This avoids identical inputs appearing in both training and held-out rows.
    duplicate_mask = raw.duplicated(subset=list(RENAME), keep=False)
    clean = raw.loc[~duplicate_mask].copy()
    split = blocked_split(clean)
    data = clean.rename(columns=RENAME)
    x, y = data[FEATURES], data.Fault
    train_mask, val_mask, test_mask = [split.eq(s) for s in ("train", "validation", "test")]
    candidates = {
        "random_forest": RandomForestClassifier(n_estimators=128, max_depth=12, min_samples_leaf=8,
                                                class_weight="balanced", n_jobs=1, random_state=20260926),
        "xgboost": XGBClassifier(n_estimators=250, max_depth=4, learning_rate=.05,
                                 subsample=.85, colsample_bytree=.9, reg_lambda=5,
                                 tree_method="hist", device="cpu", n_jobs=1, random_state=20260926),
    }
    validation = {}
    with threadpool_limits(limits=1):
        for name, model in candidates.items():
            model.fit(x.loc[train_mask], y.loc[train_mask])
            validation[name] = scores(y.loc[val_mask], model.predict(x.loc[val_mask]))
            print(name, "validation macro F1", validation[name]["macro_f1"], flush=True)
    selected = max(validation, key=lambda k: validation[k]["macro_f1"])
    model = candidates[selected]
    # Selection is frozen before opening this source-block holdout.
    test_scores = scores(y.loc[test_mask], model.predict(x.loc[test_mask]))
    output.mkdir(parents=True)
    model_file = "classifier.json" if selected == "xgboost" else "classifier.joblib"
    if selected == "xgboost":
        model.save_model(output / model_file)
    else:
        joblib.dump(model, output / model_file)
    bounds = {c: [float(x.loc[train_mask, c].min()), float(x.loc[train_mask, c].max())] for c in FEATURES}
    audit = {"source": SOURCE, "revision": REVISION, "csv_sha256": CSV_SHA256,
             "source_rows": len(raw), "rows_removed_as_duplicate_predictors": int(duplicate_mask.sum()),
             "source_columns": list(raw), "source_label_counts": {str(k): int(v) for k, v in raw.Fault.value_counts().items()},
             "no_nox_column": True, "missing_values": int(raw.isna().sum().sum()),
             "split_counts": {str(k): int(v) for k, v in split.value_counts().items()},
             "split_policy": "Per-label source order 60/20/20%; purge 100 rows on both sides of each boundary. No run independence claim.",
             "limitations": LIMITATION}
    split_record = pd.DataFrame({"source_row": clean.index, "fault_label": clean.Fault, "split": split})
    split_record.to_csv(output / "source_row_splits.csv", index=False)
    metadata = {"kind": selected, "features": FEATURES, "labels": LABELS, "label_mapping_verified": False,
                "fault_families_in_paper": ["rich mixture", "lean mixture", "low voltage"],
                "excluded_source_features": [c for c in raw if c not in RENAME and c != "Fault"],
                "input_domain": "EngineFaultDB laboratory measurements; external sensors require domain validation",
                "required_fuel_provenance": "measured; VED MAF/trim-derived fuel is not interchangeable",
                "min_score": .65, "min_margin": .15, "probabilities_calibrated": False,
                "bounds": bounds, "model_file": model_file, "model_sha256": sha256(output / model_file),
                "versions": {k: version(k) for k in ("scikit-learn", "xgboost", "numpy", "joblib")},
                "audit": audit, "validation": validation, "exploratory_test": test_scores,
                "paper": PAPER, "component_diagnosis_validated": False}
    write_json(output / "metadata.json", metadata)
    write_json(ROOT / "reports/enginefault_training.json", metadata)
    print("Selected", selected, "exploratory test", test_scores["accuracy"], flush=True)
    return metadata


class FaultPredictor:
    def __init__(self, directory=DEFAULT_MODEL):
        directory = Path(directory)
        self.metadata = read_json(directory / "metadata.json")
        filename = self.metadata["model_file"]
        if filename not in {"classifier.json", "classifier.joblib"}:
            raise ValueError("Unsupported model filename")
        if self.metadata["features"] != FEATURES or sha256(directory / filename) != self.metadata["model_sha256"]:
            raise ValueError("Fault model schema or checksum mismatch")
        if self.metadata["kind"] == "xgboost":
            from xgboost import XGBClassifier
            self.model = XGBClassifier()
            self.model.load_model(directory / filename)
            self.model.set_params(device="cpu", n_jobs=1)
        else:
            self.model = joblib.load(directory / filename)

    def predict(self, values, domain):
        missing = [k for k in FEATURES if k not in values or values[k] is None or not np.isfinite(values[k])]
        result = {"status": "unavailable", "missing_features": missing, "confirmed_component": None,
                  "label_mapping_verified": False, "probabilities_calibrated": False,
                  "domain": domain, "limitations": LIMITATION}
        if missing:
            return result
        outside = [k for k in FEATURES if not self.metadata["bounds"][k][0] <= values[k] <= self.metadata["bounds"][k][1]]
        if outside:
            return {**result, "status": "outside_training_ranges", "out_of_range_features": outside}
        if domain != "enginefaultdb_lab":
            return {**result, "status": "unsupported_sensor_domain",
                    "reason": "Classifier is validated only on source laboratory blocks; field transfer is unvalidated."}
        x = pd.DataFrame([{k: values[k] for k in FEATURES}])
        probabilities = self.model.predict_proba(x)[0]
        order = np.argsort(probabilities)[::-1]
        top = int(order[0])
        label = str(int(self.model.classes_[top]))
        certain = (probabilities[top] >= self.metadata["min_score"] and
                   probabilities[top] - probabilities[order[1]] >= self.metadata["min_margin"])
        return {**result, "status": "experimental_classification" if certain else "uncertain",
                "top_label": label, "top_label_name": LABELS[label],
                "class_scores": {str(int(c)): float(p) for c, p in zip(self.model.classes_, probabilities)},
                "reason": "Class similarity is not a confirmed failed component or a cause of fuel drift."}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--acquire-only", action="store_true")
    p.add_argument("--output", type=Path, default=DEFAULT_MODEL)
    args = p.parse_args()
    if args.acquire_only:
        acquire()
    else:
        train(args.output)


if __name__ == "__main__":
    main()
