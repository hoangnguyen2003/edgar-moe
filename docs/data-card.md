# Data card

## Intended use

Point-in-time research on whether information in US issuer 10-K and 10-Q filings predicts beta-adjusted 20-session returns. The data is not intended for execution, personalized recommendations, credit decisions, or evaluation of individuals.

## Sources

- SEC EDGAR submissions, filing documents, and company facts.
- Alpaca US-equity asset metadata, split-adjusted daily bars, and corporate actions.
- FRED/ALFRED macro observations and historical vintages.

## Universe construction

At each month end, rank eligible NYSE, Nasdaq, and AMEX common stocks by trailing 60-session median dollar volume. Require price of at least $5 and 252 prior sessions. The broad protocol supports up to 1,000 names; `config/authenticated-free.yaml` uses a top-300 universe drawn from 500 trailing-liquidity-screened candidates. The frozen v1 candidate list was screened as of 2026-07-31, inside its locked-test period, so its universe selection used test-period information. Probable funds/ETPs and issuers without a 10-K/10-Q in the study window are excluded. Include inactive securities when CIK/symbol mapping confidence passes the configured threshold.

## Availability

- Filing text becomes available at SEC acceptance.
- XBRL values become available at their filing timestamp.
- Daily market values become available after the session completes.
- Macro values use their initial FRED/ALFRED release and its availability date.

The dataset builder stores the source timestamp beside every feature and rejects violations.

## XBRL fact selection

`features.xbrl_fact_policy` decides which company facts feed the fundamental
ratios, and each dataset records it in its identity and provenance
([ADR 0015](adr/0015-xbrl-fact-selection-policy.md)):

- `duration_aware_v2` (default for new studies, `config/authenticated-v2.yaml`)
  uses facts that end within 120 days of the filing's period. Flows must span 60
  to 380 days; the quarter is preferred over year-to-date and values are
  annualized. `Revenues` and contract revenue compete as one input.
- `legacy_v1` (`config/authenticated-free.yaml`) reproduces the frozen study
  exactly, including its known defects: quarterly and year-to-date flows are
  mixed, and a discontinued `Revenues` tag can outrank current contract revenue
  however stale it is. The prospective runner keeps it so v1 is scored on the
  features it was trained on.

## Known limitations

- Free sources do not provide a perfect historical CIK/ticker master. Corporate actions and mapping evidence reduce, but do not eliminate, survivorship and identifier bias.
- Historical short availability and realized borrow fees are unavailable; portfolio results use explicit cost sensitivities.
- Filing structures vary and some sections cannot be parsed reliably.
- Filing downloads that fail are excluded from that dataset. Prospective runs
  record recent failures as a `recent_filing_download_failures` quality warning
  because an excluded filing cannot be forecast before its entry.
- Frozen text embeddings uniformly sample at most 12 spans from long configured
  sections; they are not full-token representations of every filing.
- Free IEX bars represent one venue rather than the consolidated SIP tape.
- Free-plan historical coverage can begin later than the requested start date;
  report the observed bar range and resulting temporal split dates for every run.
- Split-adjusted daily bars exclude dividend total return and cannot model intraday slippage.
- The deployed application currently exposes only a generated synthetic fixture. This does not settle the rights status of older derived research outputs in repository files, Git history, artifacts, or prior deployments.

## Source-rights review gate

The project does not interpret a derived metric as automatically cleared for
publication. As checked on 2026-09-25, [Alpaca's redistribution answer](https://alpaca.markets/support/redistribute-alpaca-api)
says Alpaca API data cannot be redistributed. The current [FRED Services terms](https://fred.stlouisfed.org/legal/terms/)
include a broad restriction on using FRED content for software or ML development
and training; they also direct users to check third-party series rights. These
pages do not by themselves settle the status of this project's past usage or
its derived aggregates. This is a rights-review question, not a legal opinion.

Keep the new v2 pre-test reports private and the public v2 catalog at
`pending_review` until [issue #280](https://github.com/hoangnguyen2003/edgar-moe/issues/280)
records a source-by-source permitted-use determination or a replacement-data
study with a new identity. The v2 publisher also requires a source-bound,
source-by-source review record; it is intentionally absent while this decision
is unresolved. Such a record is an audit trail, not proof of provider permission.
The [source-rights inventory](source-rights-review.md) identifies the four
macro series in the local authenticated checkpoint, their noted underlying
holders, and the derived public fields needing review. Review the existing
v1-derived public snapshot under the same gate. Do not
attach credentials, raw source payloads, bars, embeddings, or private
predictions to that issue or to a release PR.

## Public snapshot and synthetic fixture

`data/demo/snapshot.json` is a generated `synthetic_fixture`. The former event-level v1 snapshot identity is preserved separately in `config/withdrawn_v1_identity.json`; its data bytes are no longer the current application snapshot. The checked-in `config/public_snapshot.lock.json` pins the synthetic fixture's bytes and metadata, and `public/data-provenance.json` publishes the same identity. CI and the Vercel build reject drift. The `edgar-moe demo` command generates fixtures for software verification, by default at `data/interim/synthetic-snapshot.json`.

Historical reports, aggregate catalogs, and Git history may still contain v1-derived values. They remain pending the source-by-source review in [issue #280](https://github.com/hoangnguyen2003/edgar-moe/issues/280); replacing the live snapshot is not a full repository takedown or legal clearance.

## Authenticated refresh bundle

`edgar-moe refresh-data` writes a dated local checkpoint containing the explicit universe, complete SEC submission histories, filing HTML, SEC company facts, split-adjusted Alpaca/IEX daily bars, corporate actions, initial-release FRED observations through the requested cutoff, and a hash manifest. Credentials are read from the environment and never serialized. The bundle remains under the ignored `data/raw/` tree; the manual workflow may retain it briefly as a private artifact but never commits or promotes it to public signals.
