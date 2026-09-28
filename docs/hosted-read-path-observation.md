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
The initial table predates schema v2; its cache categories were observed but
its percentile column pooled them. The current probe keeps cache-separated
successful percentiles and bounded per-request evidence without bodies.
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

## Daily, low-rate collection

The [daily GitHub Actions observer](../.github/workflows/hosted-read-observation.yml)
also supports manual dispatch. It makes one warmup plus eight measured GETs per
route: **36 requests total**, sequential, with at least 500 ms between requests.
It targets only the public production origin, uses no secrets or writer DB
credential, rejects redirects, caps bodies at 1 MiB, and pins every health
sample to the workflow's main commit. A degraded result fails the job but the
redacted JSON is uploaded for diagnosis. Artifacts are retained for 30 days;
on a public repository, assume anyone can inspect them. GitHub schedules can
run late or be skipped, so inspect the date coverage rather than assuming a
daily sample exists. No paid service or keep-warm loop is introduced.

After seven actual consecutive UTC dates, download the JSON artifacts to a
local ignored directory and summarize them, for example:

```bash
gh run list --workflow hosted-read-observation.yml --limit 14
gh run download <run-id> --dir data/artifacts/hosted-read-observations/<run-id>
python3 -m scripts.summarize_hosted_read_observations \
  data/artifacts/hosted-read-observations/*/*/*.json \
  --output data/artifacts/hosted-read-observations/seven-day-summary.json
```

Download each relevant run ID separately. The [summarizer](../scripts/summarize_hosted_read_observations.py)
fails its CLI until there are seven **consecutive UTC dates**, rejects mixed
origins and unpinned/malformed reports, counts errors separately, and pools
successful timings by observed edge-cache category. It records commit cohorts
but does not pretend different deployments are equivalent. A full week has
**not yet been observed**. Even after it has, a client-side `MISS` cannot
separate a warm origin from a serverless cold start or an idle Postgres resume;
that requires provider evidence. No hosted p95/p99 threshold or capacity/SLO
claim should be set from this sparse sample alone. Stop or reduce collection
if failures, quota impact, or unexpected traffic appear.

The [first workflow observation](https://github.com/hoangnguyen2003/edgar-moe/actions/runs/36328346856)
completed on 2026-09-27 at 15:07 UTC against commit
`46e0d86fe643af0e991283f9c5653b8fdd532583`. All 32 measured requests returned
HTTP 200 and passed their probe checks; the forward registry was available in
all eight samples and reported a quality warning. The eight health samples
matched the expected commit. Successful p50 timings were 83.772 ms for summary
(8 HIT), 87.040 ms for forecast page (8 HIT), 279.067 ms for health (8 MISS),
and 297.635 ms for forward status (8 MISS). These are one day's observations
from a GitHub runner. The downloaded artifact passed the summary parser, which
correctly returned `seven_day_coverage: false` and exit status 1.

## Origin-process timing in later observations

The API now emits a bounded `X-EDGAR-Read-Timing` response header with
`app_header_ms`, a `worker=first|subsequent` marker, and—on the observed
forward read routes—`registry_read_ms`. These values are numeric or fixed
labels only: no request, user, row, query, connection, or provider identity is
exposed. `app_header_ms` measures app request handling until response headers
are ready, **not** end-to-end client latency or the entire streamed response.
`registry_read_ms` includes connection acquisition, SQL, and Python work; it
must not be described as database-only time. `first` means the first request
handled by that app process, not independently verified Vercel cold start.

Schema-v3 daily observations retain validated timing only on `MISS` or
`BYPASS` responses. The observer discards the header on `HIT`, `STALE`, or
unknown edge states because a shared cache can replay an earlier origin
header. It retains only fixed status labels and bounded numbers, not the raw
header. The multi-day summarizer accepts both legacy schema-v2 observations
and schema-v3 observations; legacy samples remain explicitly unattributed.
Schema v3 retains the four credential-free warmup observations **separately**
from the 32 measured requests, so a first app-process request is not lost
just because it occurred during warmup. The multi-day summary also separates
those two cohorts. This permits separate client-latency distributions for
first and subsequent observed app-process requests, with their sample counts.
It does **not**
isolate network, edge, platform cold-start initialization, or idle database
resume. Provider-side function and database logs are still required for that
attribution, and no SLO follows from these sparse samples.

## Provider-side database plan capture

The [bounded plan tool](../scripts/capture_postgres_read_plans.py) is a manual
diagnostic for issue #290, not a deployment step. Run it only with the already
verified, dedicated **SELECT-only** Postgres reader URL on the provider's direct
host, or an isolated replica with equivalent grants. Do not supply the writer
or migration URL. The tool first runs the reader-role privilege audit, then
sets the session transaction read-only, a 3-second statement timeout, and a
500-ms lock timeout. Its fixed queries mirror the status count/latest-run,
forecast page/ticker filter, and settled performance-pair reads. It selects a
high-frequency ticker for the filter but neither prints nor stores that value.
The query budget is six EXPLAINs at most; no concurrent load is generated.

After loading `EDGAR_MOE_REGISTRY_READ_DATABASE_URL` from a private secret
store into the environment, run:

```bash
uv run python -m scripts.capture_postgres_read_plans \
  --output data/artifacts/hosted-read-observations/postgres-plans-YYYY-MM-DD.json
```

Do not paste the URL in an issue, log, or command history. The tool only writes
under the git-ignored `data/artifacts/` tree, refuses overwrite, creates the
report with mode `0600`, and emits only allowlisted numeric plan/timing fields
plus node types. It discards raw plan JSON, SQL parameters, query rows,
role/database identities, and connection strings. Inspect the local report
before citing any aggregate in a public issue. Compare table counts with the
hosted registry and note that a small current dataset is not a scale test.
CI exercises the capture against a disposable empty Postgres registry to
validate the safety path; this is **not** a representative hosted plan.

Stop if the reader-role audit fails, a query times out, provider load rises,
or the captured counts do not represent the workload under review. No plan
has yet been captured from the hosted provider, and this diagnostic cannot
attribute client-side tails to a warm origin, serverless cold start, or idle
database resume. Keep the daily public observer running only while it remains
low-cost and healthy; wait for seven actual consecutive UTC samples before
setting any threshold or claiming an SLO.
