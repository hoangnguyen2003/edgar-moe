"""Validate the read-only historical scheduler-lateness workflow."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Any, cast

import yaml

LATENESS_WORKFLOW = "measure-forward-scheduler.yml"
_PINNED_ACTION = re.compile(r"^[A-Za-z0-9._-]+/[A-Za-z0-9._/-]+@[0-9a-f]{40}$")


def validate_scheduler_lateness_workflow(path: Path) -> list[str]:
    """Return contract violations without interpolating tokens or API data."""

    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return ["workflow file is missing"]
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        return [f"workflow is not readable YAML ({type(exc).__name__})"]
    if not isinstance(document, dict):
        return ["workflow root must be a mapping"]

    workflow = cast(dict[str, Any], document)
    errors: list[str] = []
    trigger = workflow.get("on")
    if trigger is None:
        trigger = workflow.get(cast(Any, True))
    if not isinstance(trigger, dict) or set(trigger) != {"workflow_dispatch"}:
        errors.append("workflow must be manual-only workflow_dispatch")
    if workflow.get("permissions") != {"actions": "read", "contents": "read"}:
        errors.append("permissions must be exactly actions: read and contents: read")
    concurrency = workflow.get("concurrency")
    if not isinstance(concurrency, dict) or concurrency.get("cancel-in-progress") is not False:
        errors.append("concurrency must preserve an in-flight measurement")

    jobs = workflow.get("jobs")
    if not isinstance(jobs, dict) or set(jobs) != {"measure"}:
        return [*errors, "workflow must define exactly one measure job"]
    job = jobs.get("measure")
    if not isinstance(job, dict):
        return [*errors, "measure job must be a mapping"]
    job_mapping = cast(dict[str, Any], job)
    if job_mapping.get("runs-on") != "ubuntu-24.04":
        errors.append("measure job must run on ubuntu-24.04")
    if job_mapping.get("timeout-minutes") != 10:
        errors.append("measure job must set timeout-minutes to 10")

    dispatch = trigger.get("workflow_dispatch") if isinstance(trigger, dict) else None
    inputs = dispatch.get("inputs") if isinstance(dispatch, dict) else None
    if not isinstance(inputs, dict):
        errors.append("workflow_dispatch must define lookback_days and limit inputs")
    else:
        for name in ("lookback_days", "limit"):
            value = inputs.get(name)
            if not isinstance(value, dict) or value.get("required") is not True:
                errors.append(f"{name} input must be required")
            elif value.get("type") != "string":
                errors.append(f"{name} input must be a string")

    steps = job_mapping.get("steps")
    if not isinstance(steps, list) or not steps:
        return [*errors, "measure job must define steps"]
    normalized_steps = [cast(dict[str, Any], step) for step in steps if isinstance(step, dict)]
    if len(normalized_steps) != len(steps):
        errors.append("measure job steps must be mappings")
    for step in normalized_steps:
        action = step.get("uses")
        if action is not None and (
            not isinstance(action, str) or not _PINNED_ACTION.fullmatch(action.split(" #", 1)[0])
        ):
            errors.append("measure job actions must be pinned to full commit SHAs")

    text = repr(workflow)
    required_markers = (
        "scripts/measure_scheduler_lateness.py",
        "GITHUB_TOKEN",
        "github.token",
        "scripts/validate_redacted_artifacts.py",
        "sha256sum",
        "SHA256SUMS",
        "reports/scheduler-lateness",
        "Fail unless scheduler lateness",
    )
    for marker in required_markers:
        if marker not in text:
            errors.append(f"workflow is missing marker {marker}")

    uses = [str(step.get("uses", "")) for step in normalized_steps]
    if sum(action.startswith("actions/checkout@") for action in uses) != 1:
        errors.append("workflow must check out the repository exactly once")
    upload_steps = [
        step
        for step in normalized_steps
        if str(step.get("uses", "")).startswith("actions/upload-artifact@")
    ]
    if len(upload_steps) != 1:
        errors.append("workflow must upload exactly one scheduler-lateness artifact")
    else:
        upload_with = upload_steps[0].get("with")
        if not isinstance(upload_with, dict):
            errors.append("scheduler-lateness artifact upload must define inputs")
        else:
            if upload_with.get("retention-days") != 30:
                errors.append("scheduler-lateness artifact retention must be 30 days")
            if upload_with.get("if-no-files-found") != "error":
                errors.append("scheduler-lateness artifact must fail when files are absent")
            if upload_with.get("path") != "reports/scheduler-lateness/":
                errors.append("scheduler-lateness artifact path must be explicit")

    if "secrets." in text:
        errors.append("scheduler-lateness workflow must not receive repository secrets")
    token_step = _step_with_marker(normalized_steps, "GITHUB_TOKEN")
    if "github.token" not in str(token_step):
        errors.append("GitHub token must be assigned from github.token")
    if not any(
        "scripts/validate_redacted_artifacts.py" in str(step.get("run", ""))
        for step in normalized_steps
    ):
        errors.append("redaction validation must run in the scheduler-lateness workflow")
    return errors


def _step_with_marker(steps: list[dict[str, Any]], marker: str) -> dict[str, Any]:
    for step in steps:
        if marker in repr(step):
            return step
    return {}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "workflow",
        nargs="?",
        type=Path,
        default=Path(".github/workflows") / LATENESS_WORKFLOW,
        help="read-only scheduler-lateness workflow to validate",
    )
    args = parser.parse_args()
    errors = validate_scheduler_lateness_workflow(args.workflow)
    if errors:
        print("scheduler lateness workflow contract failed", file=sys.stderr)
        print(f"{len(errors)} validation error(s) detected", file=sys.stderr)
        return 1
    print("scheduler lateness workflow contract passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
