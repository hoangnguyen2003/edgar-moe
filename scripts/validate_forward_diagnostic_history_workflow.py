"""Validate the manual forward-diagnostic-history workflow contract."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Any, cast

import yaml

HISTORY_WORKFLOW = "build-forward-diagnostic-history.yml"
_PINNED_ACTION = re.compile(r"^[A-Za-z0-9._-]+/[A-Za-z0-9._/-]+@[0-9a-f]{40}$")


def validate_forward_diagnostic_history_workflow(path: Path) -> list[str]:
    """Return contract violations without interpolating artifact contents."""

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
        errors.append("concurrency must preserve an in-flight history build")

    jobs = workflow.get("jobs")
    if not isinstance(jobs, dict) or set(jobs) != {"history"}:
        return [*errors, "workflow must define exactly one history job"]
    job = jobs.get("history")
    if not isinstance(job, dict):
        return [*errors, "history job must be a mapping"]
    job_mapping = cast(dict[str, Any], job)
    if job_mapping.get("runs-on") != "ubuntu-24.04":
        errors.append("history job must run on ubuntu-24.04")
    if job_mapping.get("timeout-minutes") != 15:
        errors.append("history job must set timeout-minutes to 15")

    dispatch = trigger.get("workflow_dispatch") if isinstance(trigger, dict) else None
    inputs = dispatch.get("inputs") if isinstance(dispatch, dict) else None
    if not isinstance(inputs, dict):
        errors.append("workflow_dispatch must define selection inputs")
    else:
        for name in ("run_ids", "artifact_names", "minimum_reports"):
            value = inputs.get(name)
            if not isinstance(value, dict) or value.get("required") is not True:
                errors.append(f"{name} input must be required")
            elif value.get("type") != "string":
                errors.append(f"{name} input must be a string")

    steps = job_mapping.get("steps")
    if not isinstance(steps, list) or not steps:
        return [*errors, "history job must define steps"]
    normalized_steps = [cast(dict[str, Any], step) for step in steps if isinstance(step, dict)]
    if len(normalized_steps) != len(steps):
        errors.append("history job steps must be mappings")
    for step in normalized_steps:
        action = step.get("uses")
        if action is not None and (
            not isinstance(action, str) or not _PINNED_ACTION.fullmatch(action.split(" #", 1)[0])
        ):
            errors.append("history job actions must be pinned to full commit SHAs")

    text = repr(workflow)
    required_markers = (
        "scripts/validate_forward_diagnostic_history_inputs.py",
        "Prospective forward cycle",
        "gh run view",
        "gh run download",
        "workflowName",
        "conclusion",
        "forward-diagnostic-history",
        "uv run edgar-moe forward-diagnostic-history",
        "forward-diagnostic-history-verify",
        "scripts/validate_redacted_artifacts.py",
        "sha256sum",
        "SHA256SUMS",
        "github.token",
        "GH_TOKEN",
        "reports/forward-diagnostic-history",
        "Fail unless diagnostic history",
    )
    for marker in required_markers:
        if marker not in text:
            errors.append(f"workflow is missing marker {marker}")

    uses = [str(step.get("uses", "")) for step in normalized_steps]
    if sum(action.startswith("actions/checkout@") for action in uses) != 1:
        errors.append("workflow must check out the repository exactly once")
    if sum(action.startswith("astral-sh/setup-uv@") for action in uses) != 1:
        errors.append("workflow must set up uv exactly once")
    upload_steps = [
        step
        for step in normalized_steps
        if str(step.get("uses", "")).startswith("actions/upload-artifact@")
    ]
    if len(upload_steps) != 1:
        errors.append("workflow must upload exactly one diagnostic-history artifact")
    else:
        upload_with = upload_steps[0].get("with")
        if not isinstance(upload_with, dict):
            errors.append("diagnostic-history artifact upload must define inputs")
        else:
            if upload_with.get("retention-days") != 30:
                errors.append("diagnostic-history artifact retention must be 30 days")
            if upload_with.get("if-no-files-found") != "error":
                errors.append("diagnostic-history artifact must fail when files are absent")
            if upload_with.get("path") != "reports/forward-diagnostic-history/":
                errors.append("diagnostic-history artifact path must be explicit")

    if "secrets." in text:
        errors.append("diagnostic-history workflow must not receive repository secrets")
    if "actions: write" in text or "contents: write" in text:
        errors.append("diagnostic-history workflow must not request write permissions")
    upload_text = str(upload_steps[0].get("with", {})).lower() if upload_steps else ""
    if "raw" in upload_text:
        errors.append("diagnostic-history artifact must not upload raw source directories")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "workflow",
        nargs="?",
        type=Path,
        default=Path(".github/workflows") / HISTORY_WORKFLOW,
    )
    args = parser.parse_args()
    errors = validate_forward_diagnostic_history_workflow(args.workflow)
    if errors:
        print("forward diagnostic history workflow contract failed", file=sys.stderr)
        print(f"{len(errors)} validation error(s) detected", file=sys.stderr)
        return 1
    print("forward diagnostic history workflow contract passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
