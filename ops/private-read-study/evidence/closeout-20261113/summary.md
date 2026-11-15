# Issue 290: hosted read observation

Collection status: **incomplete**. Fixed window: 2026-11-06T08:15:00+00:00 through 2026-11-13T08:15:00+00:00.

Observed 12/168 scheduled pairs and 24/336 modeled requests; 0 HTTP/contract errors. Manual setup runs are excluded.
Actual observations cover 3 UTC dates, from 2026-11-06T09:53:21.605955+00:00 through 2026-11-08T23:09:01.019490+00:00 (pair-start span: 220539.413535 seconds). The policy window elapsing does not fill missing observations.
Platform trace statuses: {'passed': 23, 'refused': 1}; degraded pairs: 1; workflow conclusions: {'failure': 1, 'success': 11}. These are separate from HTTP/contract errors.
Missing slots: [0, 2, 3, 4, 5, 6, 7, 8, 10, 11, 12, 13, 14, 16, 17, 19, 20, 21, 22, 23, 24, 25, 27, 28, 29, 30, 31, 33, 34, 35, 37, 38, 40, 41, 42, 43, 44, 45, 47, 48, 49, 50, 51, 52, 53, 55, 56, 57, 59, 60, 61, 63, 64, 65, 66, 67, 68, 69, 70, 71, 72, 73, 74, 75, 76, 77, 78, 79, 80, 81, 82, 83, 84, 85, 86, 87, 88, 89, 90, 91, 92, 93, 94, 95, 96, 97, 98, 99, 100, 101, 102, 103, 104, 105, 106, 107, 108, 109, 110, 111, 112, 113, 114, 115, 116, 117, 118, 119, 120, 121, 122, 123, 124, 125, 126, 127, 128, 129, 130, 131, 132, 133, 134, 135, 136, 137, 138, 139, 140, 141, 142, 143, 144, 145, 146, 147, 148, 149, 150, 151, 152, 153, 154, 155, 156, 157, 158, 159, 160, 161, 162, 163, 164, 165, 166, 167]. Duplicate slots: {}.

Successful hosted HTTP latency (milliseconds, nearest-rank; each row retains its own sample count):

| Collector | Route | Phase | Edge | Provider | Database pair | Process | N | p50 | p95 | p99 | Errors |
| --- | --- | --- | --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: |
| 1025ba3 | forecasts | first | BYPASS | cold | provider_idle_to_active_across_read_pair | first | 1 | 4477.310 | 4477.310 | 4477.310 | 0 |
| 1025ba3 | forecasts | paired | BYPASS | warm | provider_idle_to_active_across_read_pair | subsequent | 1 | 762.627 | 762.627 | 762.627 | 0 |
| 1025ba3 | performance | first | BYPASS | cold | provider_idle_to_active_across_read_pair | first | 1 | 9660.019 | 9660.019 | 9660.019 | 0 |
| 1025ba3 | performance | paired | BYPASS | warm | provider_idle_to_active_across_read_pair | subsequent | 1 | 1001.792 | 1001.792 | 1001.792 | 0 |
| 1025ba3 | ticker-filter | first | BYPASS | cold | provider_idle_to_active_across_read_pair | first | 1 | 5098.796 | 5098.796 | 5098.796 | 0 |
| 1025ba3 | ticker-filter | paired | BYPASS | warm | provider_idle_to_active_across_read_pair | subsequent | 1 | 749.974 | 749.974 | 749.974 | 0 |
| 1748130 | status | first | BYPASS | cold | provider_idle_to_active_across_read_pair | first | 1 | 3710.978 | 3710.978 | 3710.978 | 0 |
| 1748130 | status | paired | BYPASS | warm | provider_idle_to_active_across_read_pair | subsequent | 1 | 625.023 | 625.023 | 625.023 | 0 |
| 1748130 | ticker-filter | first | BYPASS | cold | provider_idle_to_active_across_read_pair | first | 1 | 3741.313 | 3741.313 | 3741.313 | 0 |
| 1748130 | ticker-filter | paired | BYPASS | warm | provider_idle_to_active_across_read_pair | subsequent | 1 | 691.262 | 691.262 | 691.262 | 0 |
| 2f3c053 | ticker-filter | first | BYPASS | cold | provider_idle_to_active_across_read_pair | first | 1 | 3666.375 | 3666.375 | 3666.375 | 0 |
| 2f3c053 | ticker-filter | first | BYPASS | cold | unobserved | first | 1 | 3804.810 | 3804.810 | 3804.810 | 0 |
| 2f3c053 | ticker-filter | paired | BYPASS | unobserved | unobserved | subsequent | 1 | 691.246 | 691.246 | 691.246 | 0 |
| 2f3c053 | ticker-filter | paired | BYPASS | warm | provider_idle_to_active_across_read_pair | subsequent | 1 | 701.307 | 701.307 | 701.307 | 0 |
| 64e4ebb | performance | first | BYPASS | cold | provider_idle_to_active_across_read_pair | first | 1 | 9597.013 | 9597.013 | 9597.013 | 0 |
| 64e4ebb | performance | paired | BYPASS | warm | provider_idle_to_active_across_read_pair | subsequent | 1 | 957.010 | 957.010 | 957.010 | 0 |
| 64e4ebb | status | first | BYPASS | cold | provider_idle_to_active_across_read_pair | first | 1 | 4681.490 | 4681.490 | 4681.490 | 0 |
| 64e4ebb | status | paired | BYPASS | warm | provider_idle_to_active_across_read_pair | subsequent | 1 | 670.708 | 670.708 | 670.708 | 0 |
| 8cbe7ea | forecasts | first | BYPASS | cold | provider_idle_to_active_across_read_pair | first | 1 | 3782.618 | 3782.618 | 3782.618 | 0 |
| 8cbe7ea | forecasts | paired | BYPASS | warm | provider_idle_to_active_across_read_pair | subsequent | 1 | 808.839 | 808.839 | 808.839 | 0 |
| f79f67b | ticker-filter | first | BYPASS | cold | provider_idle_to_active_across_read_pair | first | 2 | 3915.031 | 4216.538 | 4216.538 | 0 |
| f79f67b | ticker-filter | paired | BYPASS | warm | provider_idle_to_active_across_read_pair | subsequent | 2 | 768.469 | 853.082 | 853.082 | 0 |

The JSON retains preparation, connection, integrity, service, snapshot and lifecycle distributions separately. Preparation CPU is included in HTTP/lifecycle latency.

Local native reference (milliseconds; Unix socket without HTTP/TLS, separate from the hosted rows):

| Route | Service p50 | Service p95 | Service p99 | N |
| --- | ---: | ---: | ---: | ---: |
| status | 3.663 | 4.082 | 14.920 | 100 |
| forecasts | 3.069 | 3.258 | 3.793 | 100 |
| ticker_filter | 3.540 | 4.011 | 4.757 | 100 |
| performance | 17.165 | 28.197 | 28.766 | 100 |

- Native local Unix-socket timings exclude HTTP, TLS and provider startup; never pool them with hosted latency.
- Database resume attribution is pair-level endpoint state, not an exact per-request resume timestamp.
- Private no-store routes do not intentionally exercise edge hits; report zero observed hits explicitly.
- Preparation CPU remains in HTTP/lifecycle time; bootstrap cache state is not separately reported.
- Small per-cohort tail samples are descriptive and do not justify an SLO or alert threshold.
- An elapsed policy window is not proof of seven days of observations; HTTP/contract errors exclude separate trace and workflow failures.

No SLO/alert threshold is established and no issue is automatically closed. Review coverage, attribution, errors and the retained safe query plans before deciding whether the acceptance criteria are met.
