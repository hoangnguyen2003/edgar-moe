# Source-rights decision inventory

Status: **unresolved**, reviewed 2026-09-28. This is a technical inventory and
list of questions for a provider or qualified rights reviewer, not a legal
opinion or permission to use, train on, store, or publish any source content.
Keep issue [#280](https://github.com/hoangnguyen2003/edgar-moe/issues/280) open.

## Exact research inputs to review

The local authenticated checkpoint manifests dated 2026-07-31 and 2026-08-06
both list `BAA10Y`, `DFF`, `DGS10`, and `VIXCLS`. The `refresh-data` CLI has the
same default list; a future run can override it, so the rights review must be
bound to the **actual** source manifest and dataset ID, not just these defaults.
The pipeline obtains FRED/ALFRED initial-release vintages, Alpaca/IEX adjusted
daily bars and corporate actions, and Alpaca asset metadata. This inventory
does not reproduce source values, bars, model outputs, or credentials.

| Series | FRED page identifies | Rights question still open |
| --- | --- | --- |
| [`VIXCLS`](https://fred.stlouisfed.org/series/VIXCLS) | Chicago Board Options Exchange; the series notes carry Cboe copyright and say it is reprinted with permission. | Does the data holder allow the exact research/ML, historical storage, derived-output, and public-use scopes here? FRED display permission is not our permission. |
| [`BAA10Y`](https://fred.stlouisfed.org/series/BAA10Y) | Federal Reserve Bank of St. Louis computes the spread from Moody's Baa corporate-bond yield and a 10-year Treasury series; the notes include a Moody's proprietary notice. | Determine Moody's and any other underlying-holder requirements for the exact uses; the computed spread is not assumed free of upstream restrictions. |
| [`DFF`](https://fred.stlouisfed.org/series/DFF) | Board of Governors of the Federal Reserve System; FRED tags the series “Public Domain: Citation Requested.” | Confirm source notice and permitted acquisition/vintage-storage route. A source label does not override FRED API terms. |
| [`DGS10`](https://fred.stlouisfed.org/series/DGS10) | Board of Governors of the Federal Reserve System; FRED tags the series “Public Domain: Citation Requested.” | Confirm source notice and permitted acquisition/vintage-storage route. A source label does not override FRED API terms. |

The current [FRED Services and API terms](https://fred.stlouisfed.org/legal/terms/)
explicitly address software/ML development, cached or archived API content,
third-party series rights, and a notice for applications using the API. Their
application to past runs, model-derived aggregates, and a public portfolio
site requires a qualified determination; do not infer clearance from the
series source label. [Alpaca's redistribution answer](https://alpaca.markets/support/redistribute-alpaca-api)
says its API data cannot be redistributed. It does **not** answer, by itself,
whether this project's derived scores, per-event realized returns, metrics,
plots, and public API are permitted; obtain that answer rather than treating
“not raw bars” as sufficient.

## Public-output exposure to review

This is an exposure inventory, not a rights conclusion. The frozen
`data/demo/snapshot.json` contains event-level identifiers, model scores and
ranks, realized abnormal returns, attributions, predictive metrics,
portfolio scenarios, and equity curves. The built public site serves those
derived fields; the forward API additionally serves event-level forecast rows
and performance aggregates. The snapshot is content-locked, but being immutable
does not constitute permission to publish it. `public/data-provenance.json` currently
states `legal_approval: false` and `redistribution_status:
operator_review_required`; a notice does not resolve the underlying use.
The private v2 pretest report remains outside the public catalog, whose status
is `pending_review`. No provider rows, raw bars, or private v2 model outputs
should be attached to a public PR or issue.

Before changing any public output, inventory its fields and dependency chain
against the exact locked dataset and ask whether (1) event-level derived
returns/scores, (2) aggregate metrics and charts, and (3) the API and static
snapshot have different permissions. Review whether the existing v1 public
snapshot needs withdrawal, redaction, replacement, or added notices; make no
silent claim that a derived number is automatically licensed.

## Decision record required before publication

For each exact source and underlying holder, retain a dated, source-specific
written determination or approved replacement-data plan covering:

1. Private historical download, vintage storage/cache, research and ML use,
   including past use and ongoing prospective cycles.
2. Public display of event-level derived values versus aggregate metrics and
   charts, and whether an unauthenticated API changes the answer.
3. Historical candidate-master availability and identifier/listing intervals
   needed by [#292](https://github.com/hoangnguyen2003/edgar-moe/issues/292).
4. Attribution, copyright notice, API notice, retention, and deletion duties.
5. The exact dataset ID, source-manifest SHA-256, reviewed terms/version/date,
   reviewer, scope, evidence reference, and decision. Do not store credentials,
   raw source payloads, or private predictions in the public record.

The [v2 publisher](../scripts/publish_v2_research_evidence.py) already refuses
to stage aggregates without a source-bound review record. That record is a
workflow guard, **not** proof of permission. If a source is disallowed or no
determination is available, replace it with an approved source, produce a new
dataset and study identity, rerun the development analysis, and review the
existing public snapshot separately. Until then, do not promote v2, claim
historical universe membership, or use the present inventory as a clearance.
