# Repository governance

The intended enterprise-style workflow is **branch → pull request → required CI
→ review → GitHub merge → branch deletion**. Local merges are not the normal path.
This document records the settings that must be enabled in GitHub; repository
administrative settings are external state and are not represented by files alone.

## `main` branch protection

In the repository’s Settings → Branches → Branch protection rules (or Rulesets),
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
list and the rule together. A configured rule is the enforcement mechanism; the
workflow files and this document are not proof that the rule is active.

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

## Current state and gap

The repository contains the PR template, branch-policy workflow, CI checks, and
this documented policy. As of this change, branch protection settings themselves
have not been independently verified through the GitHub account. Enable and test
the rule before claiming that direct pushes are technically blocked.
