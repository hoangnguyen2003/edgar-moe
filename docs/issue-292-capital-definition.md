# Explicit issuer common-stock/capital-stock definition

An ownership cover's generic common-stock title cannot be equated to a periodic
report's capital-stock title by a global alias. The new offline supplement
requires the issuer's own pre-cutoff definitive proxy to define that particular
correspondence explicitly, at the registered par value and with a one-vote rule.
It also requires the proxy to cite an independently retained ownership report
whose common-stock holding appears in the same issuer's ownership table.

## Required evidence

`historical_capital_definition.py` authenticates the complete proxy submission's
accession, primary filename, form, filing dates, filer CIK and legal name. The
primary registrant cover must name the same issuer. The supported definition
occurs once in an annual-meeting voting question and expressly identifies its
common stock as capital stock at the registered par value. Its immediately
following rule must grant one vote per share. Named classes, voting qualifiers,
ordinary/preferred shares, absent par values and inconsistent par values cannot
use this path.

The source-qualified ownership report must have the same SEC subject issuer
and an unqualified common-stock title. Its reporter cover identity is bound to
the filed-by header through a nonzero IRS identifier. The proxy's ownership
table must immediately follow the matching class/date heading, identify the
same reporter and address, and carry a unique footnote marker. The marked
citation must match the filing date, ownership event date, aggregate holding
and all four voting/dispositive positions. Duplicate or displaced tables,
citations, headings or definitions fail closed.

Address comparison preserves all street, city, state and postal-code content.
Its only word expansion is the postal suffix `BLVD` to `BOULEVARD`, documented
in [USPS Publication 28, Appendix C1](https://pe.usps.com/text/pub28/28apc_002.htm).
It does not normalize or substitute issuer/reporter names.

Address comparison permits only case/punctuation normalization and the postal
`BLVD`/`BOULEVARD` suffix correspondence documented in
[USPS Publication 28, Appendix C1](https://pe.usps.com/text/pub28/28apc_002.htm).
It does not rewrite reporter or issuer names. Both original source payloads
and their hashes remain retained. Qualification attributes reported holdings;
it is not an independent economic audit of those holdings.

The original reported ownership title and availability remain in the proof.
The qualified title is the exact registered periodic-report title. Availability
is the latest of the original ownership evidence and the conservative proxy
availability bound; the joined anchor separately includes the periodic filing's
bound. A late source cannot inherit an earlier cover's availability.

## Independent append-only replay

`scripts/enrich_historical_capital_definition.py` adds a separate sixth stage
after the FDIC/SEC bank supplement. It first independently replays that complete
parent chain. Only unresolved ownership rows may be proposed in its private
reference plan. All subject-authenticated, parseable ownership candidates from
the existing bounded discovery must agree on the identifier, unnamed common
class and any explicitly stated par value. Foreign subjects and malformed covers
remain explicit discovery refusals; a conflicting qualified identifier cannot
be discarded to obtain a match.

The producer has no HTTP client. It uses checksum-verified private cache pairs,
preserves all prior anchors and source order, and creates a new content-addressed
`capital-definition-<sha256>.json` exclusively. `--verify` independently
recomputes that complete payload. The checkpoint preflight accepts the new stage
only with `--capital-sources` and every parent source family, and refuses that
argument for older stages. A missing current source cannot fall back to a prior
anchor count or release any study/publication gate.

## Remaining issue boundary

The retained replay adds one identity to the previous 492 while retaining the
full 503-security source: **493 verified identities, nine unresolved ownership
cases and one unresolved periodic case**. Two bounded public SEC requests were
made for discovery: one complete proxy was retained, and a second submission
exceeded the existing document byte bound. The oversized response was refused
without a retry or an enlarged allowance. There were no market-provider
requests or paid-resource changes. Source documents, issuer rows and detailed
ownership payloads remain private.

This does not establish historical market/security continuity, reconcile the
different original checkpoint cohort, or bind a new selection, checkpoint,
processed dataset and study. The original 302 literal issuer/symbol matches,
201 absent source securities and 198 checkpoint securities outside the source
are unchanged. #292 remains open. Frozen v1, original v2 data and append-only
forward records remain unchanged.

The [identity audit](../ops/issue292/capital-definition-audit-20261117.json)
and [checkpoint audit](../ops/issue292/checkpoint-capital-audit-20261117.json)
contain aggregate counts, hashes and unresolved gates. A separate 45-file
private packet retains the new source pairs, replay inputs, code and logs;
the previous 539-file packet was rehashed without changes. The complete suite
passed 2,916 tests with 36 skips and 85.99% coverage. Ruff, format and strict
typing checks passed. The actual checkpoint preflight and its independent
replay both exited 2 for a valid blocked report, not admission success. A
modified copy of the new real source was refused before any network request.
