# Historical SEC security identity evidence, 2026-11-07

The fixed historical [candidate source](issue-292-historical-candidate-source.md)
now has a reproducible SEC issuer/share-class/CUSIP anchoring path. It remains
source evidence, not a completed historical market master or corrected v2 study.

## Method and actual result

The collector orders all 503 source rows by the hash of
`issue292-security-anchor-v1|snapshot_member_key`, without reading current
listing status, prices or outcomes. Its first 12 rows all yielded real matching
pre-cutoff periodic-filing and ownership-filing anchors. The sample was fixed
before those responses were inspected. It is feasibility evidence, not an
estimate of complete-source accuracy or coverage.

A periodic 10-K/10-Q binds CIK, trading symbol, registered class and exchange
within the same XBRL context. Combined reports may contain several registrants;
the symbol's context must identify the requested CIK. Schedule 13G/13D cover
fields supply a class and a check-digit-valid CUSIP. The complete submission's
explicit `SUBJECT COMPANY` CIK and accession must match the requested issuer and
filing. A reporting-owner CIK or presence in an issuer's submissions list is
insufficient: that list can include holdings reported about another company.
Class letters, voting
rights and common/ordinary distinctions must agree. Preferred, depositary,
unit and ambiguous classes are refused. Both reported acceptance times plus
seven calendar days must precede the source cutoff. Discovery is limited to
the preceding two years, at most four periodic and five ownership filings.
These are explicit coverage bounds, not grounds for dropping unresolved rows.

The SEC archive/submissions responses are observed in November 2026, separately
from their reported historical acceptance dates. These are not signed historical
timestamps. Filing facts establish identity at their filing dates; a CUSIP or
class may change later. Neither this join nor a successful lookup in today's
asset API proves uninterrupted identity or market-data availability through all
development folds.

The original checkpoint manifest and each reused source are hash-verified.
Live capture uses fixed SEC hosts, an identified contact, no more than two
requests per second, no redirects/retries, a request budget, a 16 MiB response
cap and a 1 GiB per-run response ceiling. A source over the document cap is
retained as a refusal, never silently omitted. The original collector stopped
on any HTTP/transport error; bounded transient deferral is described below.
Current tickers in submissions are ignored. Private files use the existing
owner-only, exclusive, alias-resistant capture primitives.

Completed member receipts survive interruption; resume recomputes them from
the original source bytes rather than trusting saved extraction results.
Code digests are checked before and after the run. Missing/tampered source
bytes, changed bindings, invalid CUSIPs, late mappings, reused tickers and
cross-context joins fail closed. Synthetic tests make no provider requests.

## Reproduction

Use an isolated checkout with research dependencies. Retain the candidate
capture and original checkpoint privately. For a new sample directory:

```sh
python -m scripts.capture_historical_security_identity --live \
  --source-capture data/artifacts/historical-candidate-source \
  --retained-checkpoint data/raw/authenticated/2026-07-31 \
  --output-dir data/artifacts/historical-identities --limit 12 --max-requests 96
```

Repeat with `--resume` and omit `--live` for offline source replay. A full run
omits `--limit`; all 503 rows remain its denominator. Interrupted full-source
collection must not be reported as complete. An exhausted budget or unavailable
source requires inspection before another live invocation; there is no automatic
retry or background scheduler.

## Bounded deferral and independent offline replay

The follow-up collector can continue independent candidates after an isolated
HTTP 500/502/503/504 or timeout. It records that source's exact URL, observed
failure and no-body/no-retry flags privately, and reports the member as
`source_temporarily_unavailable`. The failed URL is not requested again within
that run, including when another member shares it. Three consecutive transient
request failures open the circuit before any fourth request. A `Retry-After`
response opens it immediately. HTTP 403/429 and other errors still stop the run.
Successful new source requests reset the consecutive-failure counter; cached
responses do not conceal a continuing service outage. No missing source is
treated as an exclusion or a successful anchor. Request, response and rate caps
remain in force.

`scripts/verify_historical_security_identity.py` separately re-extracts a saved
result with no HTTP client. It verifies the content-addressed result filename,
source-roster/checkpoint bindings, exact selected order, source bytes/receipts,
all recomputed member results, counts and false completion flags. A recorded
unavailable source can reproduce only an unresolved member, after its separate
private failure receipt is rechecked. A missing source without that bound
receipt fails verification. Hashes remain traceability evidence, not independent
authentication against a writer controlling the whole private directory.

```sh
python -m scripts.verify_historical_security_identity \
  --directory data/artifacts/historical-identities \
  --result result-<sha256>.json \
  --source-capture data/artifacts/historical-candidate-source \
  --retained-checkpoint data/raw/authenticated/2026-07-31
```

The original 12-row sample was successfully re-extracted by this independent
entrypoint with zero provider requests before the explicit subject-CIK check
was added. Those earlier anchors require revalidation against complete
submission headers; primary-document bytes alone cannot satisfy the strengthened
gate. All 12 have since passed the subject check in the full-roster run, whose
remaining coverage is separate. A separate one-request free-tier probe
of permanent-UUID bar addressing returned HTTP 400 and zero rows. That negative
result does not establish market continuity; no fallback request was made.

## Remaining issue requirements

The full-source expansion initially stopped after 55 processed candidates on
an SEC read timeout. A later manual resume reused every completed source and
reached 56 candidates before an SEC HTTP 503 stopped it again. Neither run
automatically retried. Its retained receipts contain 51 anchored identities,
two unresolved periodic anchors, two unresolved ownership identities and one
oversized source. Missing/ambiguous identities remain in the audit. The 12-row
sample must not substitute for full-cohort verification.
The original checkpoint overlaps only 305 of the historical source's 500
issuers and cannot inherit a corrected source date or membership claim.

An individually bounded private CUSIP asset lookup succeeded using the existing
free account. It is a present-day association only. The official
[historical bar API](https://docs.alpaca.markets/us/reference/stockbars)
explains that `asof` can fall back to ticker-only data when a symbol is absent.
It supplies no historical knowledge-time guarantee. No historical market
continuity claim follows from that successful lookup, and no market bars were
requested by this collector.

Full class/identifier continuity, reviewed bar/revision assumptions and a new
source/selection/checkpoint/processed-dataset/study chain remain required by
[#292](https://github.com/hoangnguyen2003/edgar-moe/issues/292). The collector
always reports those completion flags as false. It does not release the general
source-use hold or publication gate. Frozen v1, existing v2 artifacts and
append-only forward records are preserved. Public artifacts contain only
operational counts, hashes and limitations; all source rows and CUSIPs remain
private. No paid plan, infrastructure change or trading operation was used.
