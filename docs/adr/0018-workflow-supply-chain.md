# ADR 0018: Pin workflow actions and scope secrets to steps

- Status: accepted
- Date: 2026-09-21
- Deciders: project maintainer

## Context

Every workflow referenced actions, including the third-party
`astral-sh/setup-uv`, by mutable major tags such as `@v7`. The scheduled forward
cycle exposed the registry writer URL, R2 write keys, data-source keys, and the
alert webhook in job-level `env`, so every step received them, including
third-party actions. A repointed tag would therefore run with production
credentials. The provider-workflow validator required `@vN` tags, which rejected
commit-SHA pins, and its hard-coded `@v7` literals would fail CI on every
Dependabot actions update.

## Decision

- Pin every `uses:` to a full 40-character commit SHA, followed by the exact
  release as a comment (`# v7.0.1`), which Dependabot updates together.
- In the forward cycle, keep only non-secret configuration at job level. Each
  secret is passed only to the first-party `run` steps that need it. Optional
  integrations are gated on outputs of the configuration-validation step,
  because `if:` cannot read secrets.
- The provider-workflow validator requires commit-SHA pins and matches actions
  by name, not version.

## Consequences

- Retagging an upstream action cannot change the code that runs, and no
  third-party action receives production credentials.
- Action upgrades arrive as reviewed Dependabot pull requests that change the
  SHA and version comment together.

## Verification

A repository-wide test asserts that every workflow step is SHA-pinned with a
version comment, and that the forward cycle keeps secrets out of job-level env
and out of every `uses` step. Provider-workflow policy tests reject a mutable
tag.
