# Public-surface review

This review covers the anonymous Vercel deployment. It is a design and
verification record, not a claim that provider-side rate limits or data-license
terms have been independently approved.

## Boundary and decisions

| Surface | Public by design | Control | Remaining operator decision |
| --- | --- | --- | --- |
| `public/` static bundle | React UI and derived snapshot only | CI rebuilds and validates the bundle; the Vercel build rebuilds both supported output directories and checks that disclosure files are present | Confirm the snapshot's source and redistribution terms before changing its contents |
| `GET /api/v1/*` | Derived research metadata, signals, and prospective aggregates | No mutation routes; a hosted Postgres reader URL is required; query lengths/page sizes are bounded; errors are generic where storage details could leak; security headers are applied by FastAPI and Vercel | Confirm the public fields remain acceptable as the forward registry grows |
| `/api/docs` and OpenAPI | API contract and interactive documentation | Read-only endpoints; CSP explicitly allows the pinned documentation CDN while blocking objects/forms and framing | Disable public docs if deployment policy later treats the contract as private |
| Anonymous traffic | No account/authentication required for the research terminal | Cache headers and compression reduce normal load; request IDs are safe-character allowlisted | Configure CDN/provider rate limits or a WAF if traffic becomes abusive; Python process-local counters would not be reliable across serverless instances |
| Raw source data and credentials | Not public | Raw filings, market data, model checkpoints, database URLs, R2 credentials, and source maps are excluded from the bundle and deployment inputs | Re-check provider licenses and rotate credentials after any suspected exposure |

The bundle also publishes two low-cost discovery controls: `robots.txt`
discourages indexing of the read-only API and interactive API docs, while
`/.well-known/security.txt` points security reports to GitHub's private advisory
channel and identifies the supported languages. These files are checked by the
same public-bundle validator used by CI and the Vercel build. They do not
replace provider-side WAF/rate-limit configuration or establish a source-data
license.

It also publishes `data-provenance.json`, a conservative machine-readable
contract for the snapshot's source families and redistribution boundary. It
explicitly declares that raw sources are not public and that legal/provider
review remains required. CI validates the shape and statuses; this is evidence
of the repository's disclosure posture, not evidence that any provider has
approved redistribution.

## Failure behavior

- A failed public-bundle validation stops the Vercel build before a deployment is
  accepted.
- A missing/invalid snapshot degrades the health endpoint; it does not replace
  the committed snapshot with a partial result.
- A missing or unavailable forward registry returns an explicit disconnected or
  `503` response; the API never falls back to a hosted writer credential.
- Oversized or malformed query inputs fail with `422` before an unbounded scan.
- Database failures return generic public errors, and forward-run error records
  use provider-neutral, value-redacted messages; the server logs remain the
  private operator channel and must not be copied into public responses.

## Verification

Run the same checks used by CI before opening a deployment PR:

```bash
npm ci --prefix apps/web
npm run build:public --prefix apps/web
python3 scripts/validate_public_bundle.py
git diff --exit-code -- public
uv run pytest -q tests/integration/test_api.py
```

After a successful Production deployment, GitHub automatically runs the
`Deployment smoke check` workflow against the deployment status target URL. The
same workflow keeps a manual HTTPS-origin trigger for rechecks and non-GitHub
deployments. It performs only bounded `GET` requests to the
homepage, `robots.txt`, `/.well-known/security.txt`, `data-provenance.json`, and
`/api/v1/health`; it rejects cross-origin redirects, unexpected content types,
any mismatch in the full security-header contract (including a one-year HSTS
minimum), degraded health, and
oversized responses. The retained report contains paths, statuses, and health
state but never response bodies or credentials. This is a runtime observation,
not proof of provider-side rate limits, backups, or database grants.

The deployment build rebuilds the bundle and checks its required disclosure
files, but it does not replace CI review or establish provider-side traffic
controls. Keep the review with the release record and revisit it when public
fields, data sources, hosting, or traffic patterns change.
