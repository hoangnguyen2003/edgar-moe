# Historical checkpoint compatibility preflight

`scripts/audit_historical_checkpoint.py` checks whether an existing checkpoint
can be reused as the fixed historical source. It independently replays the
selected legacy, issuer-proxy, issuer-definition, charter-redesignation or FDIC-bank identity supplement and all
its previous baselines before reading the checkpoint's hash-bound requested
roster. It compares literal issuer and
symbol pairs, retaining multiple share classes with one CIK. A renamed symbol
or another class of the same issuer is not silently substituted. Missing or
later-delisted source members remain in the denominator.

The preflight separately verifies the processed dataset's source-manifest
binding, dataset identity, four asset filenames and actual asset hashes. This
establishes integrity of those bytes, not historical membership or continuity.
Changed manifests, requested rows, feature bytes, duplicate JSON fields,
symlinked paths, hard-linked files and excessive inputs are refused. Only
counts, hashes, operational statuses and blockers enter the report; issuer,
symbol, identifier and provider rows stay private.

Run from an isolated checkout with the existing private inputs:

```sh
python -m scripts.audit_historical_checkpoint \
  --directory data/artifacts/private-identity-capture \
  --identity-result class-redesignation-<sha256>.json \
  --governing-sources data/artifacts/private-legacy-sources \
  --proxy-sources data/artifacts/private-combined-proxy-sources \
  --redesignation-sources data/artifacts/private-redesignation-sources \
  --source-capture data/artifacts/private-candidate-source \
  --retained-checkpoint data/raw/authenticated/2026-07-31 \
  --processed-dataset data/processed/v2-finbert/<dataset-id> \
  --output-dir data/artifacts/historical-checkpoint-preflight
```

The new output directory and content-addressed report are owner-only and
exclusively created. For independent replay, replace `--output-dir` with
`--verify-report data/artifacts/historical-checkpoint-preflight/compatibility-<sha256>.json`.
The supplied paths must be canonical directories without aliases.

Supported result prefixes are `legacy-governance`, `proxy-class`,
`proxy-definition`, `class-redesignation`, `bank-filings`, `capital-definition`
`capital-stock-tags`, `fdic-listed-equity`, `listed-isin`, `issuer-vote`, and `issuer-offering`.
The filename digest, internal stage kind and declared
algorithm identities must match before the selected stage independently
replays. The report binds that stage kind and the replay algorithm-set hash.
For either proxy stage and the redesignation stage, `--proxy-sources` is mandatory and must preserve every
prior/current source pair needed by the chain. For the legacy-only stage, omit
that option; unused proxy inputs are refused. Missing or corrupt current-stage
sources cannot fall back to an earlier result or an attested anchor count.
For `class-redesignation`, `--redesignation-sources` is also mandatory.
That option is refused for all earlier stages, so no source input is silently ignored.

Exit **2** means a valid retained report with historical reuse **blocked**;
exit **1** means invalid or unreadable evidence. This command intentionally has
no successful-admission mode. Even identical literal rosters cannot establish
security continuity, source rights, screen selection, or a complete new study
chain. No HTTP client, provider read, model run, checkpoint write or publication
release occurs. The complete checkpoint's other raw assets are outside this
preflight; the report binds its manifest and requested-roster bytes explicitly.

The original [historical candidate protocol](issue-292-historical-candidate-source.md)
and [identity evidence](issue-292-security-identity.md) remain separate. The
frozen v1, original v2 dataset and append-only forward records are unchanged.

## Retained checkpoint result, November 14, 2026

The actual source contains 503 securities across 500 issuers; the original
checkpoint requests 500 securities across 500 issuers. Only **302 literal
issuer/symbol pairs match**, although **305 issuer CIKs overlap**. There are
**201 historical source securities absent** from the checkpoint and **198
checkpoint securities outside** the fixed source. The three additional
source share-class rows were retained, not collapsed by CIK. These are literal
comparisons, not a count of independently verified security continuities.

The original processed dataset's four assets match their manifest, and that
manifest binds to the original checkpoint. That integrity result does not
repair the different candidate cohort. The identity replay still has 488
anchors and 15 unresolved candidates. Historical continuity and a separately
identified source/selection/checkpoint/processed-dataset/study chain remain
unverified. The preflight records all these blockers and exits 2; no original
dataset is renamed or admitted as the historical cohort.

The [row-free audit](../ops/issue292/checkpoint-compatibility-audit-20261114.json)
binds the content-addressed report, exact verifier code and a private 16-file
packet, including the original processed assets and requested roster. The
preceding 21-file identity packet was rehashed separately. Both complete
preflight invocations independently replayed the identity chain and reproduced
the same report without network requests. A modified copy of the real feature
asset was refused; the original remained unchanged. Validation: 2,521 full-suite
tests passed, 36 skipped, 85.87% coverage, and 42 focused checks passed.

## Issuer-definition identity-chain replay, November 16, 2026

The preflight now accepts the separately content-addressed 490-anchor
issuer-definition result and replays its complete parent chain. The 13
unresolved identities remain blockers. The actual checkpoint still has only
302 literal issuer/symbol matches, with 201 source securities absent and 198
checkpoint securities outside the fixed source. More verified identity anchors
do not repair a different cohort or establish market continuity.

The [current row-free audit](../ops/issue292/checkpoint-compatibility-audit-20261116.json)
binds a new report and independent replay. The November 14 report, its original
algorithm version and all original data remain unchanged. The command still
returns 2 for a valid blocked report and 1 for invalid evidence. Synthetic tests
also demonstrate that even identical rosters and complete identity counts
cannot release the historical continuity or new-study-chain gates.


## Charter-redesignation replay, November 16, 2026

The fourth supported stage independently replays the 491-anchor result and all
its previous baselines. Its [separate row-free audit](../ops/issue292/checkpoint-redesignation-audit-20261116.json)
retains the new report without replacing either earlier checkpoint audit.
Twelve identities remain unresolved. The original requested roster still has
302 literal matches, with 201 source securities absent and 198 checkpoint
securities outside the fixed source. The original processed assets remain
hash verified; historical continuity and a new study chain remain unverified.
A valid blocked report continues to exit 2 and cannot release publication or
mark the issue complete.


## FDIC/SEC bank supplement

The [bank filing supplement](issue-292-bank-filings.md) adds an explicit fifth
replay stage. For `bank-filings-<sha256>.json`, retain both `--bank-sources` and
`--bank-ownership-sources`, plus the full proxy, redesignation and legacy parent
sources. Earlier stages refuse the two bank-only options. The preflight replays
the complete selected stage and binds its decoder and producer versions; no
missing bank source can fall back to an earlier count.

FDIC-periodic/SEC-ownership anchors have their own status. They contribute to the
verified identity total without relabeling their regulator or changing previous
anchor payloads. Historical continuity and a separately identified new study
chain remain independently blocked.

## Explicit capital-definition replay

The [capital-definition supplement](issue-292-capital-definition.md) adds a
sixth stage after the bank result. Select `capital-definition-<sha256>.json`
with `--capital-sources` in addition to all five parent source-family arguments.
Each older stage refuses this new argument; the new stage refuses missing
current or parent sources. Its filename, stage kind, algorithm identities and
independently recomputed payload must all match. There is no fallback to a
previous anchor count.

The new identity total is 493 of 503. The original checkpoint still has 302
literal matches, 201 absent source securities and 198 checkpoint securities
outside the fixed source. Original processed bytes remain separately verified;
continuity, new study binding, publication and issue completion remain blocked.
A valid retained report still exits 2, and invalid evidence exits 1.

## Common-stock tags in issuer filings, November 20, 2026

The `capital-stock-tags` stage accepts a registered capital-stock class when
the same filing uses standard common-stock tags for its printed outstanding
share count and balance-sheet par value. It requires one registered class,
issuer-scoped facts with matching dates and units, and at least two retained
ownership filings that name unqualified common stock and agree on the security
identifier. Capture with `python -m scripts.enrich_historical_capital_stock_tags`
using `--baseline` for the capital-definition result and `--references` for
an owner-only JSON plan in the same capture directory. The plan has
`schema_version` 1, `scope` set to `private_same_filing_capital_stock_tags_only`,
and `snapshot_member_keys` listing the unresolved ownership cases to replay.
To replay in the compatibility audit, select `capital-stock-tags-<sha256>.json`
and supply the same source arguments as for the capital-definition stage.

The retained replay adds one SEC identity and preserves the prior 493 anchors.
The fixed cohort now has 494 anchored identities, eight unresolved ownership
cases and one unresolved periodic registration. The private identity result has
SHA-256 `4d5160d9789bda9c184a854b0ed2f1813a868ea86f5e57a5e42d5a93d58cf68f`.
The replay used retained evidence and made zero provider requests.

## FDIC registration and SEC prospectus replay, November 21, 2026

The `fdic-listed-equity` stage pairs an FDIC quarterly registration with a
pre-cutoff SEC pricing supplement. The prospectus identifies the same bank
by its FDIC certificate and names the bank's common stock, ticker, exchange,
and security identifier in a single overview. The verifier checks the
identifier against filed ownership reports with the requested SEC subject
CIK. The ownership headers and FDIC report disagree on the tax identifier.
The prospectus supplies the certificate-based link, and replay binds the FDIC
registration, prospectus, and ownership receipts separately. The archival price table supplies the labelled equity identifier.
Its price values are outside this check.

Capture with `python -m scripts.enrich_historical_fdic_equity`. Use the
capital-stock-tags result for `--baseline`, an owner-only member and source
plan for `--references`, and the archived SEC submission cache for
`--listed-equity-sources`. To replay through the checkpoint audit, select
`fdic-listed-equity-<sha256>.json` and provide the same source directories.

## Issuer-listed ISIN and SEC fund replay

The `listed-isin` stage binds the same issuer's periodic registration and
archived listing prospectus by registrant, share class, nominal value and
ticker. A public pre-cutoff Form N-PORT submission gives the same ISIN and
a CUSIP for the direct ordinary-share holding. Two authenticated SEC
ownership filings agree on that CUSIP. An exit notice lacking an identifier
is recorded as a refusal.

Capture with `python -m scripts.enrich_historical_listed_isin`. Use the
FDIC-listed-equity result for `--baseline`, the owner-only source plan for
`--references`, and the archived filing cache for `--listed-isin-sources`.
To replay through the checkpoint audit, select `listed-isin-<sha256>.json`
and provide the same source directories.

The retained result verifies 496 of 503 candidate identities, preserves
the prior 495 anchors and leaves seven ownership cases unresolved.
Result SHA-256: `0130ebe6af14f8fab4cf1e341613defd5a952b953ae4ea365936871dde112ee7`.
Both retained full-size checkpoint rosters match only 302 historical
issuer-and-symbol pairs. The identity update leaves market continuity and
the new study binding unresolved.

## Issuer and fund voting-report replay

The `issuer-vote` stage matches the issuer's ordinary-share registration
and annual meeting results to separately filed fund voting reports. It
requires the same ticker, meeting and security identifier in both fund
reports, the issuer's record date in one report, and agreement with the
subject-authenticated ownership filings. Ownership filings that supply
only the corroborated ISIN in a CUSIP field or omit the identifier are
recorded as refusals.

Capture with `python -m scripts.enrich_historical_vote_identity`. Pass the
listed-isin result as `--baseline`, the owner-only plan as `--references`,
and the archived filing cache as `--vote-identity-sources`. The checkpoint
audit accepts `issuer-vote-<sha256>.json` with the same sources.

## Issuer ordinary-share offering replay

The `issuer-offering` stage checks a filed issuer offering against its later
periodic registration. It requires the same registrant, ticker, listing
venue and par value. The ordinary-share terms supply the CUSIP and ISIN in
one labelled row. Subject-authenticated ownership filings corroborate the
CUSIP, including at least one available in the year preceding the cutoff.
Reports for a different SEC subject or notices lacking an identifier are
recorded as refusals.

Capture with `python -m scripts.enrich_historical_offering_identity`. Use the
issuer-vote result as `--baseline`, the owner-only source plan as
`--references`, and the issuer filing cache as `--offering-identity-sources`.
The checkpoint audit accepts `issuer-offering-<sha256>.json` with the same
sources. For `ordinary-holdings-<sha256>.json`, also supply the retained Form
13F source directory using `--ordinary-holdings-sources`.

## Replaying a new fixed-cohort screen

`python -m scripts.replay_historical_market_screen --help` lists the offline
replay options. Supply the private SEC identity result and its original source
directories as for the checkpoint preflight. Provide an independently retained
SHA-256 of the private `review.json` with `--review-sha256`. Keep the package
under ignored `data/artifacts` in owner-only files. The review covers the
market source version, permissions, the source's publication and revision
history, and the transformation from the retained archive to the replay inputs.

- `review.json` uses schema version 1 and scope
  `private_historical_market_screen_replay`. It binds
  `identity_result_sha256`, `source_version`, `listing_intervals_sha256`,
  `daily_bars_sha256`, `lookback_start`, `maximum_issuers`, `minimum_sessions`
  and `minimum_price`.
- `listing-intervals.json` is an array of
  `archive_security_id`, `cusip`, `symbol`, `valid_from`, `valid_before` and
  `available_by` objects. The interval is half-open over local market dates.
  Use a null end for an open interval. The reviewed record must support when the
  interval became available. The cutoff mapping must match the issuer filing's
  share-class CUSIP and the snapshot symbol.
- `daily-bars.json` maps the archive security IDs to arrays of
  `session`, `symbol`, `close`, `volume` and `available_by` objects. An empty
  series explicitly accounts for a member whose reviewed archive has no bars.
  Use contemporaneous USD prices and share volumes with their original units.
  Each bar must fall within its listing interval and have been available after
  the session close and by the screen cutoff.

The replay keeps every original candidate and ranks its share classes before
selecting the most liquid eligible class per issuer. The existing liquidity
ranking uses median dollar volume with a ticker tie-break. The issuer cap
applies after deduplication, and aggregate counts account for omitted classes
and inadequate data. `--output-dir` writes the selected members and screen
receipt to a new private directory. The command prints only aggregate counts
and hashes.

To verify an existing **new** checkpoint and processed dataset, supply
`--checkpoint`, `--processed-dataset` and `--dataset-binding-sha256` together.
The latter pins a `dataset-binding.json` in the private package. It uses schema
version 1 and binds `screen_sha256` (the exact `screen.json` bytes),
`checkpoint_manifest_sha256` and `processed_manifest_sha256`. The checkpoint
must request precisely the selected CIK, ticker and security IDs in screen
order. The replay verifies the processed assets and their source manifest.
The receipt covers the recorded selection and dataset lineage. Evidence of
market continuity across the study, source rights, and the publication decision
remain separate.

## Historical archive availability review, November 26, 2026

The fixed cohort has 503 independently verified share-class identities. A market
archive still has to join all of them to dated listings and account for the
lookback price and volume series. The screening input needs source-version
evidence for the exact observations known by the cutoff and checked permission
to retain and replay the selected export privately.

| Source checked | What the source documents | Remaining gap |
| --- | --- | --- |
| [NYSE Daily TAQ](https://www.nyse.com/data-products/catalog/daily-taq/) | Consolidated trades and a daily security master with published timing in the [client specification](https://www.nyse.com/publicdocs/nyse/data/Daily_TAQ_Client_Spec_v4.3.pdf). | The [public sample directory](https://ftp.nyse.com/Historical%20Data%20Samples/DAILY%20TAQ/) lists July 2026 master and trade samples and selected 2022 administrative files. The complete historical trade and master files require licensed access. |
| [CRSP US Stock Databases](https://www.crsp.org/research__trashed/crsp-us-stock-databases/) | Permanent identifiers, delistings, corporate actions, prices and volume across listed and inactive securities. | An authorized export and documentation for the relevant source releases and revisions are still needed. A recent extract alone does not date the version of its rows. |
| [Kibot daily guest API](https://www.kibot.com/api/history-request.html) | Ticker-keyed historical daily bars with an unadjusted-price option. | The API documentation does not establish the cohort's archived listing identity joins or bar-version availability. |
| [SEC market activity data](https://www.sec.gov/data-research/market-structure-data) | Historical trade-volume and activity metrics by ticker and day. | The [published fields](https://www.sec.gov/opa/data/market-structure/market-structure-data-security-and-exchange.html) include price ranks and volume. The replay requires the prices and dated identifiers themselves. |
| [QuantConnect US Equities](https://www.quantconnect.com/data/algoseek-us-equities) | Free cloud backtesting access and separate local download licensing. | The cloud offering does not establish permission to retain this study's reproducible local source package or supply its pre-cutoff release evidence. |

This review used product documentation and public file listings. A suitable
archive remains unverified within the zero-cost project budget. Keep the
historical membership and publication gates pending until an authorized source
can supply the required files and release evidence.

## DoltHub archive metadata check, November 27, 2026

[DoltHub's public stocks database](https://www.dolthub.com/repositories/post-no-preference/stocks)
is a zero-cost candidate for versioned daily prices and volumes. Its public
commit log returned `0cl0kenipoa5uapcenohfi6rc9q3r5bj` with a recorded commit
time of `2022-12-28 09:46:44.872` and the message `ohlcv 2022-12-27 update`.
The hash pins what DoltHub serves for that revision today. The recorded
commit time still needs independent evidence of when this exact revision
became available. DoltHub's retrospective account describes daily publication
since 2021. [DoltHub publication history](https://www.dolthub.com/blog/2024-09-27-dolt-post-no-preference/)

Two metadata queries returned the archived column definitions and zero data
rows. At the pinned revision, `ohlcv` stores daily close and volume by date
and `act_symbol`. The corresponding `symbol` schema has a security name,
exchange, ticker aliases and a last-seen date. Both schemas lack a permanent
security identifier. The archive still needs a verified dated join from those
tickers to the 503 retained share-class identities and evidence that bars
follow the required close, volume and revision conventions.

To reproduce the metadata check, send these queries to the public
[`master` SQL endpoint](https://www.dolthub.com/api/v1alpha1/post-no-preference/stocks/master)
using the [documented `q` parameter](https://www.dolthub.com/docs/products/dolthub/api/v1alpha1/sql/).

```sql
SELECT commit_hash, date, message FROM dolt_log
WHERE date >= '2022-12-26 00:00:00' AND date <= '2022-12-28 23:23:50'
LIMIT 12;
SELECT * FROM symbol AS OF '0cl0kenipoa5uapcenohfi6rc9q3r5bj' LIMIT 0;
SELECT * FROM ohlcv AS OF '0cl0kenipoa5uapcenohfi6rc9q3r5bj' LIMIT 0;
```

The current repository includes a CC BY-SA 4.0 license. The publisher's
description applies it to the database's selection and arrangement and
describes the underlying facts as public domain. A query for `LICENSE.md` and
`README.md` at the pinned 2022 revision returned no entries. Upstream bar
provenance and permission for private retention and replay still need review
before an export can enter the historical screen.
[Publisher description](https://www.dolthub.com/api/v2/databases/post-no-preference/stocks)

## SEC settlement identity cross-check, November 27, 2026

An offline comparison of the SEC file for the second half of November 2022
corroborated **493 of the 503** retained identities by literal ticker and
CUSIP. On November 30 alone, 221 retained pairs appear. Across the file,
495 retained CUSIPs appear, including two without the exact cohort ticker.
Two cohort tickers also appear with a different CUSIP. The audit retains
those differences and makes no ticker alias substitutions.
[SEC source and field definitions](https://www.sec.gov/data-research/sec-markets-data/fails-deliver-data)

The hash-bound file has 62,696 data records across 11 settlement dates from
November 15 through November 30. The SEC response reports a December 15,
2022 last-modified timestamp, consistent with the ZIP member's local date
and the SEC's release cadence. These metadata were retrieved in 2026.
Independent evidence for the original publication of these exact bytes
remains unavailable. [Public release metadata and aggregate results](../ops/issue292/sec-settlement-corroboration-20261127.json)

The comparison verifies the two input hashes, matches only the settlement
date, ticker and CUSIP fields against the retained identity result, and
checks the file's record-count trailer. An offline rerun reproduced the
aggregate report. Settlement rows record outstanding delivery failures
and omit securities without such a balance. They provide dated point
observations. The remaining work includes market-archive identifier
intervals, availability and revision evidence for the daily closes and
traded volumes, and the resulting screen-to-dataset binding.

## Additional settlement archives and exchange coverage, November 27, 2026

The SEC files for November 1–14 and October 17–31 add six exact ticker
and CUSIP matches. Across the three archives, **499 of 503** candidates
have an exact match and **501 of 503** have a matching CUSIP. The comparison
checks each archive hash and record-count trailer and uses the same fixed
cohort as the initial review. A second offline replay reproduced the counts.
The SEC file for December 1–15 reports a December 30 last-modified date.
The December 28 cutoff excludes that file, which was checked via HEAD only.
[Aggregate receipts and archive hashes](../ops/issue292/sec-settlement-coverage-20261127.json)

Three additional exchange sources leave the historical screen incomplete.

| Source | Availability check | Remaining gap |
| --- | --- | --- |
| [IEX HIST](https://www.iex.io/products/equities/market-data-connectivity) | IEX describes next-trading-day publication and a rolling 12-month direct history. Its [download page](https://iextrading.com/trading/market-data/index.html) currently says the PCAPs are unavailable for direct download. A request to the historical catalog returned HTTP 403. | No 2022 files or exact release receipts were verified. The feed covers trades executed on IEX, so a replay also needs to validate comparability to the intended screen inputs. [IEX feed details](https://iextrading.com/trading/alerts/2017/014/) |
| [Cboe historical volume](https://www.cboe.com/us/equities/market_statistics/historical_market_volume/) | The public 2022 files provide market-volume statistics. Cboe lists daily per-security closes and volume as an [Equity EOD Summary product](https://datashop.cboe.com/equity-eod-summary). | The public files lack the per-security bars and listing identifiers required by the replay. Access to the full historical product within the project budget remains unverified. |
| [Nasdaq Daily List](https://m.nasdaqtrader.com/Trader.aspx?id=DailyListPD) | The historical subscription describes listing changes, delistings and an option to include CUSIPs. | The subscription supplies corporate actions for Nasdaq-listed securities. It does not supply the fixed cohort's daily bars or establish zero-cost access to the CUSIP edition. |

The new matches corroborate discrete identities on settlement dates. The
verified listing intervals and as-of daily close and traded-volume series
needed for the historical replay remain outstanding.
