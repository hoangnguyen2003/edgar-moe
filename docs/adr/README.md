# Architecture decision records

Each record states the context, the decision, the alternatives considered, and how
the decision is verified. All 23 records are accepted. When a decision changes,
add a new record that supersedes the old one rather than rewriting it.
The [Solution Architecture](../solution-architecture.md) document places these
decisions in the overall design.

## Platform and research integrity

| ADR | Decision | Date |
| --- | --- | --- |
| [0001](0001-separate-serving-and-batch.md) | Keep one repository, but run public serving (Vercel: static React and read-only FastAPI) separately from private batch work (scheduled GitHub Actions ingestion, frozen inference, and settlement), with Postgres for prospective evidence. | 2026-09-18 |
| [0002](0002-freeze-v1-prospective-evaluation.md) | Treat v1 as one immutable identity (model, dataset, selection, artifact, locked result, and snapshot lock); any change is a new version, evaluated only prospectively. | 2026-09-19 |
| [0004](0004-runtime-snapshot-lock.md) | Verify the public snapshot's path, SHA-256 digest, and identity metadata against the checked-in lock before serving it; fail closed on drift. | 2026-09-20 |
| [0011](0011-api-read-only-statement-boundary.md) | Open API database sessions with `default_transaction_read_only=on` and a bounded statement timeout (5 s default). | 2026-09-20 |
| [0015](0015-xbrl-fact-selection-policy.md) | Version the XBRL fact-selection policy: `legacy_v1` reproduces the frozen study exactly, while later studies use duration-aware, staleness-bounded facts. | 2026-09-21 |
| [0016](0016-database-append-only-triggers.md) | Enforce the append-only registry with database triggers on SQLite and Postgres: evidence tables reject every update and delete. | 2026-09-21 |
| [0017](0017-post-v1-selection-protocol.md) | For studies after v1, early-stop on a purged chronological holdout instead of the scored validation fold, and use scale-invariant baselines. | 2026-09-21 |
| [0021](0021-frozen-runtime-compatibility-gate.md) | Bind embedding caches to exact PyTorch and Transformers versions, and require a reproduction comparison of the locked test before any frozen-inference dependency upgrade. | 2026-09-22 |
| [0022](0022-copilot-review-profiles.md) | Keep research, quant, architecture, and operations copilot perspectives bounded by one content-addressed read-only agent identity. | 2026-09-22 |

## Operations and delivery

| ADR | Decision | Date |
| --- | --- | --- |
| [0003](0003-cross-domain-platform-readiness.md) | Compose the public-release, operator-evidence, copilot, and drift verifiers into one content-addressed readiness decision that keeps only scope, status, hash, and reason codes. | 2026-09-20 |
| [0018](0018-workflow-supply-chain.md) | Pin every workflow action to a full commit SHA, and pass each secret only to the steps that need it. | 2026-09-21 |
| [0019](0019-forward-cache-lifecycle.md) | Keep immutable per-attempt runtime caches, and prune all but the two newest in a separate job with no checkout, dependencies, or production secrets. | 2026-09-22 |
| [0020](0020-pre-open-schedule-margin.md) | Record each forecast run's margin before the NYSE open as an immutable quality check, and warn below 90 minutes, without changing the cutoff or the model. | 2026-09-22 |
| [0023](0023-external-forward-scheduler.md) | Stage a dormant Cloudflare Cron adapter for the existing forward workflow; require a separate observed cutover before removing the GitHub schedule. | 2026-09-22 |

## AI research copilot boundary

| ADR | Decision | Date |
| --- | --- | --- |
| [0005](0005-copilot-agent-identity.md) | Record a non-secret agent identity (policy ID, policy digest, and tool-schema digest) with every generated answer. | 2026-09-20 |
| [0006](0006-copilot-benchmark-agent-consistency.md) | Classify evaluated answers as `consistent`, `legacy`, or `mixed` agent identities; only a consistent set can be ready. | 2026-09-20 |
| [0007](0007-copilot-transient-retry-policy.md) | Retry only transient provider failures (408, 425, 429, 5xx, and bounded transport errors), with capped exponential backoff. | 2026-09-20 |
| [0008](0008-copilot-run-execution-budget.md) | Give every copilot run a wall-clock budget (300 s default, 900 s maximum) that is checked before each provider call. | 2026-09-20 |
| [0009](0009-copilot-context-budget.md) | Give every copilot run a UTF-8 context-size budget (512 KiB default, 2 MiB maximum) that is checked before each provider call. | 2026-09-20 |
| [0010](0010-copilot-provider-redirect-boundary.md) | Reject provider redirects at the egress boundary instead of following them. | 2026-09-20 |
| [0012](0012-copilot-citation-closure.md) | Fail closed when evidence tools were used but no tool citation survives into the final answer. | 2026-09-20 |
| [0013](0013-copilot-provider-host-allowlist.md) | Restrict remote provider egress to an exact hostname allowlist (default `api.openai.com`). | 2026-09-20 |
| [0014](0014-copilot-citation-payload-binding.md) | Bind every citation digest to the canonical hash of the exact tool payload that was requested. | 2026-09-20 |

## Writing a new record

Copy the structure of a recent record (for example [0020](0020-pre-open-schedule-margin.md)):
context, decision, alternatives considered, consequences, and verification. Use
the next number, and state which parts the code enforces and which depend on
infrastructure or operator action.
