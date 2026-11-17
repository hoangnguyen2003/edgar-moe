# Historical FDIC bank filing evidence

The [SEC's filing-search guidance](https://www.sec.gov/files/about/webmaster-faq.htm)
points users to the FDIC for some banks whose periodic reports are absent from
EDGAR. An empty SEC periodic-filing list cannot establish that such a historical
candidate had no registration. The [FDIC's public filing system](https://securitiesfilings.fdicconnect.fdic.gov/)
provides bank certificate indices, dated filing records and original attachments.
Its published client identifies public search, filing-detail and attachment
endpoints; no browser automation or authenticated account is needed.

## Source-qualified method

`scripts/enrich_historical_bank_filings.py` is an offline, append-only supplement
to the retained charter-redesignation result. It replays that complete parent
chain before considering unresolved periodic anchors. A private plan proposes a
source-row key, FDIC certificate, issuer proxy filing and ownership accession.
Those proposals confer no identity or publication authority.

The verifier independently selects the latest pre-cutoff quarterly report from
the certificate index. The full index row must match the separately captured
filing detail. Only a completed, non-confidential form and a single original PDF
attachment qualify. Reported filing, record/event changes, attachment creation
and attachment changes are retained separately. Availability uses the latest
of those dates, conservatively taking the end of date-only fields, plus fourteen
calendar days. A later electronic upload cannot inherit an earlier paper filing
date. Late or changed metadata blocks replay rather than falling back to an
older report.

The PDF decoder checks the regulator/form, named registrant, quarter end,
signature date, and a literal common-stock/ticker/exchange row in the cover's
registration table. Preferred/depositary rows stay distinct. Missing text,
ambiguous registrations, conflicting names, excessive streams/pages, encryption
and invalid PDF objects are refused. PDF and font support are research-only,
locked dependencies; all pre-existing package records and versions are preserved.
The replay binds both decoder distributions' installed Python source bytes.

A bank's SEC subject tax metadata can differ from its FDIC cover or contain a
zero placeholder. Those values are not equated or silently repaired. The
supported alternative requires the bank's own FDIC-filed proxy to cite the
exact ownership filing: reporter identity, reporter tax identifier, reporter
address, filing date, ownership event date, aggregate holding, and all four
voting/dispositive positions must match. Its common-stock table row and footnote
marker must agree. Both FDIC documents must belong to the same certificate and
named registrant. The source citation attributes reported holdings; it is not
an independent audit of their economic accuracy.

The original subject tax value and its failed comparison remain private in the
qualified proof, with `subject_tax_identifier_used_as_join_key: false`. No
family, class, voting or entity-tax aliases are introduced. All source-qualified
ownership candidates returned by the bounded discovery must agree on the
reported security identifier and class; the exact cited report must occur once.
A different candidate bank without this qualification remains unresolved.

## Replay and checkpoint boundary

```sh
python -m scripts.enrich_historical_bank_filings \
  --directory data/artifacts/private-identity-capture \
  --baseline class-redesignation-<sha256>.json \
  --references private-bank-plan.json \
  --governing-sources data/artifacts/private-legacy-sources \
  --proxy-sources data/artifacts/private-combined-proxy-sources \
  --redesignation-sources data/artifacts/private-redesignation-sources \
  --bank-sources data/artifacts/private-FDIC-responses \
  --bank-ownership-sources data/artifacts/private-bank-ownership \
  --source-capture data/artifacts/private-candidate-source \
  --retained-checkpoint data/raw/authenticated/2026-07-31
```

Replace `--baseline` and `--references` with
`--verify bank-filings-<sha256>.json` for independent replay. All source reads
are from private, bounded, checksum-verified cache pairs. There is no HTTP client
or live mode in this supplement. Output creation is exclusive and content
addressed; all earlier anchor payloads and source order must remain unchanged.

The [checkpoint preflight](issue-292-checkpoint-preflight.md) accepts the fifth
`bank-filings` stage only with its two explicit bank source directories and all
parent source directories. Earlier stages reject unused bank source arguments.
New anchors use `pre_cutoff_FDIC_SEC_identity_anchored`; original SEC-only statuses
are preserved. Combining the counts does not assert that an FDIC periodic report
was filed on EDGAR. Even complete identity counts and identical literal rosters
cannot release continuity, new-study binding, publication or issue-completion
holds. Frozen v1, original v2 and append-only forward records are preserved.

Raw bank documents, report details and identifier rows remain private ignored
artifacts. This narrow private verification does not redistribute bank filings
or establish rights for a combined market-data release. Retrieval times remain
in 2026, distinct from regulator-reported 2022 filing and availability dates;
these are not cryptographically signed historical-publication timestamps.


## Retained result, November 17, 2026

The [row-free audit](../ops/issue292/bank-filing-audit-20261117.json) records one
new FDIC/SEC anchor, all 491 preserved prior anchors and the complete 503-row
source order. The direct capture retained thirteen FDIC responses and eight SEC
ownership reports. Two initial name-query parameters were ignored by the portal;
those responses were not treated as filtered results or negative evidence.
Certificate indices and their actual filing metadata supplied the source links.
The second reviewed bank was not qualified and remains in the denominator.

The source PDFs' covers and the qualification page were rendered and visually
checked privately. The exact citation match is source attribution of reported
holdings. It does not reconcile the tax metadata, establish price/security
continuity, repair the original candidate cohort or admit a new research study.
