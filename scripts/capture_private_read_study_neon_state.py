"""Fixed-endpoint Neon metadata capture; no SQL or database credentials.

The metadata step's project API key is withheld from the separate HTTP probe.
Only an allowlisted state receipt is written, never the provider response.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any


class Refusal(ValueError):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str
    ) -> None:
        return None


def capture(env: dict[str, str]) -> dict[str, Any]:
    try:
        key = env["STUDY_NEON_METADATA_KEY"]
        project = env["STUDY_NEON_PROJECT_ID"]
        endpoint = env["STUDY_NEON_ENDPOINT_ID"]
        if (
            not 20 <= len(key) <= 512
            or any(c.isspace() for c in key)
            or not re.fullmatch(r"[a-z0-9-]{8,80}", project)
            or not re.fullmatch(r"ep-[a-z0-9-]{5,80}", endpoint)
        ):
            raise ValueError()
    except Exception:
        raise Refusal("neon_metadata_configuration_refused") from None
    request = urllib.request.Request(
        "https://console.neon.tech/api/v2/projects/" + project + "/endpoints/" + endpoint,
        headers={"Authorization": "Bearer " + key, "Accept": "application/json"},
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    began = datetime.now(UTC)
    try:
        with opener.open(request, timeout=10) as response:
            raw = response.read(65_537)
            if len(raw) > 65_536:
                raise Refusal("neon_metadata_size_refused")
            document = json.loads(raw)
        item = document["endpoint"]
        if (
            item["id"] != endpoint
            or item["project_id"] != project
            or item["current_state"] not in {"idle", "active"}
            or item.get("pending_state") is not None
            or item["disabled"] is not False
        ):
            raise Refusal("neon_metadata_identity_or_transition_refused")
    except urllib.error.HTTPError as error:
        error.close()
        raise Refusal("neon_metadata_api_refused") from None
    except Refusal:
        raise
    except Exception:
        raise Refusal("neon_metadata_contract_or_transport_refused") from None
    return {
        "schema_version": 1,
        "scope": "portfolio_study290_neon_endpoint_state",
        "status": "passed",
        "capture_started_at": began.isoformat(),
        "observed_at": datetime.now(UTC).isoformat(),
        "project_sha256": hashlib.sha256(project.encode()).hexdigest(),
        "endpoint_sha256": hashlib.sha256(endpoint.encode()).hexdigest(),
        "current_state": item["current_state"],
        "pending_state": None,
        "disabled": False,
        "database_connections": 0,
        "automatic_retries": 0,
        "provider_payload_retained": False,
    }


def annotate(
    observation: dict[str, Any], before: dict[str, Any], after: dict[str, Any]
) -> dict[str, Any]:
    """Bound the transition to the pair, without claiming a per-request API timestamp."""
    try:
        start = datetime.fromisoformat(observation["observed_at"])
        pre = datetime.fromisoformat(before["observed_at"])
        post = datetime.fromisoformat(after["capture_started_at"])
        end = datetime.now(UTC)
        after_observed = datetime.fromisoformat(after["observed_at"])
        if (
            before["scope"] != "portfolio_study290_neon_endpoint_state"
            or before["scope"] != after["scope"]
            or before["status"] != "passed"
            or after["status"] != "passed"
            or before["endpoint_sha256"] != after["endpoint_sha256"]
            or before["project_sha256"] != after["project_sha256"]
            or not pre <= start <= post <= after_observed <= end
            or any(t.utcoffset() != timedelta(0) for t in (pre, start, post, after_observed))
            or any(
                not re.fullmatch(r"[a-f0-9]{64}", item[key])
                for item in (before, after)
                for key in ("project_sha256", "endpoint_sha256")
            )
            or any(item["current_state"] not in {"idle", "active"} for item in (before, after))
            or (post - pre).total_seconds() > 300
        ):
            raise ValueError()
        cohort = "unobserved"
        if observation["status"] == "passed" and after["current_state"] == "active":
            if before["current_state"] == "idle":
                cohort = "provider_idle_to_active_across_read_pair"
            elif before["current_state"] == "active":
                cohort = "provider_active_before_and_after_read_pair"
        fields = (
            "scope",
            "status",
            "capture_started_at",
            "observed_at",
            "project_sha256",
            "endpoint_sha256",
            "current_state",
        )
        observation["database_lifecycle"] = {
            "status": "passed" if cohort != "unobserved" else "unobserved",
            "cohort": cohort,
            "before": {key: before[key] for key in fields},
            "after": {key: after[key] for key in fields},
            "scope": "pair_level_provider_state_not_per_request_restart_timestamp",
        }
        # The first successful native read follows the idle preflight. Preserve
        # the pair-level evidence label; never invent a timestamp for the resume.
        if cohort == "provider_idle_to_active_across_read_pair":
            observation["samples"][0]["database_resume"] = cohort
        return observation
    except Exception:
        raise Refusal("neon_metadata_correlation_refused") from None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--annotate", type=Path)
    parser.add_argument("--before", type=Path)
    parser.add_argument("--after", type=Path)
    args = parser.parse_args()
    os.umask(0o077)
    try:
        if args.annotate:
            if not args.before or not args.after:
                raise Refusal("neon_metadata_files_missing")
            report = annotate(
                json.loads(args.annotate.read_text()),
                json.loads(args.before.read_text()),
                json.loads(args.after.read_text()),
            )
        else:
            report = capture(dict(os.environ))
    except Exception as error:
        report = {
            "schema_version": 1,
            "scope": "portfolio_study290_neon_refusal",
            "status": "refused",
            "reason": str(error) if isinstance(error, Refusal) else "neon_metadata_failed",
            "database_connections": 0,
            "provider_payload_retained": False,
        }
    args.output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with args.output.open("x") as stream:
        json.dump(report, stream, indent=2)
        stream.write("\n")
    print(json.dumps({"status": report["status"], "values_withheld": True}))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
