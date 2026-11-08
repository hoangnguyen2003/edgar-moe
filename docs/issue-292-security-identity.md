# Historical SEC security identity evidence, 2026-11-09

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
rights and common/ordinary/capital/registered distinctions must agree. Preferred, depositary,
unit and ambiguous classes are refused. Both reported acceptance times plus
seven calendar days must precede the source cutoff. Current discovery is limited
to the preceding two years, at most four periodic and 64 distinct ownership filings.
These are explicit coverage bounds, not grounds for dropping unresolved rows.

### Coverage extensions

The extractor also recognizes colon-labelled ownership covers when an explicit
issuer-name/class/CUSIP sequence is present within a short cover block. Narrative
Item 2 mentions, unbound labels, multiple classes/CUSIPs and voting/non-voting or
ordinary/common mismatches still fail closed. Complete-submission subject-CIK
and accession checks remain mandatory.

Some periodic covers reuse a context for several securities, or give the three
cover columns separate property-axis contexts. A fallback requires exactly one
class, symbol and exchange in a single table row, excluding nested rows. Every
context must identify the same issuer. Separate contexts must have identical,
valid pre-cutoff periods and only the known stock-class, trading-symbol and
listing-exchange axes; shared axes cannot disagree. Unknown/typed dimensions,
debt/class conflicts, multiple matching rows and future periods are refused.

The original ownership discovery preserved the newest five attempts. If they failed, it
inspects up to five further pre-cutoff records, prioritizing other SEC file-number
series by their pre-cutoff frequency and then recency. Series metadata is only
a discovery aid; it cannot supply issuer identity. Each accepted filing still
needs its explicit subject header. The two-year lookback and seven-day lag,
request/byte/rate caps, source retention and 503-row denominator are unchanged.

The next extension preserves that original ten-report priority and then examines
further eligible reports in descending pre-cutoff acceptance/accession order,
up to **64 distinct ownership reports per candidate**. If the initial retained
history does not yield a valid join, it can inspect that retained submission's
advertised archive pages overlapping the same two-year window. Archive discovery
is lazy: a successful initial join makes no extra archive request. Both phases
share the 64-report cap, skip already attempted accessions, retain the original
periodic anchor and require the same explicit subject/class/CUSIP checks. The
source-wide request budget, seven-day publication lag and HTTP/size refusal
controls still apply. A successful discovery is not a reason to reduce the
503-row denominator.

An oversized source no longer prevents inspection of the next filing within
those bounds. Its refusal is retained, the same byte ceiling still applies to
each alternative, and any successful fallback carries the prior refusal counts.
If no alternative proves identity, the candidate remains unresolved. Integrity
errors, exhausted request budgets, missing uncaptured sources and HTTP refusal
controls cannot use this size-only continuation path.

### Literal cover facts, retained documents and linked exhibits

Capital stock and registered shares are recognized as separate equity families;
neither is an alias for common or ordinary shares. Literal ticker quotation
marks and an explicit `(NYSE)` suffix are retained as reported typography. A
dotless class ticker can match its dotted source spelling only when its cover
title explicitly names the same class letter. No rename, issuer or voting-rights
inference supplies a missing class.

One identical registered security can have several explicit listing-exchange
contexts. Those contexts must agree on issuer, title, period and non-exchange
dimensions. The result retains every reported venue and uses a null primary
exchange; it does not invent a primary listing. A narrow coupon-bearing
`Notes due 20xx` context cannot be blended into an equity context. Issuer-specific
trading-symbol axes require a declared namespace and the exact symbol member;
unknown and typed dimensions remain refusals.

Already retained, manifest-bound filing documents can be read and checked in
full up to 64 MiB after decompression. The compressed read cap remains 32 MiB.
Live responses and ownership documents remain capped at 16 MiB. No prefix-only
parse can hide a contradictory class later in the filing.

When a primary filing explicitly delegates its Inline XBRL registration cover
to a numbered exhibit, the collector may read one exact, unique sibling HTML
link in that accession. It binds the original primary and exhibit hashes.
External URLs, path traversal, ambiguous links and recursive exhibit discovery
are refused. The exhibit still needs the same dated issuer/symbol/class proof.
Inline XBRL document sets can place their shared contexts in a header document,
as described by the [XBRL International primer, section 2.2](https://www.xbrl.org/specification/inlinexbrl-part0/pwd-2013-05-31/inlinexbrl-part0-pwd-2013-05-31.html).
For this narrow case, the collector resolves only the exhibit's exact context
IDs from the explicitly linked primary, preserving namespace declarations. It
requires a single matching primary issuer, unique context definitions, valid
pre-cutoff periods and supported dimensions. The anchor binds the context IDs
and a digest of the namespace-preserving resources, without rewriting either
captured source. Missing, redefined, conflicting or late contexts fail closed.
This is a bounded identity extractor, not a complete Inline XBRL validator.

### Explicitly incorporated stock certificates

After the ownership discovery phases fail, an annual filing among the four
eligible periodic candidates can supply an explicit specimen-certificate link.
Its leaf table row must name the same registered class, with exactly one link;
the annual cover independently confirms the same issuer, symbol and class.
The link must stay on the exact issuer's SEC archive path. Historical HTTP links
are requested over HTTPS at the identical host/path, without redirects.

The collector verifies the original complete submission's accession, one
explicit `FILER` CIK, supported 8-K/8-K12B form, filing date and exact exhibit
filename/type. The supported certificate face names that filer immediately
before the fully-paid/non-assessable class field and contains one unambiguous,
check-digit-valid labelled CUSIP. Class letters, voting qualifiers and equity
families must still match. Separate classes or unlabelled identifiers cannot be
paired by their positions. Only the exact incorporated exhibit is extracted;
there is no recursive link search or image/OCR inference.

The SEC header's local acceptance string is retained verbatim. A separate
date-only availability bound uses the reported filing date plus **14 calendar
days**, and must precede the annual index's availability and cohort cutoff.
No UTC acceptance time is invented. The evidence binds the full submission,
header, exact exhibit bytes, annual cover and index-description hashes. It is
stored separately from ownership-report evidence. Prior size refusals remain
recorded even when a certificate succeeds; integrity, request-budget and HTTP
controls still stop the run. The certificate extraction module has its own code
digest, included in both capture and replay version checks.

A specimen establishes the identifier reported in that filing. The complete
class/identifier and market continuity needed by the study remains a separate
requirement.

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
The independent replay also checks that its extraction and verifier code did
not change during verification.

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

A subsequent four-request private probe used the documented symbol-based bar
route: historical CUSIP asset lookup, inverse symbol lookup, raw IEX historical
bars and a second inverse symbol lookup. Both inverse IDs matched the CUSIP
lookup, and the bar query returned three records. Current metadata was used only
as a read resolver after historical identity was established; it did not select
or replace a cohort member. The query's `asof` was the current lookup date and
is not represented as a 2022 mapping observation. This demonstrates free read
addressability for that security, not complete historical continuity, original
price-vintage availability, or a completed dataset/study chain. All request
profiles, IDs, source rows and bars remain private; only operational counts and
hashes are suitable for the public commitment.

The [SEC's official historical-list index](https://www.sec.gov/rules-regulations/staff-guidance/official-list-section-13f-securities)
also links a [third-quarter 2022 list](https://www.sec.gov/files/investment/13flist2022q3.pdf).
Its first two pages identify third-party copyright, a redistribution restriction
and a Form 13F reporting purpose. They were read and visually inspected only to
assess source fit; no identifier rows were imported and no broader source-use or
publication clearance is asserted. The private source digest and assessment
scope are recorded without publishing its contents.

## Remaining issue requirements

### Named nonvoting class and incorporated amendment chain

The latest extension accepts one narrowly supported two-class charter format.
An eligible annual report must explicitly incorporate both the restated
certificate and its later amendment, with their original forms, dates and
exhibit numbers. Split formatting links are allowed only when every href in
the same complete reference row is identical. Additional charter references,
different links, missing sources and cross-issuer paths fail closed.

Both complete submissions authenticate their filer CIK, legal name, accession,
dates and exact exhibit filename/type. The only legal-name normalization beyond
case and punctuation is terminal `Corporation`/`Corp`. The full authorized-shares
article must state the supported Class A/Class B structure and explicit Class B
nonvoting rights, retaining statutory voting exceptions. The whole amendment
must match a share-count-only replacement of the first authorization paragraph;
different operative text, competing rights, par values, class letters or share
totals are refused. Both sources retain their raw local acceptance text and
separate filing-date-plus-14-calendar-day availability bounds.

The ownership cover must already prove the same named Class B and SEC subject,
with one consistent identifier across the authenticated candidate reports.
Only its missing nonvoting qualifier can be supplied, and the charter chain
must be available before that ownership report. The annual registration cover
independently confirms the same issuer, symbol, named class and par value.
Neither a generic class nor an explicit conflicting voting qualifier can be
rewritten. The existing two-year discovery window, report/byte bounds and
integrity refusals remain in force. The extractor's own code digest is bound
by both capture and independent replay.

Full offline capture and independent source replay now cover **all 503 rows**:
**486 verified SEC identity anchors**, **15 unresolved ownership identities**
and **two unresolved periodic anchors**. All 485 previous complete anchor
payloads and their source receipts remain identical; one anchor is added.
Result SHA-256:
`332d056bbe69e14834903ff0c634f0cc76b357f5cbae3be1e35c7da12889f208`.
The row-free [`class-charter-audit-20261109.json`](../ops/issue292/class-charter-audit-20261109.json)
binds the result, code, private evidence chain and verification logs. Both
503-row runs made zero network requests. Validation: 2,380 full-suite passes,
36 skips, 85.82% coverage and 265 focused tests; formatting, lint, typing,
documentation links, public snapshot lock and frozen runtime checks pass.

The separately retained quarterly Section 13F reference was checked against
the unresolved candidates. Its class descriptions remain discovery leads;
its exact historical publication/unchanged-byte availability is unverified.
Two bounded archive index lookups produced a missing capture and a timeout,
respectively. A separate filed holdings-table lead is retained as corroborating
research only; a reporting manager's CIK cannot stand in for an issuer's CIK.
No reference-list or holdings row supplies an adopted identity in this result.
All source contents and identifiers remain private. Seventeen identities,
historical market continuity and the new selection/checkpoint/processed-dataset/
study chain remain incomplete. The publication gate stays closed.

### Explicit voting rights in cover notes and incorporated charters

A narrow SC 13G cover format links row 11's percentage to an immediately
following `*` footnote. The complete sentence must explicitly name the
denominator's voting class, the same SEC subject legal name and valid,
pre-acceptance reference dates. Only a missing voting qualifier can be supplied;
the equity family and class letter must already agree. Multiple covers,
unmatched markers, body-section tables, conflicting rights and voting-power
numbers cannot supply this evidence. The original title, percentage, dates,
marker and footnote/header/name hashes remain in the private receipt.

A separate fallback handles one generic common-stock title whose registered
class explicitly says voting. It requires an eligible pre-cutoff annual report's
unique, complete incorporation reference to an amended and restated charter,
including its amendments-through date, original form, filing date and exhibit
number. The annual registration cover must identify the same issuer, symbol,
class and par value. The exact referenced complete submission must authenticate
its filer CIK, legal name, accession, dates, filename and exhibit type.

The charter's entire authorized-shares article must state a sole common-stock
class and exactly one vote per share. Competing classes, other common-stock or
common-share language elsewhere, additional authorization clauses, conflicting
par values and unparsed rights are refused. The dated ownership reports must
supply a single identifier; an authenticated but ambiguous ownership report
prevents this fallback. The charter must be available before the ownership
report under a separate filing-date-plus-14-calendar-day, date-only bound.
Its raw local acceptance text is retained without inventing a UTC timestamp.
The normal two-year discovery window, 64-report ownership cap and exact
common/ordinary/capital/class/voting comparisons remain in force. This is
dated issuer/class evidence, not uninterrupted security or market continuity.

The preceding cover-note/charter capture processed **all 503 candidates** with **485 verified
SEC identity anchors**, **16 unresolved ownership identities** and **two
unresolved periodic anchors**. All 483 prior anchors and their identifiers,
symbols and registered titles remain unchanged. One addition uses an explicit
cover footnote; one uses an incorporated single-class charter. No candidate was
excluded. Result SHA-256:
`1c29732a85b69f1d2c1dd1fd770805ae8bc3ae0d77d4e18a367c8523fa18d2c9`.

Independent offline replay reproduced all 503 results under the final, stricter
guard against common-stock/share language elsewhere in the charter. Capture
and replay code digests are recorded separately in the row-free
[`voting-class-audit-20261108.json`](../ops/issue292/voting-class-audit-20261108.json).
A private packet preserves and rehashes 1,384 source receipts, including five
size refusals, and their available bodies. A bound follow-up retains the strict
replay, exact current code and verification logs; the earlier capture and its
code remain unchanged. Both the full capture and independent replay used zero
network requests. The governing-exhibit discovery used two SEC reads.
Validation: 2,357 full-suite passes, 36 skips, 85.79% coverage and 242 focused
identity/certificate/governance/capture/replay tests; lint, formatting, type
checks, the public snapshot lock and frozen v1 runtime validation pass.

Two further official public FDIC ownership covers supply consistent dated
class/identifier candidate evidence for one bank case. The portal reports
2012/2013 attachment dates, outside the existing two-year discovery window.
The prior SEC-subject conflict and historical continuity are unresolved; these
documents add no verified anchor. The row-free
[`fdic-ownership-assessment-20261108.json`](../ops/issue292/fdic-ownership-assessment-20261108.json)
binds the private documents, complete extracted text and cover inspections.
All identifiers and source rows remain private. That capture left eighteen
identities unresolved; the later extension above leaves seventeen. The new
historical selection/checkpoint/processed-dataset/study chain also remains
incomplete; publication holds remain in force.

### Adjacent issuer-field class evidence and remaining refusals

Some colon-labelled ownership covers put a literal `Class B` suffix in the
adjacent issuer-name field while their security-title field says only common
stock. The extractor now binds that suffix only when the rest of the name
matches the complete submission's explicit subject legal name, ignoring case,
spacing and punctuation. A class-like word already in the legal name supplies
no security qualifier. Missing subject names, conflicting classes, multiple
issuer fields and unsupported suffixes fail closed. A later cover cannot borrow
an earlier cover's issuer field. Original cover fields and subject-header/name
hashes are retained. Voting rights and equity-family distinctions still must
agree; ordinary, common and capital stock are not aliases.

Full capture and independent offline replay again processed **all 503
candidates** with zero network requests: **483 verified SEC identity anchors**,
**18 unresolved ownership identities** and **two unresolved periodic anchors**.
This is **no net coverage gain**: 482 prior anchors remain unchanged, one case
is newly verified and one prior generic-class match is now unresolved because
the ownership issuer field explicitly names a class. None of its four eligible
periodic covers resolves that ambiguity. No candidate was removed. The new
result SHA-256 is
`f6efed182dadc8c0b92dae3f695eb149a12dd21a9dc415879244a6724dc22026`.

A separate bounded older-filing probe completed five of seven planned case
receipts and inspected 45 reports using 51 SEC reads. One same-identifier,
explicit-class match remains unadmitted candidate evidence. The probe stopped
on 35 accessions whose retained and archive acceptance timestamps differ by
five hours. One additional SEC read authenticated an original submission whose
raw clock text matches the retained metadata, but this does not establish the
timezone interpretation. Neither timestamp normalization nor anchor promotion
was applied. The incomplete probe is not evidence of continuous identity.
An already retained S-1 certificate also supplies no new anchor: its identifier
field is blank or a placeholder, so certificate acceptance was not extended.

The row-free
[`issuer-field-class-audit-20261108.json`](../ops/issue292/issuer-field-class-audit-20261108.json)
binds the result, exact extraction/replay code and private verification logs.
A separate private packet preserves and rehashes 1,382 source receipts,
including five size refusals, associated available bodies, prior/current
results, exact code and diagnostic receipts. Earlier packets remain unchanged.
Validation: 2,309 full-suite passes, 36 skips, 85.74% coverage and 194 focused
identity/certificate/capture/replay tests; lint, formatting and type checks pass.
Twenty identities, historical market continuity and a new
selection/checkpoint/processed-dataset/study chain remain incomplete. No market
provider requests, paid resources or publication-gate promotion were used.

The previous ownership continuation processed and independently replayed **all
503 candidates**: **483 verified SEC identity anchors**, **18 unresolved ownership
identities** and **two unresolved periodic anchors**. Every prior 481 anchor and
issuer/security identifier was retained. A valid class-specific ownership report
was found beyond the original ten-report discovery cap, and an explicitly
incorporated certificate supplied another missing class identifier. The bounded
exploration used 74 SEC reads: 43 ownership reports, two archive pages, 19 proxy
submissions, seven annual filings, two linked certificates and one original
certificate submission. The archive pages added no qualifying records. Sixteen
complete proxy submissions supplied no CUSIP/known-identifier mentions; three
were refused at the response-size cap. Final full capture and
independent replay used zero network requests. The result SHA-256 is
`0a52e6240af7186d179faf4fbe415519a2156c0cbeae5ad09ab321c20b8dade7`.

The row-free
[`extended-ownership-audit-20261108.json`](../ops/issue292/extended-ownership-audit-20261108.json)
binds the exact result, code versions and private source packet. It preserves
1,324 source receipts and their associated bodies, prior/intermediate/final
results, exact code, replay report and test log. Full validation
passed 2,298 tests, with 36 skips and 85.72% coverage; 183 focused tests, repository
lint/formatting and type checks pass. The 20 unresolved cases, historical market
continuity and new selection/checkpoint/processed-dataset/study chain remain
incomplete. No candidate was excluded, no publication gate was promoted, and no
market-provider requests or paid resources were used.

The previous extension processed and independently replayed **all 503 candidates**:
**481 verified SEC identity anchors**, **20 unresolved ownership identities**
and **two unresolved periodic anchors**. All 466 previous anchors and their
issuer/security identifiers were retained. No row was excluded or replaced
using present-day listing status. The result SHA-256 is
`d4f51de909712c361bbf879ae0ea4c07f818c595f778d982594b99dfcfa666e0`.
The identity-extension passes used 46 additional SEC reads in total; the final
full capture and independent replay each used zero network requests. No market
provider requests or paid resources were used by these extensions.

The row-free
[`remaining-identity-audit-20261108.json`](../ops/issue292/remaining-identity-audit-20261108.json)
binds the exact capture and replay code versions separately. A final guard
against mutually inconsistent shared contexts was added after capture; full
offline replay reproduced the original result under that stricter code. A
separate private evidence packet preserves and rehashes 1,273 source receipts
and their associated bodies, the prior/intermediate/final results, exact code
versions, replay report and test log. Earlier captures were not rewritten.
Validation: 2,260 full-suite passes, 36 skips, 85.70% coverage and 145 focused
identity/capture/replay tests; repository lint, formatting and type checks pass.

Two periodic-source cases were investigated through the FDIC's official public
securities filing portal. Public quarterly reports establish a possible source
route, but their issuer details conflict with the retained SEC subject metadata.
Two public proxy reports supplied no identifier mentions in their extracted
text. Neither case was forced into a CIK/CUSIP join or counted as verified. The
public client's actual flag semantics were checked before the bounded public
downloads; confidential attachments were not requested. The row-free
[`fdic-source-assessment-20261108.json`](../ops/issue292/fdic-source-assessment-20261108.json)
records hashes and refusal outcomes. Source documents, extracted text, issuer
details and request receipts remain private. Finding a public report is not
evidence that the missing security join or publication permission is established.

The prior coverage extensions produced **466 anchors across all 503 candidates**, with
all 433 baseline anchors retained. The remaining 37 cases comprise 15 unresolved
ownership identities, 21 unresolved periodic anchors and one size-bound source.
Two of the prior three size-bound cases yielded valid alternative filings; this
last pass used nine additional SEC reads. All source rows remain private and no
candidate was dropped. The result SHA-256 is
`07699851b0b38c498ebd7558117fb67ccde33048e43c4c8f9409fc3b4610d9ae`.
Full independent offline replay reproduced all 503 results with zero provider
requests. The row-free
[`full-coverage-audit-20261107.json`](../ops/issue292/full-coverage-audit-20261107.json)
binds the result and exact capture/replay code digests. Source pairs, the final
result, prior result and exact code versions were retained in a separate private
evidence packet; no earlier capture was rewritten. This result remains source
evidence, not historical market-continuity or checkpoint/dataset completion.

The earlier subject-header audit processed and independently replayed
**all 503 candidates**: 433 explicit subject-verified anchors, 41 unresolved
ownership identities, 25 unresolved periodic anchors and four size-bound source
refusals. The live run made 1,107 paced SEC requests; offline replay made zero.
No candidate was removed. The checked-in
[`full-subject-audit-20261107.json`](../ops/issue292/full-subject-audit-20261107.json)
binds the complete private result and original capture/verifier code digests.
The result SHA-256 is
`00f085f9f8de5f9f7dab9414ccf72d845b5e8e3a3ea7387f24b07533681b0cef`.
Its 70 unresolved candidates prevented a complete historical security master
claim; 17 remain after the latest extensions. Submissions about other issuers
were actually encountered and refused;
104 subject-mismatch attempts are retained among unresolved ownership cases.
Earlier primary-document-only anchor counts below are historical progress,
not evidence that satisfies the strengthened subject gate.

The capture's code is retained separately from a subsequent receipt-consistency
fix: a newly oversized live source now records the same refusal receipt that
offline replay reads. That fix does not change identity extraction or rewrite
the completed capture.

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
