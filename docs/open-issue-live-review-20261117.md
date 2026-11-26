# Open-issue live review, November 17

Three issues remain open. This review records current evidence without closing
issues, replaying missed observations or changing acceptance criteria. No paid
plan, source request, database connection or new hosted application read was
used for this review.

## Scheduler delivery (#156)

[PR 452](https://github.com/hoangnguyen2003/edgar-moe/pull/452) repaired an expired
metadata diagnostic in a separate workflow, leaving all twelve active forecast
scope hashes unchanged. Its CI passed all 15 applicable checks, including 2,840
Python tests (36 skipped); two conditional deployment checks were skipped.

The [merged-main diagnostic](https://github.com/hoangnguyen2003/edgar-moe/actions/runs/44054308815)
verified the active Cron and private Worker boundary using three GETs. Its one
GraphQL query returned the redacted category `access_refused`. The retained
[receipt](../ops/issue156/cron-inspection-20261117.json) records zero external
writes and zero Worker invocations. Configuration success does not prove the
stored GitHub dispatch credential is valid. Analytics refusal is a blocker to
event inspection, not a verified cause of the missing dispatch.

The November 17, 07:17 UTC probe has no Actions run. Its 90-minute delivery
window is over, so this scope cannot produce the required prerequisite. The
November 18–20 forecast workflow must continue refusing before source credentials
or dependencies when that proof is missing. A manual replay or a relaxed
prerequisite would not meet the original acceptance criteria. Any replacement
must use a separately recorded scope and fresh dates after diagnosing delivery.

To inspect event history without broadening the deployment credential, this draft
follow-up proposes that the inspection workflow expect a **separate**, temporary
`CLOUDFLARE_ANALYTICS_READ_TOKEN` secret in the GitHub `scheduler` environment.
The state-check step still receives only the original Worker-scoped token;
the proposed GraphQL step receives only the analytics token. There is no fallback
to the deployment token. Cloudflare documents
[Account → Account Analytics → Read](https://developers.cloudflare.com/analytics/graphql-api/getting-started/authentication/api-token-auth/)
for this API. Restrict the new token to the existing account and short expiry;
do not add edit, billing or token-management rights. Its value belongs in GitHub
Secrets, not a file, issue or conversation. No such token was created or installed
by this review, and no second GraphQL request was sent. The credential change is
proposed for review and must remain unmerged until the required secret exists;
it is not represented as deployed. The fixed diagnostic
window still expires November 18, 00:00 UTC; it cannot silently renew itself.

## Hosted read study (#290)

The [interim audit](../ops/private-read-study/evidence/progress-20261117.json)
was generated at November 17, 09:13:54 UTC by the existing artifact collector
and summarizer. All five scheduled attempt-one main-branch receipts passed
the existing flat ZIP inventory and SHA-256 validation. The original four JSON
receipts plus manifests and this audit are retained in a separate local packet;
its 21-file manifest SHA-256 is
`bb18673d874fe1ca3a7797afd7a4685fd53ff541402f4dd7e85f29834bcd89cf`.

There are ten HTTP attempts, zero HTTP/contract errors, ten passing provider
traces, five cold and five warm startup classifications. All five pairs have
independent idle-to-active database metadata; this is pair-level attribution,
not an exact per-request database-resume timestamp. No pair is degraded. There
are observations on three UTC dates, not seven completed measurement days.

Only slots 5, 9, 16, 26 and 34 were observed in the first 42 completed hourly
slots. The other 37 completed slots remain missing; future hours are not counted
as missed. The workflow is active and the observed runs succeeded, so the gaps
are not attributed to the application's HTTP path. Their upstream scheduling
cause remains unverified. Manual requests cannot replace them or satisfy the
declared schedule provenance. The existing 168-pair complete-coverage target is
already unattainable in this window, even if every remaining hour runs.

The unchanged window ends November 22, 15:00 UTC (22:00 Vietnam time). Preserve
the current collection and finalizer, retain its incomplete coverage and review
cohort sizes, local comparisons, safe plans and all errors before deciding what
additional study is needed. A successful finalizer or elapsed week alone cannot
close #290. No SLO, percentile-tail reliability claim or completed study is
asserted by this interim audit.

## Historical candidate membership (#292)

The merged [bank identity audit](../ops/issue292/bank-filing-audit-20261117.json)
still has 492 verified identities among 503 source securities. Eleven remain:
ten ownership-evidence gaps and one periodic-filing gap. The
[checkpoint audit](../ops/issue292/checkpoint-bank-audit-20261117.json) separately
retains unverified historical market continuity and the missing new study chain.
Only 302 literal issuer/symbol pairs match the original 500-security checkpoint;
201 historical source securities are absent and 198 checkpoint securities are
outside the source. Hash integrity does not repair this different cohort.

Remaining work is to resolve source identities, establish dated security and
market-identifier continuity, bind selection and exclusions to screen inputs,
and produce a separately identified checkpoint, processed dataset and study
under the applicable reviewed source-use evidence. Nothing in this operational
review adds an identity, supplies missing historical evidence or admits the old
dataset. This issue needs active data/research work, not an observation wait.

Frozen v1, original v2 data and append-only forward records remain unchanged.
