"""Validate the safety contract of the manual Cloudflare deploy workflow."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Any, cast

import yaml

WORKFLOW_NAME = "deploy-forward-scheduler.yml"
WRANGLER_ACTION = "cloudflare/wrangler-action@9acf94ace14e7dc412b076f2c5c20b8ce93c79cd"
_PINNED_ACTION = re.compile(r"^[A-Za-z0-9._-]+/[A-Za-z0-9._/-]+@[0-9a-f]{40}$")
_SECRET_EXPRESSION = "${{ secrets."


def validate_scheduler_workflow(path: Path) -> list[str]:
    """Return workflow violations without interpolating any secret value."""
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return [f"workflow file is missing: {path}"]
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        return [f"workflow is not readable YAML ({type(exc).__name__})"]
    if not isinstance(document, dict):
        return ["workflow root must be a mapping"]

    workflow = cast(dict[str, Any], document)
    errors: list[str] = []
    errors.extend(_validate_top_level(workflow))
    errors.extend(_validate_secret_locations(workflow))
    jobs = workflow.get("jobs")
    if not isinstance(jobs, dict) or set(jobs) != {"validate", "deploy"}:
        return [*errors, "workflow must define exactly validate and deploy jobs"]
    validate_job = jobs.get("validate")
    deploy_job = jobs.get("deploy")
    if not isinstance(validate_job, dict) or not isinstance(deploy_job, dict):
        return [*errors, "validate and deploy jobs must be mappings"]
    errors.extend(_validate_validate_job(cast(dict[str, Any], validate_job)))
    errors.extend(_validate_deploy_job(cast(dict[str, Any], deploy_job)))
    return errors


def _validate_top_level(workflow: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    trigger = workflow.get("on")
    if trigger is None:
        trigger = workflow.get(cast(Any, True))
    if not isinstance(trigger, dict) or set(trigger) != {"workflow_dispatch"}:
        errors.append("workflow must be manual-only workflow_dispatch")
    if workflow.get("permissions") != {"contents": "read"}:
        errors.append("permissions must be exactly contents: read")
    concurrency = workflow.get("concurrency")
    if not isinstance(concurrency, dict) or concurrency.get("cancel-in-progress") is not False:
        errors.append("concurrency must preserve an in-flight deployment")
    return errors


def _validate_secret_locations(workflow: dict[str, Any]) -> list[str]:
    errors: list[str] = []

    def visit(value: Any, path: tuple[str, ...]) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                visit(child, (*path, str(key)))
        elif isinstance(value, list):
            for index, child in enumerate(value):
                visit(child, (*path, str(index)))
        elif isinstance(value, str) and _SECRET_EXPRESSION in value:
            if "env" not in path or "with" in path or "run" in path:
                errors.append("secret expressions may only appear in step env mappings")
            if "GITHUB_TOKEN" in value:
                errors.append("the deploy workflow must not receive a GitHub token")

    visit(workflow, ())
    return errors


def _validate_steps(job: dict[str, Any], label: str) -> tuple[list[str], list[dict[str, Any]]]:
    errors: list[str] = []
    steps = job.get("steps")
    if not isinstance(steps, list) or not steps:
        return [f"{label} job must define steps"], []
    normalized = [cast(dict[str, Any], step) for step in steps if isinstance(step, dict)]
    if len(normalized) != len(steps):
        errors.append(f"{label} job steps must be mappings")
    for step in normalized:
        action = step.get("uses")
        if action is not None and (
            not isinstance(action, str) or not _PINNED_ACTION.fullmatch(action.split(" #", 1)[0])
        ):
            errors.append(f"{label} job action must be pinned to a full commit SHA")
    return errors, normalized


def _validate_validate_job(job: dict[str, Any]) -> list[str]:
    errors, steps = _validate_steps(job, "validate")
    if job.get("timeout-minutes") != 10:
        errors.append("validate job must set timeout-minutes to 10")
    text = repr(job)
    for marker in ("scripts/validate_scheduler_config.py", "cloudflare-forward-scheduler.test.mjs"):
        if marker not in text:
            errors.append(f"validate job is missing {marker}")
    if not any(str(step.get("uses", "")).startswith("actions/checkout@") for step in steps):
        errors.append("validate job must check out the repository")
    if not any(str(step.get("uses", "")).startswith("actions/setup-node@") for step in steps):
        errors.append("validate job must pin setup-node")
    return errors


def _validate_deploy_job(job: dict[str, Any]) -> list[str]:
    errors, steps = _validate_steps(job, "deploy")
    if job.get("if") != "${{ inputs.deploy == 'DEPLOY' }}":
        errors.append("deploy job must require the explicit DEPLOY input")
    if job.get("needs") != "validate":
        errors.append("deploy job must depend on validate")
    if job.get("timeout-minutes") != 10:
        errors.append("deploy job must set timeout-minutes to 10")
    environment = job.get("environment")
    if not isinstance(environment, dict) or environment.get("name") != "scheduler":
        errors.append("deploy job must use the scheduler environment")

    wrangler_steps = [
        step for step in steps if step.get("uses", "").split(" #", 1)[0] == WRANGLER_ACTION
    ]
    if len(wrangler_steps) != 1:
        errors.append("deploy job must use the pinned Wrangler action exactly once")
        return errors
    with_values = wrangler_steps[0].get("with")
    if not isinstance(with_values, dict):
        return [*errors, "Wrangler action must define inputs"]
    expected = {
        "apiToken": "${{ env.CLOUDFLARE_API_TOKEN }}",
        "accountId": "${{ env.CLOUDFLARE_ACCOUNT_ID }}",
        "workingDirectory": "ops/scheduler",
        "wranglerVersion": "4.136.2",
        "command": "deploy --config wrangler.toml.example",
        "quiet": True,
    }
    for key, value in expected.items():
        if with_values.get(key) != value:
            errors.append(f"Wrangler input {key!r} must be {value!r}")
    env = wrangler_steps[0].get("env")
    if not isinstance(env, dict) or set(env) != {"CLOUDFLARE_API_TOKEN", "CLOUDFLARE_ACCOUNT_ID"}:
        errors.append("Wrangler step must receive only the two Cloudflare secrets")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "workflow",
        nargs="?",
        type=Path,
        default=Path(".github/workflows") / WORKFLOW_NAME,
        help="manual scheduler deployment workflow to validate",
    )
    args = parser.parse_args()
    errors = validate_scheduler_workflow(args.workflow)
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 1
    print("scheduler deployment workflow contract passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
