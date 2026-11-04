"""Compare configured targets to the prior private reference without connections.

A matching reference is not reuse of #288's one-cycle authorization and does
not release the general source/workflow gate. Values never enter the receipt.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

REFERENCE_SHA256 = "0a7cf03033bc7f551a1313f259f086cc57a9c952b2466e8214adef677e6e7e5d"
REQUIRED = (
    "ALPACA_API_KEY",
    "ALPACA_API_SECRET",
    "FRED_API_KEY",
    "SEC_USER_AGENT",
    "EDGAR_MOE_REGISTRY_DATABASE_URL",
    "EDGAR_MOE_R2_ENDPOINT_URL",
    "EDGAR_MOE_R2_BUCKET",
    "EDGAR_MOE_R2_ACCESS_KEY_ID",
    "EDGAR_MOE_R2_SECRET_ACCESS_KEY",
)


def compare_targets(environment: dict[str, str], expected: dict[str, str]) -> dict[str, Any]:
    present = {name: bool(environment.get(name, "").strip()) for name in REQUIRED}
    hashes: dict[str, str] = {}
    invalid = False
    try:
        database = urlsplit(environment.get("EDGAR_MOE_REGISTRY_DATABASE_URL", ""))
        storage = urlsplit(environment.get("EDGAR_MOE_R2_ENDPOINT_URL", ""))
        bucket = environment.get("EDGAR_MOE_R2_BUCKET", "")
        if (
            database.scheme not in {"postgresql", "postgresql+psycopg", "postgres"}
            or not database.hostname
            or not database.path.strip("/")
        ):
            invalid = True
        else:
            target = f"{database.hostname}:{database.port or 5432}{database.path}"
            hashes["registry"] = hashlib.sha256(target.encode()).hexdigest()
        if storage.scheme != "https" or not storage.hostname or not bucket:
            invalid = True
        else:
            hashes["storage"] = hashlib.sha256(f"{storage.hostname}/{bucket}".encode()).hexdigest()
    except ValueError:
        invalid = True
    matches = {
        name: bool(hashes.get(name) and hashes[name] == expected.get(name))
        for name in ("registry", "storage")
    }
    status = (
        "missing_configuration"
        if not all(present.values())
        else "invalid_targets"
        if invalid
        else "matches_private_reference"
        if all(matches.values())
        else "different_targets"
    )
    return {
        "schema_version": 1,
        "status": status,
        "required_configuration_present": present,
        "target_sha256": hashes,
        "matches_private_reference": matches,
        "reference_scope_sha256": REFERENCE_SHA256,
        "reference_authorization_reused": False,
        "provider_execution_authorized": False,
        "provider_requests": 0,
        "database_connections": 0,
        "storage_connections": 0,
        "external_writes": 0,
        "secret_values_retained": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    reference = root / "ops/issue288/private-verification-scope.json"
    if hashlib.sha256(reference.read_bytes()).hexdigest() != REFERENCE_SHA256:
        raise ValueError("Private reference identity changed")
    expected = json.loads(reference.read_text())["private_target_fingerprints"]
    report = compare_targets(dict(os.environ), expected)
    os.umask(0o077)
    args.output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with args.output.open("x") as stream:
        json.dump(report, stream, indent=2)
        stream.write("\n")
    print(
        json.dumps(
            {
                "status": report["status"],
                "database_connections": 0,
                "storage_connections": 0,
                "provider_requests": 0,
            }
        )
    )
    return 0 if report["status"] == "matches_private_reference" else 1


if __name__ == "__main__":
    raise SystemExit(main())
