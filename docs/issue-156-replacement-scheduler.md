# Finite replacement scheduler, November 14, 2026

The renewed Worker-scoped credential passed the existing management workflow.
[STOP run 43555332838](https://github.com/hoangnguyen2003/edgar-moe/actions/runs/43555332838)
made one schedule update, uploaded no Worker code and verified that Cron and
public Worker URLs are disabled. The previous November 7/10/11 scope remains
unchanged and has no qualifying forecasts. Its failed cleanup receipts are
retained alongside the successful recovery; it cannot satisfy acceptance.

The separate [November 14 scope](../ops/issue156/private-scheduler-scope-20261114.json)
proposes four exact UTC slots in the existing Worker:

| UTC date at 07:17 | Mode | Permitted work |
| --- | --- | --- |
| November 17 | `delivery_probe` | Verify the signed controller timestamp and retain a source-free Actions receipt |
| November 18 | `forecast` | One private frozen-v1 cycle, after verifying the probe |
| November 19 | `forecast` | One private frozen-v1 cycle, after verifying the probe |
| November 20 | `forecast` | One private frozen-v1 cycle, after verifying the probe |

The probe uses the same Worker, GitHub dispatch credential, workflow and HMAC
key as the forecasts. Its job receives only the signing secret, installs no
provider dependencies and receives no market, database or storage credential.
It makes zero source requests and never counts as forecast-margin evidence.
No GraphQL analytics permission or paid service is required for this proof.

The HMAC binds the new scope ID, mode, scheduled time, unchanged Cron, main
branch, CPU device and workflow. A probe signature cannot become a forecast
signature. Earlier scope dates, future/stale timestamps, unsigned/manual
inputs and reruns are refused. Outside the four exact slots the Worker returns
before reading credentials or contacting GitHub. Public HTTP requests stay 404.

Before any forecast dependencies or provider credentials are used,
`verify_private_scheduler_delivery.py --require` verifies exactly one successful
attempt-one probe on main, its repository/workflow/source commit, a matching
scope and runtime-input digest, consistent clocks, the flat artifact inventory
and receipt hash. Missing, failed, duplicate, expired, foreign or altered
proofs refuse forecast execution. The check uses at most four Actions metadata
reads with no retries. Current or historical source credentials cannot be
supplied to the delivery-only recorder.

Preparation or activation must start before **November 17 at 06:17 UTC**,
leaving at least an hour before the probe. Late activation refuses before
service access; dates never slide automatically. STOP and read-only state
verification remain available after this deadline. The configuration, workflow,
Worker and verifier are hash-bound in the new scope. Existing global provider
and publication holds, frozen inputs, source families, private targets, CPU-only
inference and storage caps remain in force. Nominal UTC timing and the New York
source-cutoff rule are unchanged; only the new finite dates and delivery gate
are introduced.

Actual on-time execution can observe fewer same-morning filings than the old
4–5-hour-delayed runs: filings not yet available when the cycle runs are not
backfilled into that prediction. Unchanged cutoff and coverage configuration
does not mean an identical filing set compared with a later actual start.
This scope measures timely recording under the configured nominal schedule;
it does not claim the delayed and on-time research inputs are interchangeable.

## Activation and closeout

This change prepares the scope; it does not activate it. After the reviewed
change is merged, use `deploy-private-scheduler.yml` on main with
`RESUME_PREPARE` to install the signed code with Cron empty, verify the private
state, then use `ACTIVATE` before the fixed deadline. Enable the existing
closeout workflow for this new scope. Do not manually dispatch a forecast or
reuse an old signature. If prerequisites cannot be verified in time, retain
this scope as unactivated and prepare a separately identified future scope.

The existing closeout requires three distinct real nonzero forecast receipts,
at least 5,400 seconds of actual pre-open margin each, unchanged prior rows,
source configuration and input boundaries, correct Actions provenance, and
retained private-evidence hashes. The delivery probe alone cannot close #156.
Missing, zero-result, failed or short-margin cycles keep the issue open. Cleanup
must acknowledge the exact successful STOP run before claiming shutdown.

The separate #290 read study still has an expired policy ending November 13.
It remains disabled. A replacement requires newly verified observation delivery,
a protected deployment, fresh attribution credentials and a SELECT-only reader
whose expiry matches a separate full seven-day window. No old observation is
replayed, pooled into a new window or labeled a new measurement day.

The [readiness audit](../ops/issue156/replacement-readiness-20261114.json)
binds the new scope, verified predecessor shutdown and a private 17-file packet.
Offline validation passed 2,585 Python tests (36 skips, 85.87% coverage), 45
scheduler Node tests and 124 focused/documentation checks. Those synthetic
checks establish the contracts, not live delivery or forecast-margin evidence.
