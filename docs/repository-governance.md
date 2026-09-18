# Repository governance

The intended lightweight workflow is **branch → pull request → CI → self-review
→ GitHub merge → branch deletion**. Local merges are not the normal path. This
keeps the project private and avoids paying for controls that are disproportionate
to a personal repository.
This document records the settings that must be enabled in GitHub; repository
administrative settings are external state and are not represented by files alone.

## Optional `main` branch protection

If the project later gains collaborators or a stronger audit requirement, the
repository’s Settings → Branches → Branch protection rules (or Rulesets) can
protect `main` with:

- Require a pull request before merging.
- Require at least one approving review from someone other than the author.
- Dismiss stale approvals when new commits are pushed.
- Require conversation resolution before merging.
- Require branches to be up to date before merging.
- Require these status checks: `CI / python`, `CI / web`, `CI / evidence-auditor`,
  and `PR policy / branch-name`.
- Require the branch to pass the checks before merge; do not allow force pushes
  or branch deletion by contributors.
- Allow administrators to bypass only for documented incidents, followed by an
  issue or post-incident record. Prefer disabling administrator bypass if the
  repository’s account tier permits it.

The exact check labels can change if workflow or job names change. Update this
list and the rule together. This is an optional future control, not a prerequisite
for the current personal-project workflow.

## Pull requests

`.github/pull_request_template.md` requires a summary, scope, validation, risk,
rollback, architecture impact, and evidence notes. `.github/workflows/pr-policy.yml`
rejects branches that do not use a descriptive approved prefix. Dependabot is
allowed to use its standard branch prefix.

Use the repository’s normal merge strategy consistently. After a successful merge,
delete the head branch and start new work from the updated `main`. Keep the PR
description and CI results as the durable review record.

## Security and release boundaries

- Secrets belong only in GitHub/Vercel/Neon/R2 secret stores and must not appear in
  PR text, logs, artifacts, or browser bundles.
- Workflow changes are privileged changes because they can access source-system,
  database, and object-store credentials. Require review for `.github/`, `ops/`,
  `config/`, and deployment files.
- A green PR proves repository checks passed; it does not prove provider quotas,
  database grants, backups, scheduler delivery, or R2 retention. Those require
  separate operational verification.
- Keep the Vercel/public bundle and private forward runner as separate deployment
  boundaries. The browser must never receive database or R2 credentials.

## Current personal-project mode

The repository contains the PR template, branch-policy workflow, and CI checks.
Branch protection is intentionally not enabled: the repository is personal,
private, and cost-sensitive. The practical controls are an open PR, green CI,
the branch-policy check, a short self-review using the template, and a deliberate
commitment not to push directly to `main`. If collaborators or regulated evidence
are introduced later, revisit paid protection or an organization plan.
