# Public-surface review

This review covers the anonymous Vercel deployment. It is a design and
verification record, not a claim that provider-side rate limits or data-license
terms have been independently approved.

**Current boundary (ADR 0034, 2026-09-29):** the public app serves a synthetic,
hash-locked fixture only. Historical research evidence and prospective
registry routes are withheld; the FastAPI function has no registry connection.
The source inventory and observations below describe a former release and are
retained as historical evidence, not as today's payload. This change does not
purge Git history, reports, workflow artifacts, caches, or prior deployments,
and does not resolve source-rights questions.

## Boundary and decisions

| Surface | Public by design | Control | Remaining operator decision |
| --- | --- | --- | --- |
| `public/` static bundle | React UI and hash-locked synthetic fixture only | CI rebuilds and validates the bundle; the Vercel build checks the synthetic lock and disclosure files | Keep the fixture synthetic while source rights remain unresolved |
| `GET /api/v1/*` | Synthetic demo data, operational health, governance boundary, and fixed publication policy | No mutation routes or registry connection; forward evidence routes return fixed `410`/`no-store`; security headers are applied by FastAPI and Vercel | Rights review and explicit maintainer decision are required before any derived research output is restored |
| `/api/docs` and OpenAPI | API contract and interactive documentation | Read-only endpoints; CSP explicitly allows the pinned documentation CDN while blocking objects/forms and framing | Disable public docs if deployment policy later treats the contract as private |
| Anonymous traffic | No account/authentication required for the research terminal | Cache headers and compression reduce normal load; request IDs are safe-character allowlisted | Configure CDN/provider rate limits or a WAF if traffic becomes abusive; Python process-local counters would not be reliable across serverless instances |
| Raw source data and credentials | Not public | Raw filings, market data, model checkpoints, database URLs, R2 credentials, and source maps are excluded from the bundle and deployment inputs | Re-check provider licenses and rotate credentials after any suspected exposure |

The source archive and Python Function apply the private-data boundary
independently: raw/processed/forward data, model/operator directories, local
database files, and dotenv files are excluded, while only the synthetic
`data/demo/snapshot.json` is explicitly included. This protects local Vercel
builds as well as Git-based deployments. The bundle also publishes two
low-cost discovery controls: `robots.txt`
discourages indexing of the read-only API and interactive API docs, while
`/.well-known/security.txt` points security reports to GitHub's private advisory
channel and identifies the supported languages. These files are checked by the
same public-bundle validator used by CI and the Vercel build. They do not
replace provider-side WAF/rate-limit configuration or establish a source-data
license.

It also publishes `data-provenance.json`, a conservative machine-readable
contract for the snapshot's source families, content-addressed identity, and
redistribution boundary. It explicitly declares that raw sources are not public
and that legal/provider review remains required until an operator records
explicit approval. CI and the Vercel build
cross-check the published identity against `config/public_snapshot.lock.json`;
this is evidence of the repository's disclosure posture, not evidence that any
provider has approved redistribution. The read-only `/api/v1/governance`
endpoint presents the same boundary together with the content-addressed frozen-v1
identity and the publication boundary. Its forward status is a fixed policy
response, not the current state of the private registry; provider-side controls
remain pending operator evidence.

The release decision aid is deliberately separate from the serving build:

```bash
python3 scripts/public_release_readiness.py \
  --output /tmp/public-release-readiness.json
```

It composes the public-bundle and snapshot-lock validators into three explicit
states:

- `blocked`: a structural bundle or immutable snapshot check failed;
- `review_required`: the published structure is sound, but source terms or legal
  approval are still pending; or
- `ready`: every source is marked `approved`, `legal_approval` is `true`, and a
  UTC `last_reviewed_at` timestamp is present.

The report contains only error codes, counts, the locked snapshot identity, and
review state. It is not a license grant and does not assert provider-side WAF,
rate-limit, backup, or account evidence.

## What each source contributes to the bundle

The following source inventory describes content in the former locked snapshot,
not the current synthetic bundle. It remains here to scope the outstanding
redistribution review. `tests/unit/test_public_data_surface.py` checks the
current synthetic fixture; passing it does not verify the historical inventory.
Historical review must use the former release's content-addressed identity and
appropriately restricted evidence, not today's replacement fixture.

| Source | In the public bundle | Not in the public bundle |
| --- | --- | --- |
| SEC EDGAR | In the former locked snapshot: accession numbers, company names, tickers, form types, acceptance timestamps, SIC industry codes, and a `https://www.sec.gov/...` link per filing | Filing text or HTML, and XBRL fact values. The longest string in that snapshot was a 233-character status message this project wrote |
| Alpaca (IEX feed) | In the former locked snapshot: one `realized_abnormal_return` per filing and three portfolio series of 395 daily points | Quotes, bars, prices, volumes, or any per-session market record |
| FRED / ALFRED | Nothing. Regime features are model inputs; no series value or observation reaches the bundle | Every FRED series and observation |

The former snapshot included project-computed values such as model scores,
ranks, expert weights, attributions, and outcomes. These are an inventory of
content requiring review, not a statement that the current bundle publishes
them. The former bundle linked to filings rather than copying them.

That is the factual half of the review. The remaining half is a judgement about
each provider's terms, which belongs to the operator and is not made here or by
CI. Restoring any provider-derived output requires the source-specific decision
in issue #280 and a separate reviewed PR, not just editing approval flags.
The current manifest describes the synthetic generator, not the former sources.
For an explicitly authorized release, record each reviewed source's
`redistribution_status` as `approved` in
`apps/web/public/data-provenance.json`, then set the `review` block's
`redistribution_status` to `approved`, `legal_approval` to `true`, and
`last_reviewed_at` to the UTC time of the review. Until then
`public_release_readiness.py` reports `review_required`, which is why CI runs it
with `--allow-review-required`.

## Published images

The bundle is reviewable UTF-8 text apart from two declared images,
`apple-touch-icon.png` and `social-card.png`. The validator accepts them only as
PNG pixel data: allowed chunk types only, valid chunk checksums, nothing after
`IEND`, and at most 200,000 bytes, so no text, EXIF, or colour-profile chunk can
carry unreviewed content. `favicon.svg` and `sitemap.xml` stay text. The two
images are rendered from text sources by `scripts/render_brand_images.py`: the
card from `apps/web/brand/social-card.html`, and the touch icon from
`favicon.svg`, which draws the logotype's own "E" outline. The social image
describes the synthetic demo only; it does not repeat withdrawn v1 metrics.
Both images and their text sources have reviewed SHA-256 digests enforced by
the validator, so valid pixel-only PNGs are not accepted merely because of
their filename. A changed source or image requires visual review and an explicit
digest update. Metadata removal alone cannot prevent publication of sensitive
numbers drawn into pixels. The HTML description, Open Graph text, and image alt
text must also describe the current synthetic boundary. The image URL carries
a new revision query to distinguish it from earlier link previews; this does
not purge third-party caches. See
[ADR 0024](adr/0024-reviewed-public-images.md).

## Failure behavior

- A failed public-bundle validation stops the Vercel build before a deployment is
  accepted.
- A missing/invalid snapshot degrades the health endpoint; it does not replace
  the committed snapshot with a partial result.
- Prospective run, forecast, performance, and quality routes return a fixed
  `410`/`no-store` publication hold. The public API does not connect to the
  registry or fall back to a writer credential.
- Oversized or malformed query inputs fail with `422` before an unbounded scan.
- The public API has no registry database path. Private database failures are
  handled by operator workflows and their redacted evidence; those logs must
  not be copied into public responses.

## Verification

Run the same checks used by CI before opening a deployment PR:

```bash
npm ci --prefix apps/web
npm run build:public --prefix apps/web
python3 scripts/validate_public_bundle.py
python3 scripts/verify_public_snapshot_lock.py
python3 scripts/public_release_readiness.py --allow-review-required
git diff --exit-code -- public
uv run pytest -q tests/integration/test_api.py
```

After a successful Production deployment, GitHub automatically runs the
`Deployment smoke check` workflow against the HTTPS origin in the
`EDGAR_MOE_PUBLIC_DEPLOYMENT_URL` repository variable. This explicit public
origin is important when the hosting provider protects per-deployment URLs;
the workflow falls back to the deployment status target when the variable is
absent. The same workflow keeps a manual HTTPS-origin trigger for rechecks and
non-GitHub deployments. It performs only bounded `GET` requests to the
homepage, `robots.txt`, `/.well-known/security.txt`, `data-provenance.json`,
`/api/docs`, `/api/v1/governance`, `/api/v1/forward/status`,
`/api/v1/forward/performance`, and `/api/v1/health`. It requires the served
synthetic identity to equal the reviewed
`config/public_snapshot.lock.json` at the deployed commit, and rejects
cross-origin redirects, unexpected content types, security-header mismatches
(including a one-year HSTS minimum), degraded health, and oversized responses.
The forward-status check requires the fixed `withheld_review` policy and
`configured=false`/`available=false`; the performance route must return the
fixed `410` with `Cache-Control: no-store`. A public aggregate response or
cacheable hold fails the check. The retained report contains endpoint paths,
statuses, and allowlisted contract metadata, but never response bodies,
performance estimates, or credentials. This is a runtime observation, not proof
of provider-side rate limits, backups, or database grants.

If GitHub receives a terminal failure (`failure`, `error`, or `inactive`) for a
Production deployment, the same workflow retains a redacted
`deployment-failure.json` artifact for 30 days and fails the deployment gate. It
records only the deployment state, commit, GitHub status identifiers, and public
target URL; it does not copy provider logs or secrets. Transient `queued` and
`in_progress` statuses are ignored. The operator must inspect the provider logs,
redeploy the reviewed commit, and rerun the smoke check before treating the
public surface as current.

The deployment build rebuilds the bundle and checks its required disclosure
files, but it does not replace CI review or establish provider-side traffic
controls. Keep the review with the release record and revisit it when public
fields, data sources, hosting, or traffic patterns change.
