"""Stage an allowlisted, credential-free pilot bundle in a NEW directory.

Never link or deploy from the repository root (which belongs to the public app).
The deploy command is deliberately separate and requires verified provider access,
free-plan evidence and a separately named project before secrets are provisioned.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path

FILES = {
    "ops/private-read-pilot/index.py": "index.py",
    "ops/private-read-pilot/vercel.json": "vercel.json",
    "ops/private-read-pilot/.vercelignore": ".vercelignore",
    "ops/private-read-pilot/requirements.txt": "requirements.txt",
    "ops/private-read-pilot/.python-version": ".python-version",
    "src/edgar_moe/__init__.py": "edgar_moe/__init__.py",
    "src/edgar_moe/api/private_read.py": "edgar_moe/api/private_read.py",
    "src/edgar_moe/forward/reader_role.py": "edgar_moe/forward/reader_role.py",
    "src/edgar_moe/forward/auditor_role.py": "edgar_moe/forward/auditor_role.py",
    "src/edgar_moe/forward/endpoint.py": "edgar_moe/forward/endpoint.py",
}


def stage(root: Path, destination: Path) -> dict[str, object]:
    destination.mkdir(mode=0o700, parents=False, exist_ok=False)
    hashes: dict[str, str] = {}
    for source, target in FILES.items():
        source_path = root / source
        if source_path.is_symlink():
            raise ValueError("symlink_source_refused")
        target_path = destination / target
        target_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        shutil.copyfile(source_path, target_path)
        hashes[target] = hashlib.sha256(target_path.read_bytes()).hexdigest()
    # Explicit namespace markers; do not import the public API or all registry code.
    for name in (
        "edgar_moe/api/__init__.py",
        "edgar_moe/forward/__init__.py",
    ):
        (destination / name).write_text("", encoding="utf-8")
        hashes[name] = hashlib.sha256(b"").hexdigest()
    report: dict[str, object] = {"schema_version": 1, "files": hashes, "secrets_included": False}
    with (destination / "bundle-manifest.json").open("x", encoding="utf-8") as stream:
        json.dump(report, stream, sort_keys=True, indent=2)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args()
    report = stage(Path(__file__).resolve().parents[1], args.destination)
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
