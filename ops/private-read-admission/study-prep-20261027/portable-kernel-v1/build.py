"""Build a new private kernel bundle from reviewed Git objects and fixed SQL.

No environment/credential input, database, provider request or deployment. The
output contains read-service source, SQL and dependency pins, never a target,
password, baseline, private row or admission state. It is not a complete app.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import subprocess
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[4]
SOURCE_COMMIT = "3077310593bdaa1f4ac0087d207d565936c73a6f"
DEPENDENCY_LOCK_SHA256 = "e407c689756c2a177889a00620e26316324382b50a82920b0378baff1162a8eb"
SQL_FILES = {
    "session-original.sql": "2d7bb825031790145501d91c0f2bf60a962420a1f15f6d7149c1456fc52e7b43",
    "schema.sql": "f2643484254218d3674792e623899d457a6ebfd649f909ad99cda57a5912f777",
    "fixture.sql": "4cd64e0ff82393d7abb27e80fe63d6a3d574e68c2216c89449d9b52eb5a14e42",
    "sql-provenance.json": "85e87aa8ec14e8e929a9234c477acc80b20340021b7da587a41a3f2f6bd5235c",
}
REQUIREMENTS = """sqlalchemy==2.1.1
orjson==3.12.0
pydantic==2.13.5
pydantic-settings==2.15.0
pyyaml==6.0.3
psycopg[binary]==3.3.4
"""


def raw_digest(raw):
    return hashlib.sha256(raw).hexdigest()


def git_source(path):
    if (
        (path != "src/edgar_moe.py" and not path.startswith("src/edgar_moe/"))
        or not path.endswith(".py")
        or ".." in PurePosixPath(path).parts
    ):
        raise ValueError("reviewed_python_source_required")
    result = subprocess.run(
        ["git", "show", SOURCE_COMMIT + ":" + path],
        cwd=ROOT,
        capture_output=True,
        timeout=5,
    )
    if result.returncode:
        return None
    if len(result.stdout) > 200000:
        raise ValueError("bounded_reviewed_source_required")
    return result.stdout


def application_sources():
    result, visiting = {}, set()

    def include(module, required=True):
        if module in visiting:
            return
        visiting.add(module)
        prefix = "src/" + module.replace(".", "/")
        path, raw = prefix + ".py", git_source(prefix + ".py")
        if raw is None:
            path, raw = prefix + "/__init__.py", git_source(prefix + "/__init__.py")
        if raw is None:
            if required:
                raise ValueError("reviewed_module_missing")
            return
        result[path] = raw
        parts = module.split(".")
        for size in range(1, len(parts)):
            # A source directory such as utils is a valid namespace package;
            # do not invent an __init__ file when the reviewed tree has none.
            include(".".join(parts[:size]), required=False)
        for node in ast.walk(ast.parse(raw)):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "edgar_moe" or alias.name.startswith("edgar_moe."):
                        include(alias.name)
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    raise ValueError("unreviewed_relative_import_requires_review")
                if node.module and (
                    node.module == "edgar_moe" or node.module.startswith("edgar_moe.")
                ):
                    include(node.module)
                    for alias in node.names:
                        if alias.name != "*":
                            include(node.module + "." + alias.name, required=False)

    include("edgar_moe.forward.registry")
    include("edgar_moe.api.models")
    return result


def sql_contract():
    values = {}
    for path, expected in SQL_FILES.items():
        raw = Path(__file__).with_name(path).read_bytes()
        if raw_digest(raw) != expected:
            raise ValueError("fixed_sql_source_changed")
        values[path] = raw.decode()
    provenance = json.loads(values["sql-provenance.json"])
    sql = {
        "session": values["session-original.sql"],
        "fixture": values["fixture.sql"],
        "schema": values["schema.sql"],
    }
    # Match the exact original generation bytes, including key order. The
    # provenance records generation inputs; no original JS module is executed.
    generated = json.dumps(sql, separators=(",", ":"), ensure_ascii=False).encode()
    if (
        raw_digest(generated) != provenance["source_sha256"]
        or sql["session"].count("<= 10485760)") != 1
    ):
        raise ValueError("fixed_sql_contract_refused")
    original = sql["session"]
    sql.update(
        session=original.replace("<= 10485760)", f"<= {192 * 1024 * 1024})"),
        original_session=original,
        source_sha256=raw_digest(generated),
    )
    return sql


def write_new(path, raw):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with os.fdopen(
        os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600), "wb"
    ) as stream:
        stream.write(raw)


def build(output):
    output = output.absolute()
    if (
        output.resolve() != output
        or not output.is_relative_to(ROOT / "data/artifacts")
        or output.exists()
    ):
        raise ValueError("new_private_bundle_directory_required")
    # Resolve and verify all sources before creating an output tree.
    lock = Path(__file__).with_name("requirements.lock").read_bytes()
    if raw_digest(lock) != DEPENDENCY_LOCK_SHA256:
        raise ValueError("fixed_dependency_lock_changed")
    sql = sql_contract()
    sources = application_sources()
    kernel = Path(__file__).with_name("snapshot_kernel.py").read_bytes()
    ast.parse(kernel)
    os.umask(0o077)
    output.mkdir(parents=True, mode=0o700)
    for name, raw in sources.items():
        write_new(output / name.removeprefix("src/"), raw)
    write_new(output / "snapshot_kernel.py", kernel)
    write_new(output / "contract.json", json.dumps(sql, sort_keys=True, indent=2).encode() + b"\n")
    write_new(output / "requirements.in", REQUIREMENTS.encode())
    write_new(output / "requirements.lock", lock)
    manifest = {
        "scope": "portable_read_kernel_source_only_not_a_deployment",
        "source_commit": SOURCE_COMMIT,
        "application_source_files": len(sources),
        "fixed_sql_files": SQL_FILES,
        "sql_generation_sources": json.loads(
            Path(__file__).with_name("sql-provenance.json").read_bytes()
        )["generation_sources"],
        "dependency_lock_sha256": DEPENDENCY_LOCK_SHA256,
        "new_target_bound": False,
        "allocation_authority_configured": False,
        "http_handler_included": False,
        "provider_requests": 0,
        "source_use_permission_granted": False,
        "files": [
            {
                "path": str(path.relative_to(output)),
                "bytes": path.stat().st_size,
                "sha256": raw_digest(path.read_bytes()),
            }
            for path in sorted(output.rglob("*"))
            if path.is_file()
        ],
    }
    write_new(
        output / "source-manifest.json",
        json.dumps(manifest, sort_keys=True, indent=2).encode() + b"\n",
    )
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    manifest = build(args.output)
    print(
        json.dumps(
            {
                "scope": manifest["scope"],
                "application_source_files": manifest["application_source_files"],
                "provider_requests": 0,
            }
        )
    )
