"""Stage a separate credential-free #290 HTTP service; no provider/deploy operation."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import tomllib
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
EXTRA = {
    "ops/private-read-study/study_app.py": "study_app.py",
    "ops/private-read-study/study_preparation.py": "study_preparation.py",
    "ops/private-read-study/index.py": "index.py",
    "ops/private-read-study/vercel.json": "vercel.json",
}
DIRECT = (
    "certifi",
    "fastapi",
    "sqlalchemy",
    "psycopg",
    "psycopg-binary",
    "pydantic",
    "pydantic-settings",
    "pyyaml",
    "orjson",
)


def requirements(lock: Path) -> bytes:
    document = tomllib.loads(lock.read_text())
    by_name: dict[str, list[dict[str, Any]]] = {}
    for package in document["package"]:
        by_name.setdefault(package["name"], []).append(package)
    selected: dict[str, dict[str, Any]] = {}

    def include(name: str) -> None:
        if name in selected:
            return
        options = by_name.get(name, [])
        if len(options) != 1 or options[0].get("source") != {"registry": "https://pypi.org/simple"}:
            raise ValueError("Dependency must resolve uniquely to the reviewed public registry")
        package = selected[name] = options[0]
        for dependency in package.get("dependencies", []):
            include(dependency["name"])

    for name in DIRECT:
        include(name)
    lines = ["--require-hashes"]
    for name, package in sorted(selected.items()):
        artifacts = list(package.get("wheels", []))
        if package.get("sdist"):
            artifacts.append(package["sdist"])
        hashes = sorted({artifact["hash"] for artifact in artifacts})
        if not hashes or any(not digest.startswith("sha256:") for digest in hashes):
            raise ValueError("All distribution bytes require reviewed SHA-256 pins")
        lines.append(
            f"{name}=={package['version']} " + " ".join(f"--hash={digest}" for digest in hashes)
        )
    return ("\n".join(lines) + "\n").encode()


def stage(destination: Path) -> dict[str, Any]:
    kernel_path = (
        ROOT / "ops/private-read-admission/study-prep-20261027/portable-kernel-v1/build.py"
    )
    spec = importlib.util.spec_from_file_location("reviewed_portable_read_builder", kernel_path)
    if not spec or not spec.loader:
        raise ValueError("Reviewed kernel builder missing")
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    # Resolve new files and dependency closure before the builder creates anything.
    additions = {target: (ROOT / source).read_bytes() for source, target in EXTRA.items()}
    prep_spec = importlib.util.spec_from_file_location(
        "public_study_preparation", ROOT / "ops/private-read-study/study_preparation.py"
    )
    if not prep_spec or not prep_spec.loader:
        raise ValueError("Public synthetic preparation module missing")
    preparation = importlib.util.module_from_spec(prep_spec)
    prep_spec.loader.exec_module(preparation)
    additions["study-preparation.json"] = preparation.synthetic_inputs()
    additions["requirements.txt"] = requirements(ROOT / "uv.lock")
    additions[".python-version"] = b"3.12\n"
    for path in EXTRA:
        if (ROOT / path).is_symlink():
            raise ValueError("Symlink source refused")
    kernel = builder.build(destination)  # New owner-only ignored directory only.
    for target, raw in additions.items():
        builder.write_new(destination / target, raw)
    inventory = [
        {
            "path": str(path.relative_to(destination)),
            "bytes": path.stat().st_size,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
        for path in sorted(destination.rglob("*"))
        if path.is_file()
    ]
    manifest = {
        "scope": "separate_portfolio_study290_source_only",
        "kernel_source_commit": kernel["source_commit"],
        "files": inventory,
        "policy_bound": False,
        "credentials_included": False,
        "provider_requests": 0,
        "deployment_performed": False,
        "old_pilot_extended": False,
    }
    builder.write_new(
        destination / "study-source-manifest.json", json.dumps(manifest, indent=2).encode() + b"\n"
    )
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    report = stage(args.destination.absolute())
    print(
        json.dumps(
            {"files": len(report["files"]), "provider_requests": 0, "deployment_performed": False}
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
