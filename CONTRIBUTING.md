# Contributing to EDGAR-MoE

EDGAR-MoE treats the forward registry as research evidence. Changes must
preserve point-in-time behavior, append-only records, and the distinction
between diagnostic observations and the official 20-session evaluation.

## Standard change flow

Every change follows this sequence:

1. Start from the latest `main` and create a descriptive branch. Use one of
   `feat/`, `fix/`, `chore/`, `docs/`, `research/`, `refactor/`, `test/`,
   `security/`, or `ops/`, followed by the concrete task (for example,
   `research/official-outcome-audit`). Dependabot branches are also allowed.
2. Make the smallest coherent change. Update the relevant architecture or
   operations documentation when a boundary, data contract, or failure
   behavior changes.
3. Run the checks relevant to the change. The normal Python gate is:

   ```sh
   uv run ruff check .
   uv run mypy src scripts
   uv run pytest --cov=edgar_moe --cov-report=term-missing --cov-fail-under=80
   ```

   CI treats `uv.lock` as authoritative and runs `uv sync --locked`. When
   dependencies change, update the lockfile deliberately with `uv lock`, then
   include the resulting lockfile diff in the pull request. The web job uses
   `npm ci`, so `apps/web/package-lock.json` must stay synchronized with
   `package.json`.

   Go changes additionally run `gofmt`, `go vet ./...`, and `go test -race ./...`
   from the relevant module. Web changes run the commands in
   `.github/workflows/ci.yml`.

   Workflow changes pin every action to a full commit SHA with its release as
   a trailing comment (`uses: actions/checkout@<sha> # v7.0.1`), and pass
   secrets only to the `run` steps that need them
   ([ADR 0018](docs/adr/0018-workflow-supply-chain.md)). Registry schema
   changes go through Alembic; changing the append-only triggers requires a
   new revision ([ADR 0016](docs/adr/0016-database-append-only-triggers.md)).
4. Commit locally with a clear imperative message, then push the branch to
   `origin`.
5. Open a GitHub pull request. Fill in the PR template, link relevant evidence
   or an issue, and wait for the applicable CI checks.
6. Self-review the PR, resolve conversations, and merge through GitHub. Do not
   push directly to `main` or treat a green local test run as a substitute for
   review.
7. Delete the remote head branch after merge. Start the next task from the
   updated `main`.

For this personal project, the repository can remain private and unprotected
to avoid paid-plan requirements. The pull request, CI checks, template, and
branch policy still provide a useful lightweight review record. See
`docs/repository-governance.md` for the intentionally minimal governance
model.

## Review expectations

Reviewers should ask:

- Does the change preserve pre-entry timestamps and append-only evidence?
- Are external inputs, secrets, and trust boundaries explicit?
- What happens when a source, database, object store, cache, or scheduler fails?
- Is the test evidence proportionate to the risk, and is the rollback/recovery
  path documented?
- Does the change add infrastructure for a measured requirement, or only for
  appearance?

Never place credentials, raw private filing bodies, licensed market data, or
local diagnostic exports in Git. Public assets are deployed artifacts; inspect
their contents as carefully as source code.

## Research and production boundaries

The frozen v1 model is not retrained or replaced by prospective observations.
Model or target changes require a new research protocol, artifact identity, and
reviewed evidence. Operational changes must state whether they affect the
private runner, database, R2 mirror, API, or public bundle.
