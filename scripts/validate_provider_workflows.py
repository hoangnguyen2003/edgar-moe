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
    "provider-partial-write-rehearsal.yml",
)

# Tags such as @v7 are mutable; only a full commit SHA pins the executed code.
_PINNED_ACTION = re.compile(r"^[A-Za-z0-9._-]+/[A-Za-z0-9._/-]+@[0-9a-f]{40}$")
_SECRET_EXPRESSION = re.compile(r"\$\{\{[^}]*\bsecrets\b[^}]*\}\}", re.IGNORECASE | re.DOTALL)


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
        elif name == "provider-partial-write-rehearsal.yml":
            errors.extend(_validate_partial_write(name, workflow))

    return errors


def _validate_partial_write(name: str, workflow: dict[str, Any]) -> list[str]:
    bindings = {
        "SOURCE_DATABASE_URL": "EDGAR_MOE_RESTORE_SOURCE_DATABASE_URL",
        "PROTECTED_R2_BUCKET": "EDGAR_MOE_R2_BUCKET",
        "REHEARSAL_DATABASE_URL": "EDGAR_MOE_REHEARSAL_DATABASE_URL",
        "REHEARSAL_AUDITOR_DATABASE_URL": "EDGAR_MOE_REHEARSAL_AUDITOR_DATABASE_URL",
        **{
            variable: "EDGAR_MOE_" + variable
            for variable in (
                "REHEARSAL_R2_ENDPOINT_URL",
                "REHEARSAL_R2_BUCKET",
                "REHEARSAL_R2_ACCESS_KEY_ID",
                "REHEARSAL_R2_SECRET_ACCESS_KEY",
                "REHEARSAL_R2_AUDITOR_ACCESS_KEY_ID",
                "REHEARSAL_R2_AUDITOR_SECRET_ACCESS_KEY",
            )
        },
    }
    expected = {key: "${{ secrets." + secret + " }}" for key, secret in bindings.items()}
    errors = _validate_step_secret_bindings(
        name,
        workflow,
        "recovery-rehearsal",
        {
            "Exercise isolated failure and verified repair": expected,
        },
    )
    jobs = workflow.get("jobs", {})
    job = jobs.get("recovery-rehearsal", {}) if isinstance(jobs, dict) else {}
    steps = job.get("steps", []) if isinstance(job, dict) else []
    exercise = next(
        (step for step in steps if isinstance(step, dict) and step.get("id") == "rehearsal"), {}
    )
    command = (
        'uv run python -m scripts.rehearse_provider_partial_write --output-root "$REHEARSAL_DIR"'
    )
    if exercise.get("run") != command or exercise.get("continue-on-error"):
        errors.append(
            f"{name}: isolated recovery must use the reviewed helper without ignoring failure"
        )
    if exercise.get("env", {}).get("CONFIRM_ISOLATED_FAILURE") != "${{ inputs.confirm }}":
        errors.append(f"{name}: isolated recovery requires explicit confirmation")
    trigger = _workflow_trigger(workflow)
    dispatch = trigger.get("workflow_dispatch", {}) if isinstance(trigger, dict) else {}
    confirmation = (
        dispatch.get("inputs", {}).get("confirm", {}) if isinstance(dispatch, dict) else {}
    )
    if (
        confirmation.get("options") != ["I_UNDERSTAND_ISOLATED_FAILURE"]
        or confirmation.get("required") is not True
    ):
        errors.append(f"{name}: isolated recovery confirmation must be required and scoped")
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
    """Keep provider secrets out of broad scopes and non-environment inputs."""
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
            and _SECRET_EXPRESSION.search(value)
            and ("env" not in path or "with" in path or "run" in path)
        ):
            errors.append(f"{name}: secret expressions may only appear in job/step env mappings")

    visit(workflow, ())
    if _secret_bindings(workflow.get("env")):
        errors.append(f"{name}: provider secrets must not be workflow-scoped")
    jobs = workflow.get("jobs")
    if isinstance(jobs, dict):
        for job_name, job in jobs.items():
            if isinstance(job, dict) and _secret_bindings(job.get("env")):
                errors.append(f"{name}: provider secrets must not be job-scoped ({job_name})")
    return errors


def _secret_bindings(env: Any) -> dict[str, str]:
    """Return only secret-backed environment bindings, never secret values."""
    if not isinstance(env, dict):
        return {}
    return {
        str(key): value
        for key, value in env.items()
        if isinstance(value, str) and _SECRET_EXPRESSION.search(value)
    }


def _validate_step_secret_bindings(
    name: str,
    workflow: dict[str, Any],
    job_name: str,
    consumers: dict[str, dict[str, str]],
) -> list[str]:
    """Require exactly the reviewed secret set on each audited consumer step."""
    jobs = workflow.get("jobs")
    job = jobs.get(job_name) if isinstance(jobs, dict) else None
    if not isinstance(job, dict):
        return [f"{name}: provider job must be named {job_name}"]
    steps = job.get("steps")
    if not isinstance(steps, list):
        return [f"{name}: provider job must define steps"]
    errors: list[str] = []
    observed: set[str] = set()
    for step in steps:
        if not isinstance(step, dict):
            continue
        step_name = str(step.get("name", ""))
        bindings = _secret_bindings(step.get("env"))
        if step_name in consumers:
            if step_name in observed:
                errors.append(f"{name}: duplicate secret consumer {step_name}")
            observed.add(step_name)
            if bindings != consumers[step_name]:
                errors.append(f"{name}: {step_name} must receive only its reviewed secrets")
        elif bindings:
            errors.append(f"{name}: provider secrets must not enter {step_name or 'unnamed step'}")
    for missing in sorted(consumers.keys() - observed):
        errors.append(f"{name}: missing secret consumer {missing}")
    return errors


def _validate_reader(name: str, workflow: dict[str, Any]) -> list[str]:
    text = _workflow_text(workflow)
    errors: list[str] = []
    secret_name = "EDGAR_MOE_REGISTRY_READ_DATABASE_URL"
    secret_expression = "${{ secrets.EDGAR_MOE_REGISTRY_READ_DATABASE_URL }}"

    def has_secret(env: Any) -> bool:
        return isinstance(env, dict) and (
            secret_name in env
            or any(_SECRET_EXPRESSION.search(str(value)) for value in env.values())
        )

    workflow_env = workflow.get("env")
    if has_secret(workflow_env):
        errors.append(f"{name}: reader audit secret must not be workflow-scoped")
    jobs = workflow.get("jobs")
    job = jobs.get("reader-contract") if isinstance(jobs, dict) else None
    if not isinstance(job, dict):
        errors.append(f"{name}: reader audit job must be named reader-contract")
    else:
        job_env = job.get("env")
        if has_secret(job_env):
            errors.append(f"{name}: reader audit secret must not be job-scoped")
        required_steps = {
            "Require the deployed reader secret",
            "Run the effective reader-role verifier",
        }
        observed_steps: set[str] = set()
        steps = job.get("steps")
        if isinstance(steps, list):
            for step in steps:
                if not isinstance(step, dict):
                    continue
                step_name = str(step.get("name", ""))
                step_env = step.get("env")
                secret_value = step_env.get(secret_name) if isinstance(step_env, dict) else None
                if step_name in required_steps:
                    observed_steps.add(step_name)
                    if secret_value != secret_expression:
                        errors.append(f"{name}: {step_name} must receive the reader secret")
                elif has_secret(step_env):
                    errors.append(
                        f"{name}: reader audit secret must not enter {step_name or 'unnamed step'}"
                    )
                if isinstance(step_env, dict) and any(
                    key != secret_name and _SECRET_EXPRESSION.search(str(value))
                    for key, value in step_env.items()
                ):
                    errors.append(
                        f"{name}: reader audit secret must use its dedicated step variable"
                    )
        for missing_step in sorted(required_steps - observed_steps):
            errors.append(f"{name}: missing reader secret consumer {missing_step}")
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
    preflight_secrets = (
        "EDGAR_MOE_REGISTRY_READ_DATABASE_URL",
        "EDGAR_MOE_REGISTRY_AUDITOR_DATABASE_URL",
        "EDGAR_MOE_R2_ENDPOINT_URL",
        "EDGAR_MOE_R2_BUCKET",
        "EDGAR_MOE_R2_AUDITOR_ACCESS_KEY_ID",
        "EDGAR_MOE_R2_AUDITOR_SECRET_ACCESS_KEY",
        "EDGAR_MOE_RESTORE_SOURCE_DATABASE_URL",
        "EDGAR_MOE_RESTORE_TARGET_DATABASE_URL",
        "EDGAR_MOE_RESTORE_SOURCE_AUDITOR_DATABASE_URL",
        "EDGAR_MOE_RESTORE_TARGET_AUDITOR_DATABASE_URL",
        "EDGAR_MOE_REGISTRY_DATABASE_URL",
        "EDGAR_MOE_R2_ACCESS_KEY_ID",
        "EDGAR_MOE_R2_SECRET_ACCESS_KEY",
    )
    errors.extend(
        _validate_step_secret_bindings(
            name,
            workflow,
            "preflight",
            {
                "Run value-redacting prerequisite check": {
                    secret_name: "${{ secrets." + secret_name + " }}"
                    for secret_name in preflight_secrets
                }
            },
        )
    )
    return errors


def _validate_r2(name: str, workflow: dict[str, Any]) -> list[str]:
    text = _workflow_text(workflow)
    errors: list[str] = []
    credentials = {
        "AUDITOR_DATABASE_URL": "${{ secrets.EDGAR_MOE_REGISTRY_AUDITOR_DATABASE_URL }}",
        "AUDITOR_R2_ENDPOINT_URL": "${{ secrets.EDGAR_MOE_R2_ENDPOINT_URL }}",
        "AUDITOR_R2_BUCKET": "${{ secrets.EDGAR_MOE_R2_BUCKET }}",
        "AUDITOR_R2_ACCESS_KEY_ID": "${{ secrets.EDGAR_MOE_R2_AUDITOR_ACCESS_KEY_ID }}",
        "AUDITOR_R2_SECRET_ACCESS_KEY": "${{ secrets.EDGAR_MOE_R2_AUDITOR_SECRET_ACCESS_KEY }}",
    }
    secret_names = (
        "EDGAR_MOE_REGISTRY_AUDITOR_DATABASE_URL",
        "EDGAR_MOE_R2_ENDPOINT_URL",
        "EDGAR_MOE_R2_BUCKET",
        "EDGAR_MOE_R2_AUDITOR_ACCESS_KEY_ID",
        "EDGAR_MOE_R2_AUDITOR_SECRET_ACCESS_KEY",
    )

    def references_secret(value: Any) -> bool:
        normalized_value = str(value).casefold()
        return any(secret_name.casefold() in normalized_value for secret_name in secret_names)

    def exposes_credentials(value: Any) -> bool:
        return isinstance(value, dict) and any(
            key in credentials or references_secret(item) for key, item in value.items()
        )

    if exposes_credentials(workflow.get("env")):
        errors.append(f"{name}: R2 audit credentials must not be workflow-scoped")
    jobs = workflow.get("jobs")
    job = jobs.get("r2-audit") if isinstance(jobs, dict) else None
    if not isinstance(job, dict):
        errors.append(f"{name}: R2 audit job must be named r2-audit")
    else:
        if exposes_credentials(job.get("env")):
            errors.append(f"{name}: R2 audit credentials must not be job-scoped")
        consumers = {
            "Require read-only provider credentials": credentials,
            "Verify auditor database permissions": {
                "AUDITOR_DATABASE_URL": credentials["AUDITOR_DATABASE_URL"],
            },
            "Run the independent Go auditor": credentials,
        }
        observed: set[str] = set()
        named_steps: dict[str, dict[str, Any]] = {}
        steps = job.get("steps")
        if isinstance(steps, list):
            for step in steps:
                if not isinstance(step, dict):
                    continue
                step_name = str(step.get("name", ""))
                named_steps[step_name] = step
                step_env = step.get("env")
                if step_name in consumers:
                    if step_name in observed:
                        errors.append(f"{name}: duplicate R2 audit credential consumer {step_name}")
                    observed.add(step_name)
                    expected = consumers[step_name]
                    if step_env != expected:
                        errors.append(f"{name}: {step_name} must receive the read-only credentials")
                    if isinstance(step_env, dict) and any(
                        key not in credentials and references_secret(item)
                        for key, item in step_env.items()
                    ):
                        errors.append(
                            f"{name}: R2 audit credentials must use dedicated step variables"
                        )
                elif exposes_credentials(step_env):
                    errors.append(
                        f"{name}: R2 audit credentials must not enter {step_name or 'unnamed step'}"
                    )
        for missing_step in sorted(set(consumers) - observed):
            errors.append(f"{name}: missing R2 audit credential consumer {missing_step}")
        grants = named_steps.get("Verify auditor database permissions")
        audit = named_steps.get("Run the independent Go auditor")
        final = named_steps.get("Fail unless the provider audit passed")
        if (
            not grants
            or grants.get("id") != "grants"
            or (
                'scripts/verify_postgres_auditor.py > "$AUDIT_DIR/database-permissions.json"'
                not in str(grants.get("run", ""))
            )
        ):
            errors.append(f"{name}: auditor permission report must be retained")
        if not audit or audit.get("if") != (
            "${{ always() && steps.secret.outcome == 'success' && steps.grants.outcome == 'success' }}"
        ):
            errors.append(f"{name}: Go audit must require passed auditor permissions")
        if not final or final.get("if") != (
            "${{ always() && (steps.secret.outcome != 'success' || steps.grants.outcome != 'success' "
            "|| steps.audit.outputs.exit_code != '0' || steps.redaction.outcome != 'success') }}"
        ):
            errors.append(f"{name}: final audit gate must reject failed auditor permissions")
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
        "scripts/verify_restore_identities.py",
        "scripts/export_registry_dump.py",
        '--functions-output "$WORK_DIR/registry-functions.sql"',
        "pg_restore --no-owner --no-privileges --exit-on-error",
        "Remove private temporary dump",
    ):
        if required not in text:
            errors.append(f"{name}: missing isolated restore safety marker {required}")
    for forbidden in ("--clean", "DROP DATABASE", "TRUNCATE"):
        if forbidden in text:
            errors.append(f"{name}: destructive restore marker is forbidden: {forbidden}")
    source = "${{ secrets.EDGAR_MOE_RESTORE_SOURCE_DATABASE_URL }}"
    target = "${{ secrets.EDGAR_MOE_RESTORE_TARGET_DATABASE_URL }}"
    source_auditor = "${{ secrets.EDGAR_MOE_RESTORE_SOURCE_AUDITOR_DATABASE_URL }}"
    target_auditor = "${{ secrets.EDGAR_MOE_RESTORE_TARGET_AUDITOR_DATABASE_URL }}"
    r2 = {
        "AUDITOR_R2_ENDPOINT_URL": "${{ secrets.EDGAR_MOE_R2_ENDPOINT_URL }}",
        "AUDITOR_R2_BUCKET": "${{ secrets.EDGAR_MOE_R2_BUCKET }}",
        "AUDITOR_R2_ACCESS_KEY_ID": "${{ secrets.EDGAR_MOE_R2_AUDITOR_ACCESS_KEY_ID }}",
        "AUDITOR_R2_SECRET_ACCESS_KEY": "${{ secrets.EDGAR_MOE_R2_AUDITOR_SECRET_ACCESS_KEY }}",
    }
    consumers = {
        "Validate isolated target and read-only audit credentials": {
            "SOURCE_DATABASE_URL": source,
            "TARGET_DATABASE_URL": target,
            "SOURCE_AUDITOR_DATABASE_URL": source_auditor,
            "TARGET_AUDITOR_DATABASE_URL": target_auditor,
            **r2,
        },
        "Export source registry counts": {"SOURCE_DATABASE_URL": source},
        "Verify source auditor permissions": {"AUDITOR_DATABASE_URL": source_auditor},
        "Verify empty target auditor permissions": {"AUDITOR_DATABASE_URL": target_auditor},
        "Verify restored target reader permissions": {"AUDITOR_DATABASE_URL": target_auditor},
        "Audit source registry and R2 evidence": {"AUDITOR_DATABASE_URL": source_auditor, **r2},
        "Dump source into private temporary storage": {"SOURCE_DATABASE_URL": source},
        "Re-check target emptiness before restore": {"TARGET_DATABASE_URL": target},
        "Restore into the isolated target": {"TARGET_DATABASE_URL": target},
        "Check restored schema": {"EDGAR_MOE_REGISTRY_DATABASE_URL": target},
        "Export restored registry counts": {"TARGET_DATABASE_URL": target},
        "Audit restored registry and R2 evidence": {"AUDITOR_DATABASE_URL": target_auditor, **r2},
        "Probe restored read path": {"EDGAR_MOE_REGISTRY_READ_DATABASE_URL": target_auditor},
    }
    errors.extend(_validate_step_secret_bindings(name, workflow, "restore-rehearsal", consumers))
    jobs = workflow.get("jobs", {})
    job = jobs.get("restore-rehearsal", {}) if isinstance(jobs, dict) else {}
    steps = job.get("steps", []) if isinstance(job, dict) else []
    named = (
        {str(step.get("name", "")): step for step in steps if isinstance(step, dict)}
        if isinstance(steps, list)
        else {}
    )
    preflight = named.get("Validate isolated target and read-only audit credentials", {})
    setup = named.get("Install reviewed PostgreSQL client tools", {})
    setup_index = next((index for index, step in enumerate(steps) if step is setup), -1)
    preflight_index = next((index for index, step in enumerate(steps) if step is preflight), -1)
    if (
        setup.get("run") != "bash scripts/setup_postgres_client.sh"
        or not 0 <= setup_index < preflight_index
    ):
        errors.append(f"{name}: reviewed PostgreSQL client setup must precede credential preflight")
    preflight_run = str(preflight.get("run", ""))
    identity_command = 'uv run python scripts/verify_restore_identities.py --output "$REHEARSAL_DIR/preflight.json"'
    if (
        preflight.get("id") != "preflight"
        or "set -euo pipefail" not in preflight_run
        or preflight_run.strip().splitlines()[-1:] != [identity_command]
        or "inet_server_addr" in text
    ):
        errors.append(f"{name}: restore preflight must fail closed using endpoint identity checks")
    empty_run = str(named.get("Re-check target emptiness before restore", {}).get("run", ""))
    if "n.nspname !~ '^pg_' AND n.nspname <> 'information_schema'" not in empty_run:
        errors.append(f"{name}: target emptiness must cover all non-system schemas")
    schema = named.get("Check restored schema", {})
    if (
        schema.get("id") != "schema"
        or schema.get("run")
        != 'uv run python scripts/verify_restore_schema.py > "$REHEARSAL_DIR/schema-check.json"'
    ):
        errors.append(f"{name}: schema check must fail closed with credential-free diagnostics")
    read_run = str(named.get("Probe restored read path", {}).get("run", ""))
    if (
        "uv run python scripts/probe_restore_read_path.py" not in read_run
        or '--expected-counts "$REHEARSAL_DIR/source-counts.json"' not in read_run
        or '> "$REHEARSAL_DIR/read-path.json"' not in read_run
        or "|| true" in read_run
    ):
        errors.append(
            f"{name}: read probe must verify source-count consistency and retain its result"
        )
    dump_run = str(named.get("Dump source into private temporary storage", {}).get("run", ""))
    restore_run = str(named.get("Restore into the isolated target", {}).get("run", ""))
    if "scripts/export_registry_dump.py" not in dump_run or re.search(r"\bpg_dump\b", dump_run):
        errors.append(f"{name}: restore export must use only the scoped registry exporter")
    functions_marker = '--file="$WORK_DIR/registry-functions.sql"'
    if (
        functions_marker not in restore_run
        or restore_run.find(functions_marker) > restore_run.find("pg_restore --no-owner")
        or 'if [[ "$functions_code" -ne 0 ]]' not in restore_run
    ):
        errors.append(f"{name}: restore must install reviewed trigger functions and fail closed")
    gates = {
        "Verify source auditor permissions": ("source_grants", ("preflight",)),
        "Verify empty target auditor permissions": (
            "empty_target_grants",
            ("preflight", "source_grants"),
        ),
        "Export source registry counts": (
            "source_counts",
            ("preflight", "source_grants", "empty_target_grants"),
        ),
        "Audit source registry and R2 evidence": ("source_audit", ("source_counts",)),
        "Dump source into private temporary storage": ("source_dump", ("source_counts",)),
        "Re-check target emptiness before restore": ("target_empty", ("source_dump",)),
        "Restore into the isolated target": ("restore", ("target_empty",)),
        "Export restored registry counts": ("restored_counts", ("restore",)),
        "Verify restored target reader permissions": ("restored_grants", ("restored_counts",)),
        "Audit restored registry and R2 evidence": (
            "restored_audit",
            ("restored_counts", "restored_grants"),
        ),
        "Probe restored read path": ("read_path", ("restored_counts", "restored_grants")),
    }
    for step_name, (step_id, dependencies) in gates.items():
        step = named.get(step_name, {})
        condition = (
            "${{ "
            + " && ".join(f"steps.{dependency}.outcome == 'success'" for dependency in dependencies)
            + " }}"
        )
        if step.get("id") != step_id or step.get("if") != condition:
            errors.append(f"{name}: {step_name} must preserve the restore permission gates")
    profiles = {
        "Verify source auditor permissions": ("evidence", "source-permissions.json"),
        "Verify empty target auditor permissions": (
            "empty-restore-target",
            "empty-target-permissions.json",
        ),
        "Verify restored target reader permissions": (
            "registry-reader",
            "restored-target-permissions.json",
        ),
    }
    summary = named.get("Write redacted rehearsal summary", {})
    summary_env = summary.get("env", {})
    outcome_loop = re.search(r"for outcome in (.*?); do", str(summary.get("run", "")), re.S)
    for step_name, (profile, report) in profiles.items():
        step = named.get(step_name, {})
        command = (
            f"uv run python scripts/verify_postgres_auditor.py --profile {profile}"
            f' > "$REHEARSAL_DIR/{report}"'
        )
        if step.get("run") != command:
            errors.append(f"{name}: {step_name} must retain the correct permission profile")
        step_id = gates[step_name][0]
        variable = step_id.upper() + "_OUTCOME"
        if (
            not isinstance(summary_env, dict)
            or summary_env.get(variable) != "${{ steps." + step_id + ".outcome }}"
            or outcome_loop is None
            or f'"${variable}"' not in outcome_loop.group(1)
        ):
            errors.append(f"{name}: rehearsal summary must reject failed {step_id}")
    final = named.get("Fail unless the rehearsal passed", {})
    if final.get("if") != (
        "${{ always() && (steps.summary.outputs.overall_status != 'passed' "
        "|| steps.redaction.outcome != 'success') }}"
    ):
        errors.append(f"{name}: final restore gate must require summary and redaction success")
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
