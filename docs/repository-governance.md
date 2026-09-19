# Repository governance

The intended lightweight workflow is **branch → pull request → CI →
self-review → GitHub merge → branch deletion**. Local merges are not the normal
path. This keeps the project private and avoids paying for controls that are
disproportionate to a personal repository.

This document records the settings and expectations that must be enabled in
GitHub; repository administrative settings are external state and are not
represented by files alone.

## Optional `main` branch protection

If the project later gains collaborators or a stronger audit requirement, the
repository’s Settings → Branches → Branch protection rules (or Rulesets) can
protect `main` with required pull requests, reviews, conversation resolution,
up-to-date branches, and the applicable CI checks. This is an optional future
control, not a prerequisite for the current personal-project workflow.

## Pull requests

`.github/pull_request_template.md` requires a summary, scope, validation, risk,
rollback, architecture impact, and research-integrity notes.
`.github/workflows/pr-policy.yml` rejects branches without a descriptive
approved prefix. The approved prefixes are `feat/`, `fix/`, `chore/`, `docs/`,
`research/`, `refactor/`, `test/`, `security/`, and `ops/`. Dependabot is
allowed to use its standard branch prefix.

After a successful merge, delete the head branch and start new work from the
updated `main`. Keep the PR description and CI results as the durable review
record.

## Security and release boundaries

- Secrets belong only in GitHub/Vercel/Neon/R2 secret stores and must not appear
  in PR text, logs, artifacts, or browser bundles.
- Workflow changes are privileged changes because they can access source-system,
  database, and object-store credentials. Require review for `.github/`, `ops/`,
  `config/`, and deployment files.
- A green PR proves repository checks passed; it does not prove provider quotas,
  database grants, backups, scheduler delivery, or R2 retention. Those require
  separate operational verification.
- Keep the Vercel/public bundle and private forward runner as separate
  deployment boundaries. The browser must never receive database or R2
  credentials.

## Current personal-project mode

Branch protection is intentionally not enabled: the repository is personal,
private, and cost-sensitive. The practical controls are an open PR, green CI,
the branch-policy check, a short self-review using the template, and a deliberate
commitment not to push directly to `main`. If collaborators or regulated
evidence are introduced later, revisit paid protection or an organization plan.
