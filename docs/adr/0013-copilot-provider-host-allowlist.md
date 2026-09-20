# ADR 0013: Restrict copilot remote provider egress to exact hosts

- Status: accepted
- Date: 2026-09-20
- Deciders: project maintainer

## Context

The operator-run copilot sends an authorization header and bounded evidence
context to an OpenAI-compatible endpoint. The adapter already requires HTTPS for
remote providers and rejects redirects, but an accidental or compromised
endpoint setting could still point the request at an unintended HTTPS origin.
The provider key must not become a general-purpose egress credential.

## Decision

Require the adapter to normalize an exact comma-separated hostname allowlist for
remote HTTPS requests. The default list contains only `api.openai.com`. A
custom remote provider must be added explicitly through the private
`EDGAR_MOE_COPILOT_ALLOWED_HOSTS` setting. Entries containing schemes, paths,
ports, credentials, wildcards, or invalid DNS labels are rejected. Loopback
HTTP endpoints remain supported as a local-development exception for runtimes
such as Ollama; non-loopback HTTP remains rejected.

The endpoint path is still validated separately, and the no-redirect opener
remains required. The host list is runtime configuration, not an answer field,
so private reports do not retain endpoint details or secrets.

## Consequences

- The default configuration cannot silently send the copilot key to an
  arbitrary HTTPS host.
- Custom providers require an intentional, reviewable configuration change.
- Local free-tier runtimes remain practical without exposing a remote allowlist.
- Exact host matching does not replace provider-side network policy, TLS,
  secret rotation, or operational evidence.

## Verification

Unit tests cover exact-host matching, loopback exception, malformed allowlist
entries, and settings propagation. The normal Python lint, type, coverage,
benchmark-contract, and immutable-snapshot gates remain required. Provider-side
evidence and human review for issue #38 remain separate operational
requirements.
