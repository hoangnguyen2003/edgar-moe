# Project review — September 18, 2026

## Verified live state

Read-only observations from the deployed forward status and performance APIs:

- 47 recorded forecasts; 23 settled outcomes; 24 pending outcomes.
- 61 runs; latest successful run finished September 17 at 12:47:20 UTC.
- Registry health `ok`; latest quality warnings and failures both zero.
- Official coverage: 48.94%.
- Rank IC: -0.15727; RMSE: 0.10704; MAE: 0.07780.
- Directional accuracy: 56.52%.

These are an early sample of official 20-session outcomes, not evidence of
positive ranking performance. Metrics can change as additional outcomes settle.
The source endpoints are `/api/v1/forward/status` and
`/api/v1/forward/performance` at https://edgar-moe.vercel.app.

Local main was clean at `23ba414` before this review. The unauthenticated GitHub
workflow API returned 404; individual job logs and remote branch state could not
be verified through that endpoint.

## Diagnostic issue and implemented correction

The dataset builder fetched SPY prices but discarded its daily return components
because SPY was not in the stock issuer mapping. The diagnostic requires SPY
returns for every matured abnormal return. This explains a concrete path to the
earlier repeated unmatched results; the original production bundles are not
available locally to attribute every unmatched row to that cause.

New builds retain SPY, use a new builder identity, and report per-forecast missing
data reasons. Coverage uses all forecasts as its denominator. A data gap with no
evaluated outcomes is explicitly reported as insufficient coverage. A fixture
integration test verifies source refresh through dataset serialization and
diagnostic evaluation, comparing the five-session outcomes to independently
computed dataset targets.

## Next operational step

Push the reviewed correction and start a new forward workflow on main, or let its
next scheduled run build a fresh dataset. Download the newly produced diagnostic
and inspect `unmatched_reasons`, observations, and coverage. Rerunning the report
against an old processed bundle alone cannot restore its omitted SPY returns.

Any remaining unmatched rows should be investigated using their reported IDs and
reasons. Keep the official model frozen while collecting more prospective data.
The official settlement path computes targets from raw bars and already has 23
settled outcomes; this correction does not rewrite those outcomes.
