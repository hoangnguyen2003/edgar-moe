# ADR 0023: Stage an external forward scheduler without changing research timing

- Status: accepted (adapter only; cutover requires a separate operational PR)
- Date: 2026-09-22
- Deciders: project maintainer

## Context

The forward cycle is scheduled at `07:17 UTC` so its source cutoff remains after
the prior SEC acceptance window. GitHub Actions schedule events are best-effort;
observed starts can be several hours late, leaving little pre-open margin. Moving
the cron earlier changes same-day pre-market filing coverage and therefore changes
the research design. The application already records an immutable
`pre_open_schedule_margin` quality check, but that is observability rather than a
start-time guarantee.

## Decision

Add an optional Cloudflare Workers Cron adapter that dispatches the existing
`forward-production.yml` workflow through GitHub's API. The adapter:

- has no public trigger endpoint;
- stores only a narrowly scoped GitHub token in the Worker secret store;
- validates repository, workflow, ref, and device configuration before egress;
- checks for an active run and skips a duplicate dispatch;
- sends no cutoff, source payload, model, database, or object-store credential;
- fails closed without logging response bodies or secrets; and
- remains dormant until a separate reviewed cutover removes the GitHub schedule.

The source cutoff, `forecast_as_of < entry_at` invariant, frozen v1 artifact, and
prospective evaluation policy remain unchanged.

## Alternatives considered

- Keep GitHub schedule only: no new credential boundary, but the known latency
  risk remains unresolved.
- Move the GitHub cron earlier: better margin but different filing coverage and
  therefore a research-design change.
- Run the complete forward job in a Worker: impossible and unnecessary because
  the Worker should not receive research credentials or run the Python stack.
- Expose a public dispatch endpoint: rejected because it would create an
  unauthenticated privileged trigger.

## Consequences

The repository now contains a reviewable, testable external-scheduler boundary
without changing live production behavior. Deployment still requires a
Cloudflare account, a scoped GitHub token, and an operator cutover decision.
The duplicate guard is best effort; GitHub concurrency and the append-only
registry remain the integrity boundaries. The existing schedule stays as a
rollback path until the cutover has its own evidence.

## Verification

The Worker contract tests run in CI with Node 24. The eventual cutover requires
one observed dispatch, a successful forward run, and review of its recorded
`pre_open_schedule_margin`; only then can the GitHub schedule be removed.
