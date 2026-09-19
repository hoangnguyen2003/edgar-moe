"""Verify a forward-cycle evidence manifest and its referenced files."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import orjson

from edgar_moe.forward.evidence_manifest import EvidenceManifestError, verify_evidence_manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--root", type=Path, default=Path("."))
    args = parser.parse_args()
    try:
        payload = orjson.loads(args.manifest.read_bytes())
        if not isinstance(payload, dict):
            raise EvidenceManifestError("manifest must be a JSON object")
        verify_evidence_manifest(payload, root=args.root)
    except (OSError, TypeError, ValueError) as error:
        print(f"Evidence manifest verification failed: {type(error).__name__}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "status": "verified",
                "manifest": str(args.manifest),
                "manifest_sha256": payload["manifest_sha256"],
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
