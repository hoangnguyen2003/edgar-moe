# WorldQuant Hanoi role-to-evidence map

This maps the 13 public role descriptions reviewed on 2026-09-24 to things a
reviewer can inspect in EDGAR-MoE. It is a project-scope map, **not** a claim of
professional tenure, academic qualifications, BRAIN access, work on WorldQuant
systems, or a profitable alpha. The frozen v1 result remains rank IC 0.0316 on
1,794 locked events, with a rank-IC interval spanning zero and a −0.63 Sharpe
after 10 bps costs ([model card](model-card.md)). “Gap” means the strongest
public-project improvement or a qualification the project cannot establish.

| Role and relevant requirement | Existing inspectable evidence | Most important genuine gap | Specific closure artifact |
| --- | --- | --- | --- |
| [Backend Engineer](https://www.worldquant.com/career-listing/?id=4665338006): Python APIs, database, CI, reliability and performance | Read-only [FastAPI](../src/edgar_moe/api/app.py), [registry](../src/edgar_moe/forward/registry.py), [CI](../.github/workflows/ci.yml) | No hosted API load/latency distribution under a stated traffic model | Proposed `reports/api-read-path-benchmark.json` and a reproducible failure-injection test; never infer scale from unit tests |
| [Full Stack Engineer](https://www.worldquant.com/career-listing/?id=4658486006): REST, React, responsive and accessible UI, deployment | [Research page](../apps/web/src/pages/ResearchPage.tsx), [API contracts](../src/edgar_moe/api/models.py), [page tests](../apps/web/src/pages/ResearchPage.test.tsx) | The terminal does not yet explain a separate v2 study and its uncertainty | Proposed v2 comparison API contract and accessible React panel, backed by a reviewed v2 report |
| [Python Developer](https://www.worldquant.com/career-listing/?id=4673851006): production Python, ML/backtesting platforms, algorithms and concurrency | [Point-in-time builder](../src/edgar_moe/features/dataset.py), [walk-forward training](../src/edgar_moe/modeling/walk_forward.py), [resume failure tests](../tests/unit/test_authenticated_refresh.py) | No measured evidence that a proposed pipeline concurrency change improves throughput safely | Proposed bounded-ingestion benchmark; the project cannot establish seven years' experience |
| [Quantitative Developer](https://www.worldquant.com/career-listing/?id=4703213006): scheduled jobs, diagnostics, automation and incident triage | [Forward workflow](../.github/workflows/forward-production.yml), [alerts](../src/edgar_moe/forward/alerts.py), [runbook](forward-testing.md) | The GitHub schedule's pre-open delay is observed but not resolved | Proposed scheduler-cutover observation and rollback evidence; no claim of on-call employment |
| [Quantitative Researcher](https://www.worldquant.com/career-listing/?id=4069478006): rigorous signal research and C/C++ on Unix | [Locked negative result](../reports/authenticated_research_report.md), [selection](../src/edgar_moe/modeling/walk_forward.py), [leakage tests](../tests/integration/test_authenticated_study.py) | v1's XBRL and baseline defects need a separately identified v2 evaluation; C++ proficiency is unproved | Proposed `v2-pretest-review` command/report and [native-code decision](architecture-roadmap.md); degree/class-rank requirements are not project evidence |
| [Senior Backend Engineer](https://www.worldquant.com/career-listing/?id=4653484006): database design, query optimization, API/network boundaries | [Migrations](../migrations/versions/20260921_0002_append_only_triggers.py), [reader-role audit](../src/edgar_moe/forward/reader_role.py), [API tests](../tests/integration/test_forward_api.py) | No production-scale query-plan and p95 evidence for the read path | Proposed Postgres `EXPLAIN`/latency report with a regression threshold; senior tenure/mentoring are not provable here |
| [Senior Software Engineer](https://www.worldquant.com/career-listing/?id=4672066006): ETL ownership, architecture trade-offs and incident response | [Refresh pipeline](../src/edgar_moe/data/refresh.py), [resume decision](adr/0029-authenticated-checkpoint-resume-integrity.md), [failure-injection tests](../tests/unit/test_authenticated_refresh.py), [restore rehearsal](restore-rehearsal.md) | Provider-side failure recovery and scale limits remain partly unverified | Proposed retained provider restore/audit evidence; eight years' experience is not a project claim |
| [Software Engineer](https://www.worldquant.com/career-listing/?id=4065713006): data/research/portfolio lifecycle and useful LLM tooling | [Pipeline](../src/edgar_moe/features/dataset.py), [portfolio](../src/edgar_moe/backtest/optimizer.py), [bounded copilot](../src/edgar_moe/copilot/agent.py) | The copilot has safety/evaluation contracts but no demonstrated incremental research value over deterministic navigation | Proposed paired copilot-vs-rule-based task evaluation with a failure/latency/cost report |
| [Software Engineer (C/C++)](https://job-boards.greenhouse.io/worldquant/jobs/4699962006): modern C++, memory/I/O, distributed time-series storage | [Go evidence auditor](../tools/evidence-auditor/audit.go) shows an independent storage-integrity boundary; [capacity baseline](../src/edgar_moe/capacity.py) measures some paths | No C++ implementation or profiled native bottleneck; no evidence of distributed-storage experience | Proposed scoped native microbenchmark and parity tests **only if** profiling shows a meaningful benefit; do not add résumé-only C++ |
| [Solution Engineer](https://www.worldquant.com/career-listing/?id=4673858006): user needs, platform roadmap, Git lifecycle and Go/Python | [Architecture](solution-architecture.md), [ADRs](adr/README.md), [Go auditor](evidence-auditor.md), PR/CI workflow | Hosted read-only audit and restore proof are still provider-dependent | Proposed retained provider evidence packet and operator walkthrough; cannot claim customer-facing work or tenure |
| [WQBRAIN AI Researcher](https://job-boards.greenhouse.io/worldquant/jobs/4686030006): AI/LLM experiments and quantitative modeling | [MoE](../src/edgar_moe/modeling/moe.py), [copilot evaluation](../src/edgar_moe/copilot/evaluation.py), [model card](model-card.md) | No evidence that MoE or LLM components add independent signal value after fair baselines and costs | Proposed v2 paired comparison with uncertainty and an LLM-vs-deterministic evaluation; no BRAIN-platform claim |
| [WQBRAIN Researcher](https://www.worldquant.com/career-listing/?id=4686007006): literature-led alpha ideas, new datasets and researcher tools | [Research report](../reports/research_report.md), [data card](data-card.md), [research terminal](../apps/web/src/pages/ResearchPage.tsx) | Current cost-aware result rejects the tradable-alpha hypothesis; private BRAIN usage cannot be shown | Proposed explicit v2 hypothesis registry and reproducible pre-test review, reporting negative findings and leaving BRAIN work unclaimed |
| [Software Engineer Intern](https://www.worldquant.com/career-listing/?id=4652485006): Python, algorithms, tests, quant-data interest | [Quick start](../README.md#quick-start), [unit tests](../tests/unit), [CI](../.github/workflows/ci.yml) | Reviewers need a small credential-free route from setup to a checked result | Proposed one-command synthetic fixture walkthrough in [research runbook](research-runbook.md); current enrollment is not demonstrated by this repository |

The next cross-role slice is a *separate, pre-test-only v2 review path*: verify
the duration-aware dataset and revised-selection identities, recompute paired
model comparisons on the same scored filings, retain sample/uncertainty/cost
context, and refuse to touch the frozen v1 locked result or forward registry.
Only reviewed aggregate evidence should reach the public API or React terminal.

## Native-code decision gate

C++ is **deferred**, not quietly claimed. Profile a representative authenticated
v2 dataset under a pinned Python runtime and retain wall time, peak RSS, input
size, and profiler output for dataset assembly, daily portfolio simulation, and
forward settlement. A native component is justified only when one isolated
CPU-bound loop dominates at least 30% of end-to-end runtime and a production-
shaped C++ prototype achieves at least 2× speedup on that loop **and** 20%
end-to-end speedup without materially increasing peak memory or operational
complexity. Before adoption it must match Python outputs on golden fixtures,
missing/duplicate data, date boundaries, and randomized property tests, and
ship as an optional, versioned package with a pure-Python fallback. Until that
evidence exists, Python/NumPy/Postgres/Go remain the simpler architecture.
