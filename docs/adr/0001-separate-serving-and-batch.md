# ADR 0001: Separate public serving from private research execution

Date: 2026-09-18

Status: Accepted as a retrospective description of the existing design.
This record does not imply that all operational safeguards are implemented.

## Context

Inference and source refresh involve Python research dependencies, network-bound
ingestion, and long-running batch work. The public interface needs small, quick
reads. Forecast integrity requires a durable record written before market entry,
followed by later outcome settlement. One maintainer operates under a zero-spend
objective with no demonstrated requirement for real-time traffic or multi-tenancy.

## Decision

Keep one repository and modular Python application, with separate execution roles:

- Vercel: React assets and read-only FastAPI serving.
- Scheduled GitHub Actions: private ingestion, frozen inference, and settlement.
- Postgres: structured prospective evidence and operational state.
- Private R2 mirror: content-addressed run evidence; not a replacement for DB backup.
- Local research environment: training and artifact reproduction.

Maintain the frozen historical snapshot separately from growing forward evidence.
Do not introduce microservices, Kubernetes, a queue, or a new database merely for
architectural appearance. New infrastructure must solve a measured constraint.

## Alternatives and trade-offs

| Alternative | Benefit | Reason not selected now |
| --- | --- | --- |
| Static-only site | Minimal operational surface | Cannot independently serve the growing authoritative registry without an export pipeline |
| One always-on container | Simple runtime and dependency consistency | Couples heavy jobs and public availability; needs host lifecycle and resource management |
| Inference inside web requests | Fewer execution environments | Couples user latency, research dependencies, and write authority |
| Queue plus dedicated workers | Better task scheduling and retry control | Additional operations before a measured need |

Separation limits serving dependencies and credential exposure. Its costs are
cross-provider troubleshooting, credentials in multiple systems, scheduler delays,
and non-atomic database/object-store writes. Reconciliation and tested recovery
are therefore necessary design work, not optional polish.

## Revisit when

Measure repeated deadline misses, missed eligible pre-entry forecasts, growing
database load, or a requirement for authenticated users / stronger availability.
If these cannot be addressed within the current design, evaluate a dedicated
worker or managed scheduler using observed runtime, cost, and recovery evidence.
Provider names are implementation choices, not permanent architectural requirements.

## Verification

Trace boundaries through `vercel.json`, `api/index.py`, the forward workflow,
`src/edgar_moe/forward/workflow.py`, and the registry and artifact-store modules.
Operational acceptance work is tracked in [the roadmap](../architecture-roadmap.md).
