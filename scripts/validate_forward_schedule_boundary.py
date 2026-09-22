"""Validate the live forward workflow's explicit rollback schedule boundary.

The Cloudflare scheduler is intentionally dormant until a separate reviewed
cutover. This contract keeps the existing GitHub schedule and manual dispatch
available together, so an accidental workflow edit cannot silently remove the
rollback trigger or change the point-in-time filing window.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, cast

import yaml

WORKFLOW = Path(".github/workflows/forward-production.yml")
EXPECTED_CRON = "17 7 * * 2-6"
EXPECTED_CONCURRENCY = "edgar-moe-forward-production"


def validate_forward_schedule_boundary(path: Path = WORKFLOW) -> list[str]:
    """Return violations of the pre-cutover GitHub scheduler contract."""

    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return ["forward workflow is missing"]
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        return [f"forward workflow is not readable YAML ({type(exc).__name__})"]
    if not isinstance(document, dict):
        return ["forward workflow root must be a mapping"]

    workflow = cast(dict[str, Any], document)
    errors: list[str] = []
    trigger = workflow.get("on")
    if trigger is None:
        trigger = workflow.get(cast(Any, True))
    if not isinstance(trigger, dict) or set(trigger) != {"schedule", "workflow_dispatch"}:
        errors.append("forward workflow must retain schedule and workflow_dispatch triggers")
    else:
        schedule = trigger.get("schedule")
        if schedule != [{"cron": EXPECTED_CRON}]:
            errors.append(f"schedule must contain only cron {EXPECTED_CRON!r}")
        dispatch = trigger.get("workflow_dispatch")
        errors.extend(_validate_dispatch_inputs(dispatch))

    if workflow.get("permissions") != {"contents": "read"}:
        errors.append("forward workflow permissions must remain contents: read")
    concurrency = workflow.get("concurrency")
    if not isinstance(concurrency, dict) or concurrency.get("group") != EXPECTED_CONCURRENCY:
        errors.append("forward workflow concurrency group must remain stable")
    if not isinstance(concurrency, dict) or concurrency.get("cancel-in-progress") is not False:
        errors.append("forward workflow must preserve an in-flight run")

    jobs = workflow.get("jobs")
    if not isinstance(jobs, dict) or "forecast-and-settle" not in jobs:
        errors.append("forward workflow must define forecast-and-settle")
    return errors


def _validate_dispatch_inputs(dispatch: Any) -> list[str]:
    if not isinstance(dispatch, dict):
        return ["workflow_dispatch must remain configured"]
    inputs = dispatch.get("inputs")
    if not isinstance(inputs, dict) or set(inputs) != {"cutoff", "device"}:
        return ["workflow_dispatch must retain cutoff and device inputs"]
    errors: list[str] = []
    cutoff = inputs.get("cutoff")
    if not isinstance(cutoff, dict) or cutoff.get("required") is not False:
        errors.append("cutoff input must remain optional")
    if not isinstance(cutoff, dict) or cutoff.get("type") != "string":
        errors.append("cutoff input must remain a string")

    device = inputs.get("device")
    if not isinstance(device, dict):
        return [*errors, "device input must remain a mapping"]
    if device.get("required") is not True:
        errors.append("device input must remain required")
    if device.get("type") != "choice" or device.get("options") != ["cpu"]:
        errors.append("device input must remain the cpu-only choice")
    if device.get("default") != "cpu":
        errors.append("device input default must remain cpu")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workflow", nargs="?", type=Path, default=WORKFLOW)
    args = parser.parse_args()
    errors = validate_forward_schedule_boundary(args.workflow)
    if errors:
        print("forward schedule boundary contract failed", file=sys.stderr)
        print(f"{len(errors)} validation error(s) detected", file=sys.stderr)
        return 1
    print("forward schedule boundary contract passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
