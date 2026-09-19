# Data card

## Intended use

Point-in-time research on whether information in US issuer 10-K and 10-Q filings predicts beta-adjusted 20-session returns. The data is not intended for execution, personalized recommendations, credit decisions, or evaluation of individuals.

## Sources

- SEC EDGAR submissions, filing documents, and company facts.
- Alpaca US-equity asset metadata, split-adjusted daily bars, and corporate actions.
- FRED/ALFRED macro observations and historical vintages.

## Universe construction

At each month end, rank eligible NYSE, Nasdaq, and AMEX common stocks by trailing 60-session median dollar volume. Require price of at least $5 and 252 prior sessions. The broad protocol supports up to 1,000 names; `config/authenticated-free.yaml` uses a top-300 universe drawn from 500 trailing-liquidity-screened candidates. Probable funds/ETPs and issuers without a 10-K/10-Q in the study window are excluded. Include inactive securities when CIK/symbol mapping confidence passes the configured threshold.

## Availability

- Filing text becomes available at SEC acceptance.
- XBRL values become available at their filing timestamp.
- Daily market values become available after the session completes.
- Macro values use their initial FRED/ALFRED release and its availability date.

The dataset builder stores the source timestamp beside every feature and rejects violations.

## Known limitations

- Free sources do not provide a perfect historical CIK/ticker master. Corporate actions and mapping evidence reduce, but do not eliminate, survivorship and identifier bias.
- Historical short availability and realized borrow fees are unavailable; portfolio results use explicit cost sensitivities.
- Filing structures vary and some sections cannot be parsed reliably.
- Frozen text embeddings uniformly sample at most 12 spans from long configured
  sections; they are not full-token representations of every filing.
- Free IEX bars represent one venue rather than the consolidated SIP tape.
- Free-plan historical coverage can begin later than the requested start date;
  report the observed bar range and resulting temporal split dates for every run.
- Split-adjusted daily bars exclude dividend total return and cannot model intraday slippage.
- Public snapshots contain derived values only and are not a redistribution of raw market data. The deployed bundle exposes `data-provenance.json`, which records the source families plus the content-addressed snapshot identity; the manifest keeps redistribution review explicitly pending.

## Public snapshot and synthetic fixture

`data/demo/snapshot.json` contains derived output from the frozen authenticated study and is marked `authenticated_locked_test`. It contains no credentials, raw filings, source bars, embeddings, or model checkpoint. The checked-in `config/public_snapshot.lock.json` pins its bytes and frozen metadata identity; `public/data-provenance.json` publishes the same identity without raw data, and CI plus the Vercel build reject drift between those records. The `edgar-moe demo` command can generate an explicitly marked `synthetic_fixture` at a separate path for software verification; it must not replace the frozen public snapshot.

## Authenticated refresh bundle

`edgar-moe refresh-data` writes a dated local checkpoint containing the explicit universe, complete SEC submission histories, filing HTML, SEC company facts, split-adjusted Alpaca/IEX daily bars, corporate actions, initial-release FRED observations through the requested cutoff, and a hash manifest. Credentials are read from the environment and never serialized. The bundle remains under the ignored `data/raw/` tree; the manual workflow may retain it briefly as a private artifact but never commits or promotes it to public signals.
