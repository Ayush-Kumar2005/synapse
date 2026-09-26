"""Download immutable official VED sources and extract the two dynamic archives."""
from pathlib import Path
import hashlib
import subprocess
import urllib.parse
import urllib.request

from .common import ROOT, read_json, sha256, write_json

COMMIT = "6baa4963782d515a67d32a5490bd5d11f5d9bf0d"
FILES = ["Data/VED_DynamicData_Part1.7z", "Data/VED_DynamicData_Part2.7z", "Data/VED_Static_Data_ICE&HEV.xlsx", "LICENSE", "README.md"]


def main():
    base = ROOT / "data/raw/ved-source"
    if (base / ".git").exists():
        head = subprocess.check_output(["git", "-C", str(base), "rev-parse", "HEAD"], text=True).strip()
        if head != COMMIT:
            raise ValueError("Raw source checkout is not the pinned VED revision")
    prior = ROOT / "reports/source_manifest.json"
    # Git on Windows may use CRLF in text checkouts; raw HTTP downloads use LF.
    # Older manifests did not normalize those two documentation files.
    checksums = {item["file"]: item["sha256"] for item in read_json(prior)["files"] if item["file"].startswith("Data/") or item.get("checksum_format") == "lf_normalized"} if prior.exists() else {}
    destination = ROOT / "data/raw/dynamic"
    destination.mkdir(parents=True, exist_ok=True)
    sources = []
    for relative in FILES:
        path = base / relative
        url = f"https://raw.githubusercontent.com/gsoh/VED/{COMMIT}/{urllib.parse.quote(relative)}"
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix(path.suffix + ".partial")
            print(f"Downloading {relative}", flush=True)
            urllib.request.urlretrieve(url, temporary)
            temporary.replace(path)
        text_file = relative in {"README.md", "LICENSE"}
        digest = hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest() if text_file else sha256(path)
        if relative in checksums and checksums[relative] != digest:
            raise ValueError(f"Source checksum changed: {relative}")
        sources.append({"file": relative, "url": url, "bytes": path.stat().st_size, "sha256": digest, "checksum_format": "lf_normalized" if text_file else "binary"})
        if path.suffix == ".7z":
            import py7zr
            with py7zr.SevenZipFile(path) as archive:
                members = archive.getnames()
                if any(Path(name).name != name or not name.endswith("_week.csv") for name in members):
                    raise ValueError("Unexpected archive member path")
                if not all((destination / name).exists() for name in members):
                    print(f"Extracting {path.name}", flush=True)
                    archive.extractall(destination)
    write_json(ROOT / "reports/source_manifest.json", {"repo": "https://github.com/gsoh/VED", "commit": COMMIT, "files": sources})
    (ROOT / "VED_LICENSE.txt").write_bytes((base / "LICENSE").read_bytes())
    print(f"Source manifest written; {len(list(destination.glob('*.csv')))} weekly files present")


if __name__ == "__main__":
    main()
