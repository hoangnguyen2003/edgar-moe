"""Validate safety invariants for provider-facing GitHub Actions workflows."""

from __future__ import annotations

import argparse
import re
from pathlib import Path
from typing import Any, cast

import yaml

PROVIDER_WORKFLOWS = (
    "provider-evidence-preflight.yml",
    "provider-reader-contract-audit.yml",
    "provider-r2-evidence-audit.yml",
    "provider-restore-rehearsal.yml",
)

# Tags such as @v7 are mutable; only a full commit SHA pins the executed code.
_PINNED_ACTION = re.compile(r"^[A-Za-z0-9._-]+/[A-Za-z0-9._/-]+@[0-9a-f]{40}$")
_SECRET_EXPRESSION = "${{ secrets."


def validate_provider_workflows(
    root: Path = Path(".github/workflows"),
) -> list[str]:
    """Return provider workflow safety violations without exposing secret values."""
    errors: list[str] = []
    expected = set(PROVIDER_WORKFLOWS)
    actual = {path.name for path in root.glob("provider-*.yml")}
    for unexpected in sorted(actual - expected):
        errors.append(f"unexpected provider workflow requires explicit policy: {unexpected}")

    for name in PROVIDER_WORKFLOWS:
        path = root / name
        if not path.is_file():
            errors.append(f"required provider workflow is missing: {name}")
            continue
        try:
            document = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
            errors.append(f"{name}: workflow is not readable YAML ({type(exc).__name__})")
            continue
        if not isinstance(document, dict):
            errors.append(f"{name}: workflow root must be a mapping")
            continue
        workflow = cast(dict[str, Any], document)
        errors.extend(_validate_common(name, workflow))
        errors.extend(_validate_secrets(name, workflow))
        if name == "provider-evidence-preflight.yml":
            errors.extend(_validate_preflight(name, workflow))
        elif name == "provider-reader-contract-audit.yml":
            errors.extend(_validate_reader(name, workflow))
        elif name == "provider-r2-evidence-audit.yml":
            errors.extend(_validate_r2(name, workflow))
        elif name == "provider-restore-rehearsal.yml":
            errors.extend(_validate_restore(name, workflow))

    return errors


def _validate_common(name: str, workflow: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    trigger = _workflow_trigger(workflow)
    if not isinstance(trigger, dict) or set(trigger) != {"workflow_dispatch"}:
        errors.append(f"{name}: provider workflow must be manual-only workflow_dispatch")

    permissions = workflow.get("permissions")
    if permissions != {"contents": "read"}:
        errors.append(f"{name}: permissions must be exactly contents: read")

    concurrency = workflow.get("concurrency")
    if not isinstance(concurrency, dict) or concurrency.get("cancel-in-progress") is not False:
        errors.append(f"{name}: concurrency must preserve an in-flight evidence run")

    jobs = workflow.get("jobs")
    if not isinstance(jobs, dict) or len(jobs) != 1:
        errors.append(f"{name}: provider workflow must contain exactly one job")
        return errors
    job = next(iter(jobs.values()))
    if not isinstance(job, dict):
        errors.append(f"{name}: provider job must be a mapping")
        return errors
    timeout = job.get("timeout-minutes")
    if not isinstance(timeout, int) or timeout <= 0:
        errors.append(f"{name}: provider job must set a positive timeout-minutes")

    steps = job.get("steps")
    if not isinstance(steps, list) or not steps:
        errors.append(f"{name}: provider job must define steps")
        return errors
    uses = [str(step["uses"]) for step in steps if isinstance(step, dict) and "uses" in step]
    for action in uses:
        if not _PINNED_ACTION.fullmatch(action):
            errors.append(f"{name}: action must be pinned to a full commit SHA (found {action!r})")
    for required_action in ("actions/checkout", "astral-sh/setup-uv"):
        if not any(action.startswith(f"{required_action}@") for action in uses):
            errors.append(f"{name}: missing required action {required_action}")
    upload_indexes = [
        index
        for index, step in enumerate(steps)
        if isinstance(step, dict)
        and str(step.get("uses", "")).startswith("actions/upload-artifact@")
    ]
    if len(upload_indexes) != 1:
        errors.append(f"{name}: provider workflow must have one actions/upload-artifact step")
        return errors
    upload_index = upload_indexes[0]
    upload = steps[upload_index]
    assert isinstance(upload, dict)
    upload_with = upload.get("with")
    if not isinstance(upload_with, dict):
        errors.append(f"{name}: artifact upload must define a with mapping")
    else:
        if upload_with.get("retention-days") != 30:
            errors.append(f"{name}: provider evidence retention must be 30 days")
        if upload_with.get("if-no-files-found") != "error":
            errors.append(f"{name}: provider evidence upload must fail when files are absent")
        if not isinstance(upload_with.get("path"), str):
            errors.append(f"{name}: provider evidence upload path must be explicit")

    text = _workflow_text(workflow)
    redaction_indexes = [
        index
        for index, step in enumerate(steps)
        if isinstance(step, dict)
        and "scripts/validate_redacted_artifacts.py" in str(step.get("run", ""))
    ]
    if len(redaction_indexes) != 1 or redaction_indexes[0] >= upload_index:
        errors.append(f"{name}: redaction validation must run before artifact upload")
    if "steps.redaction.outcome == 'success'" not in text:
        errors.append(f"{name}: artifact upload must require redaction success")
    if "sha256sum" not in text or "SHA256SUMS" not in text:
        errors.append(f"{name}: provider evidence must be hashed before upload")
    if not any(isinstance(step, dict) and "always()" in str(step.get("if", "")) for step in steps):
        errors.append(f"{name}: provider evidence retention must run on failure paths")
    if not any(
        isinstance(step, dict) and str(step.get("name", "")).startswith("Fail unless")
        for step in steps
    ):
        errors.append(f"{name}: workflow must fail after retaining a failed audit")
    return errors


def _validate_secrets(name: str, workflow: dict[str, Any]) -> list[str]:
    """Ensure secret expressions are only assigned through environment mappings."""
    errors: list[str] = []

    def visit(value: Any, path: tuple[str, ...]) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                visit(child, (*path, str(key)))
        elif isinstance(value, list):
            for index, child in enumerate(value):
                visit(child, (*path, str(index)))
        elif (
            isinstance(value, str)
            and _SECRET_EXPRESSION in value
            and ("env" not in path or "with" in path or "run" in path)
        ):
            errors.append(f"{name}: secret expressions may only appear in job/step env mappings")

    visit(workflow, ())
    return errors


def _validate_reader(name: str, workflow: dict[str, Any]) -> list[str]:
    text = _workflow_text(workflow)
    errors: list[str] = []
    for required in (
        "EDGAR_MOE_REGISTRY_READ_DATABASE_URL",
        "scripts/verify_postgres_reader.py",
        "reader-role.error",
    ):
        if required not in text:
            errors.append(f"{name}: missing reader audit contract marker {required}")
    for forbidden in (
        "EDGAR_MOE_REGISTRY_DATABASE_URL",
        "EDGAR_MOE_REGISTRY_WRITER_DATABASE_URL",
    ):
        if forbidden in text:
            errors.append(f"{name}: writer database secret must not enter reader audit")
    return errors


def _validate_preflight(name: str, workflow: dict[str, Any]) -> list[str]:
    text = _workflow_text(workflow)
    errors: list[str] = []
    for required in (
        "scripts/check_provider_evidence_prerequisites.py",
        "REPORT_DIR",
        "SHA256SUMS",
        "scripts/validate_redacted_artifacts.py",
        "provider evidence preflight",
    ):
        if required not in text:
            errors.append(f"{name}: missing prerequisite preflight marker {required}")
    for forbidden in ("--repair", "pg_dump", "pg_restore"):
        if forbidden in text:
            errors.append(
                f"{name}: provider preflight must not execute provider mutations: {forbidden}"
            )
    return errors


def _validate_r2(name: str, workflow: dict[str, Any]) -> list[str]:
    text = _workflow_text(workflow)
    errors: list[str] = []
    for required in (
        "actions/setup-go@",
        "AUDITOR_DATABASE_URL",
        "AUDITOR_R2_ACCESS_KEY_ID",
        "AUDITOR_R2_SECRET_ACCESS_KEY",
        "go run . -timeout 10m -stale-after 96h",
    ):
        if required not in text:
            errors.append(f"{name}: missing read-only R2 audit contract marker {required}")
    for forbidden in (
        "--repair",
        "EDGAR_MOE_REGISTRY_DATABASE_URL",
        "EDGAR_MOE_R2_ACCESS_KEY_ID",
        "EDGAR_MOE_R2_SECRET_ACCESS_KEY",
    ):
        if forbidden in text:
            errors.append(f"{name}: writer or repair path is forbidden in R2 audit")
    return errors


def _validate_restore(name: str, workflow: dict[str, Any]) -> list[str]:
    text = _workflow_text(workflow)
    errors: list[str] = []
    trigger = _workflow_trigger(workflow)
    if isinstance(trigger, dict):
        dispatch = trigger.get("workflow_dispatch")
        if not isinstance(dispatch, dict):
            errors.append(f"{name}: restore workflow must define dispatch inputs")
        else:
            inputs = dispatch.get("inputs")
            confirmation = (
                inputs.get("confirm_isolated_target") if isinstance(inputs, dict) else None
            )
            if not isinstance(confirmation, dict) or confirmation.get("default") != "CANCEL":
                errors.append(f"{name}: restore workflow must default to CANCEL")
            if not isinstance(confirmation, dict) or "I_UNDERSTAND_ISOLATED_TARGET" not in str(
                confirmation.get("options", "")
            ):
                errors.append(f"{name}: restore workflow must require explicit target confirmation")
    for required in (
        "SOURCE_DATABASE_URL",
        "TARGET_DATABASE_URL",
        "SOURCE_AUDITOR_DATABASE_URL",
        "TARGET_AUDITOR_DATABASE_URL",
        "source and isolated target identities are not distinct",
        "isolated target is not empty",
        "pg_dump --format=custom",
        "pg_restore --no-owner --no-privileges --exit-on-error",
        "Remove private temporary dump",
    ):
        if required not in text:
            errors.append(f"{name}: missing isolated restore safety marker {required}")
    for forbidden in ("--clean", "DROP DATABASE", "TRUNCATE"):
        if forbidden in text:
            errors.append(f"{name}: destructive restore marker is forbidden: {forbidden}")
    return errors


def _workflow_text(workflow: dict[str, Any]) -> str:
    """Flatten parsed values for contract-marker checks without reading secret values."""
    return repr(workflow)


def _workflow_trigger(workflow: dict[str, Any]) -> Any:
    """Read the GitHub Actions ``on`` key under YAML 1.1 and 1.2 parsers."""
    trigger = workflow.get("on")
    if trigger is None:
        trigger = workflow.get(cast(Any, True))
    return trigger


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--workflow-root",
        type=Path,
        default=Path(".github/workflows"),
        help="directory containing provider workflow YAML files",
    )
    args = parser.parse_args()
    errors = validate_provider_workflows(args.workflow_root)
    if errors:
        for error in errors:
            print(error)
        return 1
    print(f"provider workflow safety contract passed ({len(PROVIDER_WORKFLOWS)} workflows)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
