# Data card

## Intended use

Point-in-time research on whether information in US issuer 10-K and 10-Q filings predicts beta-adjusted 20-session returns. The data is not intended for execution, personalized recommendations, credit decisions, or evaluation of individuals.

## Sources

- SEC EDGAR submissions, filing documents, and company facts.
- Alpaca US-equity asset metadata, adjusted daily bars, and corporate actions.
- FRED/ALFRED macro observations and historical vintages.

## Universe construction

At each month end, rank eligible NYSE, Nasdaq, and AMEX common stocks by trailing 60-session median dollar volume. Require price of at least $5 and 252 prior sessions. Select up to 1,000 names. Include inactive securities when CIK/symbol mapping confidence passes the configured threshold.

## Availability

- Filing text becomes available at SEC acceptance.
- XBRL values become available at their filing timestamp.
- Daily market values become available after the session completes.
- Macro values use the ALFRED vintage known on the relevant date.

The dataset builder stores the source timestamp beside every feature and rejects violations.

## Known limitations

- Free sources do not provide a perfect historical CIK/ticker master. Corporate actions and mapping evidence reduce, but do not eliminate, survivorship and identifier bias.
- Historical short availability and realized borrow fees are unavailable; portfolio results use explicit cost sensitivities.
- Filing structures vary and some sections cannot be parsed reliably.
- Adjusted daily bars cannot model intraday slippage around filing events.
- Public snapshots contain derived values only and are not a redistribution of raw market data.

## Bundled fixture

`data/demo/snapshot.json` is generated from synthetic companies and explicitly marked `synthetic_fixture`. It validates software behavior only.

## Authenticated refresh bundle

`edgar-moe refresh-data` writes a dated local checkpoint containing the explicit universe, SEC submissions, SEC company facts, adjusted Alpaca/IEX daily bars, FRED observations as known at the requested cutoff, and a hash manifest. Credentials are read from the environment and never serialized. The bundle remains under the ignored `data/raw/` tree; the scheduled workflow may retain it briefly as a private artifact but never commits or promotes it to public signals.
