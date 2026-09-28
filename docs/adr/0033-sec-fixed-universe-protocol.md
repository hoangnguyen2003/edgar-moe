# ADR 0033: Pilot a pre-cutoff SEC fixed universe for a new v2 study

Date: 2026-09-28. Status: proposed; **not** an accepted point-in-time claim.
Extends [ADR 0031](0031-universe-observation-chronology.md) and
[ADR 0032](0032-universe-screen-preflight.md). Those records continue to govern
the existing screen until a separately reviewed implementation passes this
protocol.

## Context

The current broad SEC-to-Alpaca mapping was observed after the proposed
research cutoff. Hashes and a local observation timestamp prove its contents
and chronology, but cannot establish the population of securities available
before the development folds. A current ticker list, current submissions
metadata, or a nightly-recompiled bulk archive filtered by an old filing date
would still be a retrospective starting roster.

The SEC publishes [dated EDGAR daily and quarterly indexes](https://www.sec.gov/search-filings/edgar-search-assistance/accessing-edgar-data)
that enumerate filings with form, CIK, filed date, and archive path. The SEC
describes nightly index generation and possible next-business-day dissemination.
The [EDGAR XBRL guide](https://www.sec.gov/file/xbrl-guide) defines
`dei:TradingSymbol` and `dei:SecurityExchangeName` cover facts. These sources
make a pre-cutoff *filing-derived fixed-universe pilot* testable, but an index
row is a filer, not a listed security, and a symbol stated in a filing can
become stale before the screen date.

## Proposed decision

Build a **new, separately identified study**, not a silent repair of the
existing v2 artifacts:

1. Predeclare one fixed cutoff before the first validation fold, eligible
   forms, a trailing filing lookback, market-bar lookback, minimum liquidity,
   eligible exchanges/security classes, and a conservative index-availability
   lag. Never choose these after seeing validation or locked outcomes. Use the
   dated SEC index entry, not a current company/ticker API, to enumerate the
   broad CIK set. Use only index dates whose conservative availability bound
   precedes the cutoff; a filing's own earlier filed date cannot override a
   later index/dissemination date.
2. For each enumerated CIK, inspect the exact pre-cutoff filing accession(s)
   identified by the index. Extract cover-page symbol, exchange, and class
   context from that accession; retain its path and content digest. Do not
   copy today's `company_tickers_exchange.json` or present-day submissions
   ticker fields into the historical master. Reject missing, conflicting,
   multi-class, amended, or otherwise ambiguous mappings unless an explicit
   predeclared rule can resolve them using only pre-cutoff evidence. Record
   exclusion counts and reasons, not just the selected survivors.
3. Join to historical market bars only if their source and intended use pass
   [the rights gate](../source-rights-review.md). Require a versioned,
   independently evidenced identifier/listing history or demonstrate in a
   bounded pilot that the filing-derived identity and pre-cutoff bar match do
   not rely on a present-day security master. A bare matching ticker string is
   insufficient when reuse, rename, or corporate action is possible. Apply
   the liquidity screen using only bars available by the cutoff. Fail closed
   on absent or ambiguous history; do not backfill a late mapping.
4. Freeze the selected CIK/security identities at the cutoff. Keep members
   that later rename or delist in every applicable development fold; missing
   subsequent bars or labels are recorded as missing/coverage outcomes, not
   grounds for retrospective removal. New listings after the cutoff are not
   added to the fixed cohort.
5. Hash the exact index files, accession manifest and bytes, extraction and
   exception manifest, historical identifier source, bar-input manifest,
   screen policy, selected and excluded rosters, and code/config version.
   Bind this chain to a **new** checkpoint, processed dataset, and study ID.
   Reject a missing link, changed bytes, changed counts, or a source whose
   availability bound falls after the cutoff. Preserve frozen v1 and all
   append-only forward evidence.

This is a proposed protocol for a fixed cohort, not a claim that the SEC
archive alone supplies a complete tradable-security master. If the pilot
cannot establish historical identifier continuity and adequate class/issuer
coverage, obtain a separately evidenced historical listing source or redesign
the study; do not label the existing v2 screen point-in-time.

## Verification before adoption

- Offline synthetic fixtures must include pre-cutoff filers that later delist
  or rename, a ticker reused by another CIK, a late listing, a delayed index
  entry, a post-cutoff correction, multiple share classes, and tampered index,
  filing, mapping, bar, roster, checkpoint, and processed-dataset artifacts.
  No SEC or market-provider calls may run in CI.
- A bounded, privately retained real-archive pilot must report eligible CIKs,
  symbol/exchange coverage, ambiguous and excluded classes, dissemination
  lag assumptions, identifier-join failure rates, and the effect of keeping
  later delistings. Record the SEC source and capture hashes without
  publishing private/provider rows. Follow the SEC's
  [fair-access guidance](https://www.sec.gov/search-filings/edgar-search-assistance/accessing-edgar-data)
  for any live retrieval.
- Independent review must confirm the predeclared cutoff, source availability,
  issuer-to-security joins, provider rights, study identity, and no
  post-cutoff selection. The public v2 gate remains
  `historical_membership_unverified` and `pending_review` until that evidence
  exists and a separate publication PR passes review.

## Alternatives and consequences

Using today's inactive-inclusive Alpaca master or current SEC ticker metadata
is simpler but still conditions on present-day membership. Filtering a
nightly-recompiled SEC bulk ZIP by historical filing dates is useful for
discovery, not independent proof of what the broad roster contained at the
cutoff. A dynamically changing universe for each fold may eventually improve
coverage, but requires independently evidenced listing intervals at every
fold and is a different protocol. This fixed-cohort pilot is narrower and
auditable, at the cost of excluding post-cutoff IPOs and potentially many
ambiguous classes; those exclusions must be quantified before adoption.

No provider-backed run, historical master, or revised v2 result is authorized
by this ADR alone. [Issue #292](https://github.com/hoangnguyen2003/edgar-moe/issues/292)
and [source-rights issue #280](https://github.com/hoangnguyen2003/edgar-moe/issues/280)
remain open.
