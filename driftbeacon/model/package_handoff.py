"""Build a local portable training archive, with train/validation but no test data."""
from pathlib import Path
import zipfile

from .common import ROOT, read_json, sha256, write_json


def main():
    manifest = read_json(ROOT / "data/prepared/split_manifest.json")
    paths = []
    for folder in ("model", "tests", "config"):
        paths.extend(p for p in (ROOT / folder).rglob("*") if p.is_file() and "__pycache__" not in p.parts)
    paths.extend(ROOT / p for p in ("README.md", "TRAINING_HANDOFF.md", "requirements.txt", "requirements-gpu.txt", "VED_LICENSE.txt", "artifacts/feature_schema.json", "artifacts/split_manifest.json", "reports/data_audit.json", "reports/source_manifest.json", "reports/cpu_baseline_training.json", "data/prepared/split_manifest.json"))
    for split in ("train", "validation"):
        file = ROOT / "data/prepared" / manifest["datasets"][split]["file"]
        if sha256(file) != manifest["datasets"][split]["sha256"]:
            raise ValueError(f"Prepared {split} data changed")
        paths.append(file)
    destination = ROOT / "handoff/driftbeacon-training.zip"
    destination.parent.mkdir(parents=True, exist_ok=True)
    inventory = {str(p.relative_to(ROOT)).replace("\\", "/"): sha256(p) for p in paths}
    write_json(destination.parent / "handoff_manifest.json", {"contents": inventory, "test_data_included": False})
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=1) as archive:
        for path in sorted(paths):
            archive.write(path, "driftbeacon/" + path.relative_to(ROOT).as_posix())
        archive.write(destination.parent / "handoff_manifest.json", "driftbeacon/handoff_manifest.json")
    with zipfile.ZipFile(destination) as archive:
        if archive.testzip() is not None:
            raise RuntimeError("Handoff ZIP integrity check failed")
        if any(name.endswith("test.parquet") for name in archive.namelist()):
            raise RuntimeError("Test data must remain outside the training archive")
    write_json(ROOT / "reports/handoff.json", {"filename": destination.name, "bytes": destination.stat().st_size, "sha256": sha256(destination), "files": len(paths) + 1, "test_data_included": False})
    print(f"Training handoff ready: {destination} ({destination.stat().st_size / 1024**2:.1f} MiB)")


if __name__ == "__main__":
    main()
