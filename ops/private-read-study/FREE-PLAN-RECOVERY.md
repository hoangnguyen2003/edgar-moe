# Issue #290: free-plan recovery, November 6, 2026

**A free provider-metadata path is now verified. The seven-day study is not yet
active and the issue is not complete. No paid upgrade, trial, card or billing
change is required for the verified path.**

## Verified replacement for paid invocation queries

The normal invocation-event query returned HTTP 402 requiring Observability Plus.
Vercel separately documents [CLI session tracing on all plans](https://vercel.com/changelog/trace-any-vercel-request-from-the-cli).
The [official CLI implementation](https://github.com/vercel/vercel/tree/84878e532142f0ac584953695658b324eb9e5b80/packages/cli/src/commands)
uses a hostname-scoped trace session and an authenticated trace lookup. Those
requests returned HTTP 200 on the existing Hobby account.

The implemented collector was then exercised against the new protected,
**unbound** study service. Both application requests intentionally returned 503:
no database URL or policy was deployed and no Postgres connection was possible.
This is transport/metadata setup evidence, not two successful database reads.

| Provider evidence | First setup request | Paired setup request |
| --- | --- | --- |
| Complete, matching request/project/deployment trace | Yes | Yes |
| `Start VM`, `Spawn Python`, `Init User Code` | Present | Absent |
| `Invoke Function` | Present | Present |
| Operational cohort | Provider startup observed | Complete invocation without startup |

The collector reports those operational cohorts as `cold` and `warm`, preserving
its exact basis and individual startup-span flags. It does **not** claim to read
the paid `functionStartType` enum. It verifies the trace tree, target, request,
provider service identity, finite timings and completeness. Startup must occur
inside the request's provider span, with a five-millisecond rounding tolerance;
historical prewarming is refused. An unknown runtime span, partial trace,
ambiguous invocation or mismatch is refused rather than called warm.

Trace ingestion is asynchronous: an initial metadata lookup returned 404 and a
later lookup succeeded. Only that metadata GET is retried, at most three times;
application reads are never replayed. Raw provider attributes, headers, URLs,
identifiers, tokens and application bodies are discarded. Receipts contain an
allowlisted numeric summary and hashed request identifier. Traced measurements
are an instrumented cohort; do not pool them with untraced historical latency.

Neon's fixed endpoint-details GET also passed, returning `idle` without waking
Postgres. The separate metadata steps capture before/after state without SQL.
The HTTP probe receives neither the Neon API key nor any database credential.
Correlation labels an idle-to-active transition **across the read pair**; it does
not invent the exact restart time or a per-request state for the paired read.
Pending transitions, target mismatches and stale receipts refuse correlation.

The [redacted setup packet](evidence/free-plan-setup-20261106/) retains these
two proofs and SHA-256 checksums. It contains no hosted measurement days. The
collector source hash identifies the version used for the live setup; the
subsequent request-interval/prewarming guard is covered by regression tests.

## Free alternatives evaluated

| Option | Finding / dependency | Decision |
| --- | --- | --- |
| Existing Vercel Hobby + Neon Free | Actual cold/warm provider traces and endpoint-state GET verified. Existing native Python application works. | Selected; avoids a host migration. |
| Vercel Pro trial | Owner's actual checkout requires a card and authorizes future charges. | Withdrawn; do not complete checkout. |
| [Render Free](https://render.com/docs/free) | Native Python, no-payment-method quota suspension, automatic idle spin-down; Free compute is small. Account setup, capacity and lifecycle attribution would still need live verification. | Possible fallback, unnecessary for the proven route. |
| [Hugging Face Spaces](https://huggingface.co/docs/hub/spaces-overview) | Current documentation requires a paid plan to create new Docker/Gradio compute Spaces. | Not a free substitute for this setup. |
| [Modal](https://modal.com/docs/guide/billing), Koyeb, Northflank | Credits/free tiers do not establish a card-free, charge-free setup; payment verification remains a dependency. | No billing enrollment pursued. |
| PythonAnywhere Free | Required outbound native Postgres connection is unavailable. | Incompatible with this adapter. |
| Leapcell Hobby | Native Python/free plan documented; automatic authoritative startup capture not verified. | No new account or speculative migration. |
| GitHub-hosted temporary origin / application process-first flag | Useful diagnostics, but neither supplies the required serverless-provider startup evidence. | Do not replace the original acceptance criterion. |

## Activation checklist and remaining dependencies

1. Obtain an expiring Vercel automation token restricted to **only the newly
   created study project**. Verify actual scope/expiry and trace-session/lookup
   access before placing it in the `hosted-read-study` environment. Never export
   the CLI OAuth refresh credential or an account-wide bootstrap token to CI.
2. Provision and verify a separate Neon project-scoped metadata key for **only
   the new synthetic study project**. Such keys can modify their project; they
   are not read-only credentials. Keep the key in the fixed endpoint-metadata
   steps, outside the HTTP probe and hosted application. Arrange revocation when
   the study ends. This scoped key has not been issued; the existing organization
   key remains local and must not be exported to CI.
3. Recheck included quota headroom, bind a fresh whole-hour 168-hour policy and
   matching SELECT-only reader expiry, deploy the protected preview and verify
   the bound HTTP path. The current reader expiry is provisional; this work does
   not extend the old pilot or change any existing project.
4. Configure the workflow's HTTP and provider metadata secrets, host/policy
   hashes and absolute start/end variables. Enable `ISSUE290_STUDY_ENABLED` only
   after the fixed targets, credentials and setup pass. Missing trace credentials
   refuse before any application read; failed metadata/reads stop this observer.
5. Collect **seven real days**, retaining missing/failed slots, route/cohort sample
   counts and errors. Compare matched p50/p95/p99 and plans with the local baseline;
   set an SLO only if the observations support it. Close #290 only after the
   original acceptance criteria are met.

The frozen application/kernel, existing datasets/deployments, append-only ledger
and #156 scheduler are unaffected. No daily manual dashboard check is part of
this route. One-time credential setup and seven elapsed observation days remain;
local setup traces cannot substitute for them.
