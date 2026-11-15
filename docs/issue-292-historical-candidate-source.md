# Historical candidate source assessment, 2026-11-06

Status: actual historical candidate-source capture; security continuity and
complete study binding remain unverified. This supplies a possible fixed
cohort for [#292](https://github.com/hoangnguyen2003/edgar-moe/issues/292), not a
repair of the original v2 universe or authorization for a research release.

## Source found and captured

The [MediaWiki revision API](https://www.mediawiki.org/wiki/API:Revisions)
returned revision **1130173030** of *List of S&P 500 companies*, page 2676045,
with server-reported timestamp **2022-12-28 23:23:50 UTC**. A second request
for that exact revision verified its content and server SHA-1. The pinned
wikitext SHA-256 is
`852a25efb345a50ba615e82de0dcc21fece03570a63c3d608637152fe453f67a`.
The executable capture also succeeded against the exact revision on November 6.

The table has **503 candidate rows and 500 distinct issuer CIKs**. The raw
response and extracted roster are owner-only, ignored local artifacts. The
checked-in commitment contains only revision metadata, counts, hashes, actual
retrieval time and explicit incomplete-status fields. November 2026 retrieval
has not been backdated to the source's December 2022 publication timestamp.
The platform currently reports that historical revision; this is not a signed
timestamp, official index-accuracy audit, or complete tradable-security master.

[`historical_candidate_roster_v1.json`](../config/historical_candidate_roster_v1.json)
selects every row from the pinned table. The parser keeps different share
classes sharing a CIK, preserves dotted tickers, and never consults today's
survivors. The `snapshot_member_key` identifies a source row, **not** a permanent
security identifier. Later renames or delistings do not remove captured rows.
The policy was designed in November 2026 with earlier research outcomes known;
it is not a pre-2023 study predeclaration or independent locked-test protocol.

## Private reproduction

From a checkout with research dependencies installed:

```sh
python -m scripts.capture_historical_candidate_roster \
  --live --output-dir data/artifacts/historical-candidate-source-new
python -m scripts.capture_historical_candidate_roster \
  --verify --output-dir data/artifacts/historical-candidate-source-new
```

Live mode makes one anonymous request for the pinned revision, with a one-MiB
response ceiling, no redirects and no retries. An existing or aliased output
is refused before creating the HTTP client. Files/directories are exclusively
created with owner-only permissions. Offline verification reuses the existing
bounded, alias-resistant source-capture reader and recomputes the roster and
commitment. Only a row-free operational commitment is printed.

Exact page/revision/timestamp/digests, table columns and candidate/issuer counts
are required. Later revisions, missing rows, repeated tickers, invalid CIKs,
unknown symbol syntax, duplicate JSON and tampered captures fail closed. CI
uses synthetic revisions only, including renamed/delisted candidates and
different classes sharing a CIK; no provider requests run in CI.

## Source terms and alternatives

The [2014–2023 Wikimedia terms](https://foundation.wikimedia.org/w/index.php?oldid=98524&title=Policy:Terms_of_Use)
describe CC BY-SA 3.0/GFDL for text contributions. The
[current terms, section 7](https://foundation.wikimedia.org/wiki/Policy:Terms_of_Use#7._Licensing_of_Content)
describe attribution, notices and modified-content duties. Retain the
[permanent revision](https://en.wikipedia.org/w/index.php?oldid=1130173030),
article history and applicable notices with any future source distribution,
and identify the roster as an extraction. This change distributes no source
rows or source text. This narrow assessment does not clear market/macro
vendors, index branding, or a future combined study.

A December 24, 2022 [DataHub commit](https://github.com/datasets/s-and-p-500-companies/commit/2f8dc5efbfae2019b098e82a27451f56bc235846)
has 503 ticker/name/sector rows and declares ODC-PDDL-1.0, but lacks CIKs and
security identifiers. [GH Archive](https://www.gharchive.org/)'s December 24
17:00 UTC archive records pull-request event 26077032486 at **17:53:27 UTC**
with that exact head commit. This establishes an independent recorded
publication event beyond the author-controlled Git commit date, but not the
missing identifiers. Private archive SHA-256:
`ee5598414bea83fc0f9ec3ab09cda8c08ad2a219ce8f3d82fbe6369aa5890fc0`.

Nasdaq's [directory documentation](https://www.nasdaqtrader.com/trader.aspx?id=symboldirdefs)
describes live files updated during each day. An Internet Archive metadata
request returned HTTP 429 and querying stopped; this is a retrieval limitation,
not proof that historical snapshots are absent. Neither current directories
nor an unverified archive response were substituted.

## Remaining acceptance

The separate [checkpoint compatibility preflight](issue-292-checkpoint-preflight.md)
independently replays the retained identities and checks the existing requested
roster and processed asset hashes. It refuses to promote compatible file
integrity or overlapping issuers into historical membership evidence.

1. Establish share-class/security continuity and dated market-identifier joins,
   including ticker reuse, exchange moves and corporate actions. CIKs identify
   issuers, and historical spelling alone does not establish security identity.
2. Obtain reviewed historical bar inputs and their availability/revision
   assumptions. Retain every cohort member and account for missing/excluded
   coverage without filtering later delistings out of the denominator.
3. Bind the reviewed source, mapping evidence, selection/exclusions, original
   screen inputs, checkpoint, processed dataset and a separately identified
   study. The old 2026-observed v2 master cannot inherit this source's date.

The source-use hold, `historical_membership_unverified` catalog state, frozen
v1, append-only forward records and active hosted/scheduler studies are
unchanged. This capture made no market-provider call, model run, database
operation, paid-plan change, new scheduled job or publication-gate release.
