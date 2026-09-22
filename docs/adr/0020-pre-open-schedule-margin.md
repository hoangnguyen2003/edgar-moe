# ADR 0020: Make pre-open schedule margin observable

- Status: accepted
- Date: 2026-09-22
- Deciders: project maintainer

## Context

The forward workflow is intended to run before the next regular NYSE open, but
GitHub Actions schedule triggers are best-effort. Moving the cron earlier would
change which same-day pre-market filings can be included, while an external
scheduler requires a provider and account decision. A late cycle can therefore
record forecasts with little time left before entry without any explicit status
signal.

## Decision

- At forecast-run completion, compute the time from the actual recording timestamp
  to the target entry open using the pinned NYSE calendar. On a trading day, the
  target is that day's open even if it has already passed; on a non-session day,
  it is the next regular open.
- Append an immutable `pre_open_schedule_margin` quality check to the forecast
  run. Store the margin in seconds, the 90-minute warning threshold, the
  observed timestamp, and the target-open timestamp; do not store raw provider
  payloads or scheduler internals.
- Mark margins below 90 minutes as `warning`. The existing registry status and
  optional redacted webhook classify that warning as `quality_warning`.
- Keep the existing source cutoff, strict `forecast_as_of < entry_at` invariant,
  and frozen v1 model unchanged. The check is observability, not permission to
  backdate or record an after-entry forecast.

## Alternatives considered

- Moving the cron earlier improves margin but changes same-day filing coverage and
  therefore requires a research-design decision.
- A Cloudflare/Vercel/external scheduler could preserve the current cutoff, but
  it introduces an external service, credentials, and a new operational failure
  boundary that has not been approved.
- Ignoring the delay leaves a known timing risk invisible to operators.

## Consequences

Late runs are visible in registry quality status, the public-safe status payload,
and the existing optional alert channel. The control does not guarantee a
punctual trigger or prevent a cycle from missing pre-market filings; it provides
the evidence needed to choose a scheduler change. A run after the target open is
represented as zero margin and a warning. The NYSE calendar remains a research
dependency, and an unavailable calendar produces a warning rather than silently
claiming a safe margin.

## Verification

Unit tests cover a 75-minute warning, a 150-minute passing margin, and a
post-open zero-margin warning around a known NYSE open. An integration test
verifies that a successful forecast run retains the check. Existing workflow,
alert, and frozen-snapshot gates remain unchanged.
