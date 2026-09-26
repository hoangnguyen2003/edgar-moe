# ADR 0030: Profile and pre-sort the forward interval read path

Date: 2026-09-26. Status: accepted.

## Context

The public performance route reads immutable settled pairs and reports a
calendar-clustered 1,000-draw rank-IC interval only after the [ADR 0027](0027-clustered-forward-rank-ic-uncertainty.md)
evidence gate. Its existing bounded process cache makes repeated reads cheap,
but each new append-only label invalidates the cache. The first origin read
then sorts the resampled score and label arrays on every draw. Edge caching
does not remove that first origin cost.

The credential-free [read-path benchmark](../../scripts/benchmark_forward_read_path.py)
seeds a temporary SQLite registry with deterministic synthetic forecasts in
12 calendar months, uses the real FastAPI routes in-process, and probes both
warm and explicitly cold interval reads. At 1,200 forecasts and 900 settled
pairs, the original cold interval route had a 465.449 ms median over five
sequential requests. Other warm read routes were low single-digit milliseconds
in that local environment. See the [retained measurement](../../reports/forward_read_path_benchmark.json).

## Decision

Keep the 1,000-draw, two-month block design and the same capacity/evidence
gates. Sort score and label values into tie groups once per uncached interval.
For each draw, count how many times each original observation was resampled;
compute the ranks and Pearson correlation with those multiplicities. This is
mathematically the Spearman correlation of the expanded resample, including
ties and duplicate month draws. Retain the existing bounded cache and 60 s
edge policy. A randomized test compares the weighted calculation to expanded
resampling, and a full seeded-bootstrap test compares interval percentiles.

On the same synthetic workload, the changed cold route had a 262.567 ms median
over five requests, a 43.59% reduction in the two measured runs. Warm-route
median was essentially unchanged (3.526 vs 3.500 ms). These are local
measurements, not a hosted latency guarantee or statistical efficacy result.

## Alternatives considered

- Reduce draw count: faster but changes interval Monte Carlo precision.
- Add NumPy or native C++ to the production API: adds packaging and cold-start
  complexity without evidence that it beats this standard-library change
  end-to-end. The [native-code gate](../worldquant-role-evidence.md#native-code-decision-gate)
  remains unmet.
- Precompute a mutable interval table: complicates append-only provenance and
  cache invalidation for a statistic already bounded to 5,000 pairs.

## Consequences and verification

This reduces cold CPU work but not Postgres, network, or serverless startup
time. The benchmark cannot justify a production SLO or a claim of scale under
concurrent traffic. Operators can rerun:

```bash
.venv/bin/python scripts/benchmark_forward_read_path.py --rows 1200 --samples 20
.venv/bin/python scripts/benchmark_forward_read_path.py --rows 4800 --samples 5
```

Do not compare results from different hosts as if they were controlled. For a
hosted claim, collect a separate, authorized seven-day read-only latency/error
baseline and a Postgres `EXPLAIN (ANALYZE, BUFFERS)` on a safe replica; never
point this synthetic benchmark at production. The tests do not require source
data, market data, network access, or provider keys.
