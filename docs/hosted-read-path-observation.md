# Hosted read-path observation

On 2026-09-27 at 07:40 UTC, an operator sampled the public
[`edgar-moe.vercel.app`](https://edgar-moe.vercel.app) API from this workspace.
The existing [deployment smoke check](../scripts/smoke_deployment.py) first
verified the frozen snapshot lock and serving commit
`26cb0d02a71a24705675a7fdbbb33e68a6265fa9`; all 12 measured health
responses in the final sample reported that same commit. This is a bounded client
observation, **not** a production SLO, throughput test, or Postgres query plan.

The [reproducible probe](../scripts/benchmark_hosted_read_path.py) sent one
warmup and 12 measured GET requests to each of four allowlisted routes,
round-robin with concurrency one and 250 ms between requests. It used no
credentials, rejected redirects, capped response reads at 1 MiB, and retained
only response-status/cache categories and aggregate timing. It can also pin
each health sample to an expected serving commit. Its 10-second
`urllib` timeout is a socket-operation timeout, not a hard end-to-end deadline.
The [failure-injection tests](../tests/unit/test_hosted_read_path_benchmark.py)
exercise malformed responses, transport/HTTP errors, degraded semantic status,
unsafe origins, and request-budget bounds.

| Route | Edge-cache observations | HTTP 200 / measured | Successful p50 / p95 / max, ms | Semantic observation |
| --- | --- | ---: | ---: | --- |
| `/api/v1/health` | 12 MISS | 12/12 | 176.444 / 10,186.142 / 10,192.272 | 12 `ok`; serving commit matched 12/12 |
| `/api/v1/summary` | 12 HIT | 12/12 | 125.565 / 4,684.869 / 10,130.788 | Reviewed frozen snapshot |
| `/api/v1/forward/status` | 12 MISS | 12/12 | 205.108 / 10,203.280 / 10,204.305 | Registry available 12/12; health `warning` 12/12 |
| `/api/v1/forward/forecasts?limit=25` | 11 HIT, 1 STALE | 12/12 | 128.410 / 173.858 / 223.319 | Public page only; no rows retained |

The overall report status was `warning` because the forward status endpoint
reported a quality warning, **not** because an HTTP request failed or the
registry was unavailable. The probe does not infer a warning's cause from
latency data. A separate public status read at the time named
`pre_open_schedule_margin`, the actionable scheduler warning tracked in
[issue #156](https://github.com/hoangnguyen2003/edgar-moe/issues/156); it was
not the expected candidate-count warning described in the [runbook](forward-testing.md).

The 10-second outliers occurred even on a route with edge-cache hits. Two
earlier unpinned exploratory runs while the probe was being developed had
different tail behavior; they were not substituted for this commit-pinned
sample. This
measurement cannot attribute them to the client network, edge, serverless
resume, origin, or database. With only 12 measured requests per route, the
interpolated p95 is especially unstable. Do not compare these mixed-cache
figures to the [synthetic in-process SQLite benchmark](adr/0030-forward-interval-read-path.md)
or use either as a hosted SLO. The next justified step is a low-rate,
multi-day error/latency observation and a safe Postgres plan on a replica
before changing cache policy or paying to keep services warm.

To repeat this small observation against the public deployment, first run the
[identity smoke check](../README.md#quick-start), then:

```bash
uv run python scripts/benchmark_hosted_read_path.py https://edgar-moe.vercel.app \
  --expect-commit <current-deployed-40-character-sha> \
  --samples-per-route 12 --pause-ms 250 --timeout-seconds 10 \
  --output data/artifacts/hosted-read-observations/observation.json
```

Choose a new output path for each run; the script refuses overwrite. Reports
under `data/artifacts/` are ignored by Git. Review route/cache mix, all error
counts, health status, and the limited sample size before comparing runs.
