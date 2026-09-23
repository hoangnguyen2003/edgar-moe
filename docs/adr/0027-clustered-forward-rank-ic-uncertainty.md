# ADR 0027: Withhold live rank-IC intervals until time clusters exist

- Status: accepted
- Date: 2026-09-23
- Deciders: project maintainer

## Context

The public forward test has one pooled Spearman rank IC over settled forecasts.
Its first interval used a Fisher/Bonett–Wright approximation that treated
filings as independent. On 2026-09-23, 24 forecasts had matured, but all 24
were accepted in one calendar month. Same-day filings share market conditions,
and 20-session outcomes overlap across adjacent filing dates. Twenty-four
rows therefore cannot be interpreted as 24 independent time observations.

The v1 locked study is immutable. This decision changes only the uncertainty
display for append-only prospective outcomes; it does not change forecasts,
labels, their pooled point estimate, or model selection.

## Decision

- Retain the existing pooled Spearman point estimate and counts.
- The public API reports `null` interval bounds with an explicit reason until
  at least 100 settled pairs cover 12 distinct UTC acceptance months. The
  browser then says why the interval is pending instead of substituting a
  pair-independent approximation.
- Once eligible, draw 1,000 seeded, overlapping **two-calendar-month moving
  blocks**. All filings in one month move together; empty months keep their
  calendar positions. Report percentile 2.5%/97.5% bounds, the method, month
  count, block length, resample count, and a machine-readable status. If the
  rank statistic or too many bootstrap draws are undefined, report no bounds.
- Do not run more than 1,000 replicates, accept over 5,000 settled pairs, or
  span over 120 calendar months on this public read path without a capacity
  review. Reuse at most eight
  unchanged cohorts in a warm function; edge-cache public registry reads for
  60 seconds as decided in [ADR 0026](0026-edge-cached-reads.md).

The moving-block design follows [Künsch (1989)](https://projecteuclid.org/journals/annals-of-statistics/volume-17/issue-3/The-Jackknife-and-the-Bootstrap-for-General-Stationary-Observations/10.1214/aos/1176347265.full).
The 12-month threshold is a conservative product gate, not a theorem that 12
months make the estimator powerful or stationary.

## Alternatives considered

- **Keep the independent-pair interval with a caveat.** It provides a number
  immediately but looks more precise than the one-month evidence warrants.
- **Average per-run cross-sectional ICs.** Runs contain only one to four
  filings, so this changes the estimator into mostly undefined or discrete
  noise.
- **Materialize a new metric row on every settlement.** It can remove
  computation from public reads, but adds migrations, a backfill, and another
  immutable evidence boundary before the current workload justifies them.
- **Use the research stack's NumPy/SciPy bootstrap in the API.** This widens
  the lightweight serverless dependency and cold-start boundary for one
  statistic. The bounded standard-library implementation keeps it separate.

## Consequences and limits

The public site may show a point estimate without an interval for roughly a
year of prospective observations. That is honest: the point is a running log,
not a detected edge. Even when bounds appear, two-month blocks only address
some contemporaneous and adjacent-month dependence. They do not correct for
long regime shifts, repeat issuers, candidate selection, or future drift.

A local synthetic benchmark of the full 1,000-resample method on a Mac showed
uncached times of about 0.24 s at 500 pairs, 1.7 s at 3,000, and 2.9 s at
5,000; a warm process-cache hit was under 0.01 s. These are design inputs, not
hosted latency guarantees. A local SQLite/FastAPI integration call including
120 persisted forecasts and labels completed in about 0.15 s; it is a contract
and cost baseline, not a managed-Postgres cold-start measurement. Review the
boundary if settled pairs exceed 2,500,
public p95 latency exceeds its eventual service objective, or before lifting
the 5,000-pair cap. At that point, settlement-time materialization is the
preferred next option; do not silently reduce draws or drop old observations.

## Verification

`tests/unit/test_forward_uncertainty.py` covers cluster integrity, sparse
months, finite input, degenerate scores, seeded recomputation, append-only
cache invalidation, and the capacity boundary. Registry and API tests assert
that the pooled point estimate survives while bounds are withheld for sparse
history. Web tests assert both ready and insufficient-month states. The
public-bundle build and deployment smoke remain release gates. Hosted latency
and the actual first eligible interval must be observed before treating this
as an operational performance SLO or a validated market inference.
