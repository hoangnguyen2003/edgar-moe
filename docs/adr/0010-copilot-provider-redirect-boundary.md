# ADR 0010: Reject provider redirects at the copilot egress boundary

- Status: accepted
- Date: 2026-09-20
- Deciders: project maintainer

## Context

The operator-run research copilot sends bounded evidence context to an
OpenAI-compatible chat-completions endpoint. Endpoint validation permits HTTPS
for remote providers and HTTP only for loopback local runtimes. Python's
default URL opener follows HTTP redirects, however. A provider endpoint that
returns a redirect could therefore move the request to a different host or
scheme after validation, making the egress boundary implicit and harder to
audit. The request can also carry an authorization header and evidence payload.

## Decision

Use a dedicated `urllib` opener whose redirect handler raises a non-sensitive
`CopilotProviderError`. Redirects are not retried. The existing HTTPS and
loopback-HTTP endpoint checks remain unchanged, as do the bounded retries for
explicitly transient responses and transport failures.

## Consequences

- Provider endpoints must expose the final chat-completions URL directly.
- Misconfigured gateways fail clearly instead of silently crossing an origin
  boundary.
- The copilot does not need to retain redirect targets or response bodies.
- Providers that require a redirect-based login or discovery flow are outside
  this deliberately small operator adapter; configure their final API endpoint
  explicitly.

## Verification

Unit tests exercise the redirect handler and preserve the existing retry and
endpoint-validation tests. The normal Python lint, type, and coverage gates
remain required. This control protects the provider egress boundary; it does
not replace provider-side network policy, secret rotation, or human review.
