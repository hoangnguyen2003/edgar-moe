# Evidence-grounded research copilot

EDGAR-MoE defaults to deterministic, credential-free evidence navigation, with
an experimental operator-run LLM/agentic layer available only by explicit opt-in.
Neither mode is part of the forecasting path: the
frozen v1 model, prospective forecast registry, labels, and deployment state are
all outside the copilot's write boundary.

## What it does

The `research-copilot` command uses the separately versioned
`deterministic-evidence-explanations-v1` navigator by default. Fixed templates
display predictive metrics, cost scenarios, methodology, experiment rows, identity,
and governance. Matched topics combine at most five zero-argument projections;
experiment questions also include study metrics, and locked-test questions include
both metrics and snapshot identity. It refuses matched order requests, explicitly
reports unavailable live quotes, and abstains when no fixed route matches.
Source text is quoted and bounded; missing or invalid values are labeled not recorded.
This mode reads only the local snapshot:
configured provider keys, endpoints, and registry URLs do not enable network or
database access. It is a navigation aid, not a complete question-answering model;
the keyword routes and templates can omit parts of a question. The original
`deterministic-evidence-navigation-v1` comparison arm in `research-copilot-baseline`
is unchanged. Citation digests identify tool payloads, not proof of licensing,
provider backup coverage, or performance. Locally projected governance is not a
live console audit; an unattached registry says nothing about production's state.

```bash
uv run edgar-moe research-copilot "Show the study summary and cost scenarios."
```

For a readable terminal answer with sources, fields, digests, and the research
disclaimer, append `--format text`. JSON remains the default for scripts.
`--output /tmp/private-copilot/answer.json` always saves the complete private JSON
envelope, regardless of display format, and prints only the saved-file notice.
The terminal view escapes control/bidirectional characters without changing the
saved answer. Presentation verification checks the envelope, not prose factuality
or the original source bytes.

```bash
uv run edgar-moe research-copilot "Explain the target and limitations." --format text
uv run edgar-moe research-copilot "Show the study summary." --plan-only
```

The JSON-only default `--plan-only` preview lists exactly five snapshot projections,
the navigator version, and its five-call ceiling. Previewing experimental profiles,
provider/agent options, registry or diagnostic capabilities also requires
`--experimental-llm`; planning still makes no provider call or database connection.
`--format text` is for answers, not the plan contract.

Adding `--experimental-llm` sends the question to an OpenAI-compatible
chat-completions endpoint and gives the model seven baseline bounded, read-only tools:

- frozen snapshot identity;
- study summary and cost scenarios;
- methodology and limitations;
- model-selection experiments;
- bounded searches of derived filing events;
- one derived event by accession number; and
- governance plus optional forward-registry status.

When an operator explicitly attaches diagnostic evidence, the toolset can also
expose one redacted diagnostic or one verified redacted diagnostic history.

Provider execution in `research-copilot-panel` and `research-copilot-benchmark`
also requires `--experimental-llm`. `--plan-only` remains provider-free without
that flag, and does not open a registry database. Explicit provider, profile,
diagnostic, registry, or agent-budget options on a single-question answer or
preview require the flag rather than silently enabling or ignoring an experiment.

### Current engineering decision

The 2026-10-07 local synthetic comparison does not justify promoting an LLM.
Both original arms passed 5/8 structural checks; that score is not a factuality
rating. An **unblinded AI assessment**, not a human study, identified unsupported
tradability/model-superiority claims and an inadequate live-order refusal in
the generated answers. Keep deterministic navigation as the default and the
LLM experimental; do not claim incremental utility, human validation, or
production readiness. No human ratings have been generated or substituted.
The existing comparison router, corpus, saved reports, and frozen/forward
records are unchanged. Future changes evaluated on these now-inspected cases
are development regressions, not a new independent held-out confirmation.
The new default explanations are an engineering response to those findings,
tested with invented fixtures and boundary regressions, not a fresh utility study.

The agent can make at most four tool calls by default. The provider transport
allows at most two retries for explicitly transient HTTP/network failures, with
a capped exponential backoff; authentication, validation, malformed-response,
and oversized-response failures are not retried. Each tool result is
content-hashed and returned with a citation; the runtime rejects a citation
whose digest does not match that exact sanitized payload, or whose result name
does not match the requested capability. The JSON answer envelope retains
the question, answer, frozen identity, citations, and a non-sensitive tool
trace. A report with no tool citations is marked `uncited`; it is not silently
treated as evidence.

Newly generated envelopes also include an `agent_identity` record containing
the stable policy ID, a SHA-256 digest of the system policy, a SHA-256 digest of
the exact tool schema offered for that run, and the configured tool-call
budget. This lets a reviewer distinguish a changed prompt/tool boundary from a
changed provider answer without retaining the provider endpoint, prompt
payload, or secret. The verifier checks the policy digest and bounded fields;
the tool-contract digest is content-addressed because optional diagnostic tools
can change the allowed schema. Existing schema-1 reports without this optional
record remain verifiable.

The provider HTTP client uses a no-redirect opener. A configured HTTPS endpoint
or loopback HTTP endpoint must answer directly; a `3xx` response fails closed
before a second host can receive the request. Redirects are not treated as
transient failures and are never retried. This keeps the provider egress
boundary explicit and prevents an endpoint-controlled redirect from weakening
the endpoint validation contract.

Remote HTTPS egress is also restricted to an exact, comma-separated hostname
allowlist. The default is `api.openai.com`; changing the endpoint to another
remote OpenAI-compatible provider requires adding that provider hostname to
`EDGAR_MOE_COPILOT_ALLOWED_HOSTS`. Entries cannot contain schemes, paths, ports,
credentials, or wildcards. Loopback HTTP runtimes such as Ollama remain
available for local development without being added to the remote list.

Envelope verification is enforced at every local boundary: the agent validates
its generated answer before returning it, the CLI validates it before printing
or atomically writing it, and benchmark/evaluation commands reject unverified
reports before they enter private case files or aggregate scores.

Citation closure is also enforced at the generation boundary. Every allowlisted
evidence-tool result must carry a citation bound to its payload hash, and the
final answer must retain at least one citation emitted by that tool; otherwise
the run fails closed instead of saving an apparently valid uncited answer. A
refusal after only rejected or unknown tool requests may remain explicitly
uncited because no evidence was obtained. The offline verifier repeats the
presence and closure rules for saved envelopes; payload binding is enforced at
the live tool boundary before provider egress.

Each generated private answer may also include a bounded `usage` summary: the
number of provider requests, total local elapsed milliseconds, and standard
prompt/completion/total token counters when the provider reports them. Unknown
provider metadata is discarded, counters are range-checked, and missing counters
remain `null`. This supports operator cost/capacity review without retaining
provider payloads, prompts, billing assumptions, or endpoint details. Aggregate
benchmark reports additionally retain only the successful-answer count and
aggregate request/latency telemetry; token totals are `null` when any successful
answer does not report that counter. These aggregates contain no answer text,
questions, provider payloads, billing assumptions, or endpoint details.

The usage `request_count` includes transport retries, so an operator can see
the actual number of provider attempts without retaining error bodies. Override
the conservative defaults with `--max-retries` and
`--retry-backoff-seconds`, or the corresponding
`EDGAR_MOE_COPILOT_MAX_RETRIES` and
`EDGAR_MOE_COPILOT_RETRY_BACKOFF_SECONDS` settings. Retry delays are never
read from provider response headers.

Each copilot run also has a 300-second aggregate wall-clock budget by default,
bounded to 900 seconds. The agent checks that budget before every provider
completion, so a long tool loop cannot start another paid call after the
deadline; an already in-flight provider request is not forcibly interrupted.
The envelope records total local elapsed time, and its `agent_identity` records
the run budget. Override it with `--max-duration-seconds` or
`EDGAR_MOE_COPILOT_MAX_DURATION_SECONDS` when a private provider exercise
needs a different bounded allowance.

The same pre-call boundary also limits cumulative provider context. The agent
serializes the current UTF-8 messages and tool schemas, measures their byte
length, and rejects the next provider call when it exceeds the 512 KiB default.
The operator may choose a value from 16 KiB through the 2 MiB hard maximum with
`--max-context-bytes` or
`EDGAR_MOE_COPILOT_MAX_CONTEXT_BYTES`. The answer identity records this bound,
and usage retains only the numeric `peak_context_bytes`; prompts, tool payloads,
and the serialized context are never retained. This is a byte-budget safety
control, not a provider-specific token-count guarantee, and an already in-flight
request is not interrupted.

The aggregate also records `agent_identity_status`: `consistent` when every
evaluated answer carries the same verified policy/tool-contract identity,
`legacy` when all answers predate the identity field, and `mixed` when the
answers do not share one boundary. A consistent aggregate retains that one
non-secret identity; legacy and mixed aggregates retain `null` so they cannot
be mistaken for a reproducible run.

## Safety boundary

This is an operator workflow, not a public Vercel endpoint. The provider key
must remain in a local `.env` file or another private secret store. Do not add
`EDGAR_MOE_COPILOT_API_KEY` to the public deployment or browser bundle. The
copilot:

- does not fetch filing URLs or arbitrary web pages;
- does not receive raw filing bodies, source credentials, or licensed market
  payloads;
- cannot write forecasts, labels, registry rows, model artifacts, or GitHub;
- treats tool output as untrusted data and ignores instructions embedded in it;
- records an unknown or write-like tool request only as the neutral
  `rejected_tool_request` trace marker, never as an executable or allowlisted
  write capability; and
- binds every accepted citation digest to the exact canonical payload returned
  by its requested tool, rejecting mismatched result names or hashes; and
- rejects a final answer that follows an allowlisted evidence-tool call without
  a retained tool citation; and
- sends remote provider requests only to an exact configured hostname allowlist
  (loopback HTTP is the local-development exception); and
- uses a zero-temperature request, bounded timeout, response-size limit, and
  token/tool-call budgets, and rejects provider redirects; and
- must describe missing, pending, or negative evidence instead of inventing a
  metric or turning a research result into a recommendation.

The model is an explanation and evidence-navigation assistant. It does not
replace statistical evaluation, the frozen model identity checks, a human
review, or investment/legal/compliance advice.

### Review profiles

The same read-only agent can be run with a bounded review perspective. The
profile changes only the instructions and is recorded in the content-addressed
`agent_identity`; it never adds tools or write authority:

- `research` (default): concise evidence navigation;
- `quant`: point-in-time availability, leakage, label maturity, costs, and
  uncertainty;
- `architect`: system boundaries, source-of-truth choices, trust boundaries,
  SLOs, failure modes, and cost/reliability trade-offs; or
- `operations`: scheduler, deployment, database/artifact, alert, and recovery
  evidence.

For example, the architecture profile is useful when preparing a design review:

```bash
uv run edgar-moe research-copilot \
  --experimental-llm \
  --profile architect \
  "Which controls are observed, configured, or still unverified?"
```

Profiles are not separate autonomous actors and do not constitute a model
promotion or trading decision. The existing tool-call, duration, context,
citation, and envelope-verification limits apply unchanged. Reports created
before profile identities were introduced remain verifiable as legacy reports.

### Run a bounded specialist review panel

When one perspective is not enough for an architecture review, the operator can
run a small panel over the same question and read-only evidence boundary:

```bash
uv run edgar-moe research-copilot-panel \
  --experimental-llm \
  --profile quant \
  --profile architect \
  --profile operations \
  --output-dir /tmp/edgar-moe-copilot-panel \
  "Which controls are observed, configured, or still unverified?"
```

The panel is a bounded coordinator, not an autonomous trading system. It runs
the selected profiles sequentially, with each profile retaining its own verified
answer envelope (`quant.json`, `architect.json`, and so on). `panel.json` is a
content-addressed aggregate that contains only profile outcomes, answer hashes,
tool/citation counts, agent identities, coarse failure types, and bounded usage
telemetry. It does not contain answer text, prompts, provider payloads, or
credentials. A partial panel exits non-zero so an operator cannot mistake one
successful perspective for a complete review; a mixed frozen identity or tool
contract is marked `review_required`.

Verify the aggregate without reopening the child answer text:

```bash
uv run edgar-moe research-copilot-panel-verify \
  /tmp/edgar-moe-copilot-panel/panel.json
```

Use `--plan-only` to inspect the selected profiles and exact tool contract
without contacting a provider. The panel never changes the frozen v1 model,
prospective forecasts, registry, labels, deployment, or readiness decisions.

### Inspect a private forward diagnostic

An operator may explicitly provide one downloaded forward-cycle diagnostic to
the copilot. The agent receives only a validated summary of counts, maturity,
coverage, and short-horizon metrics; observations, event/forecast identifiers,
unmatched rows, and the local filesystem path are removed before the tool
result reaches the provider. The source artifact is represented by a SHA-256
digest in the citation.

```bash
uv run edgar-moe research-copilot \
  --experimental-llm \
  --diagnostic-path /private/path/diagnostic-2026-09-19.json \
  "Is the latest short-horizon diagnostic mature, and what remains pending?"
```

This capability is opt-in and operator-only. Without `--diagnostic-path` the
tool is not advertised to the model. Short-horizon diagnostics remain
research-only and never replace the official twenty-session evaluation.

To let the copilot compare repeated diagnostic observations, first build and
verify a history from private reports, then attach the retained history:

```bash
uv run edgar-moe forward-diagnostic-history \
  --report /private/path/diagnostic-2026-09-17.json \
  --report /private/path/diagnostic-2026-09-18.json \
  --report /private/path/diagnostic-2026-09-19.json \
  --output /tmp/forward-diagnostic-history.json

uv run edgar-moe research-copilot \
  --experimental-llm \
  --diagnostic-history-path /tmp/forward-diagnostic-history.json \
  "How has short-horizon diagnostic status changed across the retained observations?"
```

`--diagnostic-history-path` is explicitly opt-in, and the retained file is
independently verified before the `get_forward_diagnostic_history` result is
returned. The copilot receives only
the content-addressed history and its safe counts, maturity, metrics, horizons,
and source digests; it never reopens the private source reports or receives
raw observations, event/forecast identifiers, or filesystem paths. Invalid or
tampered history is reported as unavailable without exposing parser details.
The tool contract also states that `ready` means only the configured snapshot
count and report checks passed: snapshots may overlap, independence is not
assessed, the artifact does not record human review, and this history is never
model-promotion or retraining evidence.

## Run it locally

First inspect the exact tool contract without contacting a provider:

```bash
uv run edgar-moe research-copilot \
  --plan-only \
  "What does the frozen study measure and what are its limitations?"
```

For a remote provider, configure the key privately and keep the default HTTPS
endpoint, or set another OpenAI-compatible endpoint:

```bash
export EDGAR_MOE_COPILOT_API_KEY="..."
export EDGAR_MOE_COPILOT_MODEL="gpt-4o-mini"
uv run edgar-moe research-copilot \
  --experimental-llm \
  --output /tmp/edgar-moe-copilot.json \
  "Summarize the locked result, costs, and the limitations I should disclose."
```

For a different remote provider, configure its exact hostname explicitly before
running the command:

```bash
export EDGAR_MOE_COPILOT_ENDPOINT="https://api.example.com/v1/chat/completions"
export EDGAR_MOE_COPILOT_ALLOWED_HOSTS="api.example.com"
```

For a local runtime such as Ollama, use a loopback endpoint; plain HTTP to a
non-loopback host is rejected:

```bash
export EDGAR_MOE_COPILOT_ENDPOINT="http://localhost:11434/v1/chat/completions"
export EDGAR_MOE_COPILOT_MODEL="qwen2.5:7b"
uv run edgar-moe research-copilot --experimental-llm "Which controls are enforced versus pending?"
```

In the experimental LLM mode only, to include prospective status, provide a SELECT-only reader URL through
`EDGAR_MOE_REGISTRY_READ_DATABASE_URL` or `--database-url`. With no registry,
the answer explicitly reports that forward evidence is unavailable. The CLI
prefers the reader URL and only falls back to a local SQLite writer URL for
development convenience.

Private reports are intentionally not checked into Git. If a report is shared
for review, retain the JSON identity and citations and remove the question if
it contains personal or confidential context.

Before sharing or archiving a saved answer envelope, verify its integrity
offline. The verifier checks the research-only boundary, frozen identity,
timestamp, citation and tool-trace digests, read-only tool names, bounded
snapshot/API sources, and grounded-versus-uncited consistency. It never prints
the answer text or contacts the provider:

```bash
uv run edgar-moe research-copilot-verify \
  /tmp/edgar-moe-copilot-benchmark/governance-status.json
```

## Evaluate a private report

The repository includes a small reviewed corpus at
`config/copilot_eval_cases.json`. It checks observable contracts—expected tool
use, citation sources, grounded versus uncited status, and the research-only
envelope—without attempting to score whether an LLM's prose is factually true.
The evaluator is offline and never sends a report to a provider. It also stores
only answer hashes and structural observations in the aggregate output.

After running the copilot for one or more corpus questions, evaluate the saved
reports locally:

```bash
uv run edgar-moe research-copilot-eval \
  /tmp/copilot-summary.json \
  /tmp/copilot-methodology.json \
  --output /tmp/copilot-evaluation.json
```

Use `--require-complete` when the report is intended as a full corpus gate.
The command exits non-zero below the requested `--fail-under` pass rate (the
default is 100% for submitted reports). A passing structural score is not a
statistical evaluation, truth guarantee, or investment recommendation; review
the private answer text and citations before relying on it.

To run the complete corpus sequentially with one provider configuration, use
the bounded benchmark command. It writes the individual private answer
envelopes and an aggregate structural report under `/tmp` by default. When all
successful answers include verified usage summaries, the aggregate also records
the successful-answer count, provider-request count, and local duration; each
standard token total is summed only when every successful answer reports it,
otherwise that counter is `null`. This is an observability signal, not a billing
estimate or provider-pricing calculation:

```bash
uv run edgar-moe research-copilot-benchmark \
  --experimental-llm \
  --output-dir /tmp/edgar-moe-copilot-benchmark
```

Use `--case governance-status` (repeatable) to exercise a subset, or
`--plan-only` to list the selected cases without contacting the provider. A
provider error is recorded only by case id and coarse exception type; the
benchmark exits non-zero unless every selected case succeeds and meets the
requested pass rate. It creates answer-envelope directories with owner-only
`0700` permissions and writes JSON files with owner-only `0600` permissions;
symlinked or broadly accessible run directories are rejected before execution.
Benchmark, evaluation, comparison, and review writers also refuse output
paths inside a Git worktree.

### Deterministic control arm and paired comparison

The credential-free `research-copilot-baseline` uses a fixed keyword router
over the same read-only evidence tools and reviewed corpus. It returns direct
structured evidence, refuses trade requests, and abstains when no route
matches. This is a deliberately simple evidence-navigation control, not an
LLM or a substitute for human task review. Run it into a **separate private
directory** so its case envelopes cannot replace provider answers. Both
benchmark commands reject a non-empty output directory before any case runs;
use a fresh path for each run:

```bash
uv run edgar-moe research-copilot-baseline --plan-only
uv run edgar-moe research-copilot-baseline \
  --output-dir /tmp/edgar-moe-copilot-baseline
```

The fixed router passes the four existing corpus cases structurally, which
shows that this small corpus alone cannot establish incremental LLM value.
It does not establish prose usefulness, factual correctness, or a latency SLA.
Both benchmark arms now retain local per-case microsecond durations, the
content hash of the snapshot they read, and their read-only tool context.
For issue #284, the opt-in `--synthetic-only` provider mode is independent of
source-rights issue #280: it requires the exact lock-verified synthetic public
snapshot and rejects registry or diagnostic context. It still contacts the
configured provider, so the configured service receives the question and
synthetic snapshot-derived evidence. It does not clear rights for any
source-derived context. Compare only runs with the same corpus, selected case
IDs, snapshot hash, and tool context (the current baseline has no forward
registry or diagnostic files configured):

```bash
uv run edgar-moe research-copilot-compare \
  --baseline /tmp/edgar-moe-copilot-baseline/evaluation.json \
  --copilot /tmp/edgar-moe-copilot-benchmark/evaluation.json \
  --output /tmp/edgar-moe-copilot-comparison.json
```

The comparator emits only structural pass categories, per-case local durations,
aggregate latency summaries, and reported token/request counters. It refuses
mismatched identities or tool contexts, missing timings, or embedded answer text. These are not
price estimates, and latency comparisons require matched host/network
conditions. A blinded, held-out human task review is still needed to test
whether the LLM adds useful synthesis over direct navigation. Do not send
source-derived context to a provider while the
[source-rights review](https://github.com/hoangnguyen2003/edgar-moe/issues/280)
is unresolved. The baseline and comparator never contact a provider.

### Held-out, label-masked task review

The separate [eight-case held-out corpus](../config/copilot_holdout_cases.json)
was authored after the four-case development/control evaluation. Its reviewed
content hash is
`a3c988f5a7c14d8d770f640c7c57608c50b6cb0c963e162a0ce159ad902f1678`.
Do not tune either arm to these questions after seeing the results. This is a
publicly inspectable holdout, not an independent external test set. The
automated structural score is not a human judgment.

For a paired provider arm, use the predeclared holdout and exact same synthetic
snapshot/tool context. The provider command's `--synthetic-only` guard fails
closed if a registry, diagnostics, alternate snapshot, or alternate snapshot
lock is configured. It may incur provider charges; choose a suitable provider
spend limit before running it. Do not compare a four-case development run
against the eight-case holdout. These commands are examples only; they are not
run by CI:

```bash
uv run edgar-moe research-copilot-baseline \
  --corpus config/copilot_holdout_cases.json \
  --output-dir /tmp/holdout-baseline
uv run edgar-moe research-copilot-benchmark \
  --experimental-llm \
  --corpus config/copilot_holdout_cases.json \
  --output-dir /tmp/holdout-copilot \
  --synthetic-only \
  --max-tool-calls 4 \
  --max-retries 0
```

Prepare a masked review only after both complete successfully. This command
makes no provider calls:

```bash
uv run edgar-moe research-copilot-mask-review \
  --baseline-dir /tmp/holdout-baseline \
  --copilot-dir /tmp/holdout-copilot \
  --output-dir /tmp/holdout-masked-review
```

The command rejects incomplete or mismatched benchmark arms and verifies each
saved answer envelope, case question, snapshot identity, provider metadata,
and answer hash. It creates `packet.json` and `mapping.json` in a new
owner-only directory, with owner-only files; it never prints answer text. Give
the reviewer **only** `packet.json`. Keep `mapping.json` sealed until the
reviewer has recorded and finalized all ratings. The packet contains only a
digest of the paired comparison. The sealed mapping also carries the matching
structural outcomes, latency summaries, and reported provider-usage counters,
so these metrics cannot disclose the arm before human scoring. A/B order is
randomly balanced, but answer style can still disclose the arm, so this is
label masking rather than guaranteed blinding.

The reviewer creates a private JSON file with `schema_version: 1`,
`packet_sha256` from the packet-preparation output, `corpus_sha256` from the
packet, a short pseudonymous `reviewer` ID, timezone-aware `reviewed_at`, and
one entry per packet case. Each entry has `case_id`, `A`, and `B`; both ratings
must give `task_completion`, `factuality`, `citations`, and `safety` as `pass`
or `fail`, plus integer `usefulness` from 1 to 5. Score the two answers
independently against the question and visible citations; record the rubric
before viewing the mapping. No answer text belongs in the review JSON.

```bash
uv run edgar-moe research-copilot-score-masked-review \
  --packet /tmp/holdout-masked-review/packet.json \
  --mapping /tmp/holdout-masked-review/mapping.json \
  --review /tmp/holdout-review.json \
  --output /tmp/holdout-score.json
```

The scorer requires complete case coverage, exact schemas, and matching packet
and comparison hashes; it refuses overwrites and emits per-arm rubric counts,
paired win/tie/loss outcomes for each rubric dimension, overall paired quality,
per-case numeric judgments, structural outcome totals, latency summaries, and
provider usage where reported. The packet, mapping, raw
review, and score stay private; do not commit or publish them without a
separate disclosure/rights review. The hashes detect local mismatches, not
malicious alteration or independent provenance. One reviewer's descriptive
scores cannot establish general LLM value or investment efficacy. The
synthetic-only local operator run completed on 2026-10-07 with Qwen2.5 7B
(`Q4_K_M`) through a cloud-disabled, loopback-only Ollama runtime. Both arms
completed all eight predeclared cases against the same lock-verified synthetic
snapshot, without registry or diagnostic context, retries, or model selection
based on held-out outcomes. Private answers, metrics, and the label-masked
review packet remain outside Git; human assessment is still pending. Local
inference incurred no provider API fees and did not change hosted resources.
Source-derived provider use and publication remain subject to the separate
source-rights review. Closure of issue #280 alone is not source clearance.

## Human-review history

Structural evaluation does not establish that generated prose is useful. After
reviewing the private answer files, record one explicit rubric decision per
completed case in a separate private JSON file. The review batch must contain
the benchmark's case and answer hashes, four `pass`/`fail` fields
(`grounding`, `citations`, `safety`, and `prose`), a decision (`accept`,
`revise`, or `reject`), and optional allowlisted review codes. The
`benchmark_sha256` field may be omitted; the command computes and pins it from
the supplied aggregate. The file must not contain answer text, questions,
provider payloads, or free-form notes.

The review file uses this shape (replace the digest and timestamp values with
the private benchmark values):

```json
{
  "schema_version": 1,
  "corpus_id": "copilot-v1",
  "corpus_sha256": "<corpus sha256>",
  "benchmark_sha256": "<evaluation sha256>",
  "reviews": [
    {
      "case_id": "governance-status",
      "answer_sha256": "<answer sha256>",
      "reviewer": "operator",
      "reviewed_at": "2026-09-20T03:00:00Z",
      "grounding": "pass",
      "citations": "pass",
      "safety": "pass",
      "prose": "pass",
      "decision": "accept",
      "review_codes": []
    }
  ]
}
```

For example, a review batch can be applied with:

```bash
uv run edgar-moe research-copilot-review \
  --benchmark /tmp/edgar-moe-copilot-benchmark/evaluation.json \
  --review /path/to/private-review-batch.json \
  --history /tmp/edgar-moe-copilot-review-history.json \
  --minimum-reviews 4

uv run edgar-moe research-copilot-review-verify \
  /tmp/edgar-moe-copilot-review-history.json
```

The history is append-only at the command boundary: existing hashes and
chronological entries are verified before a new entry is added, duplicate
case/answer reviews are rejected, and the resulting status is
`insufficient_history`, `accepted`, `review_required`, or `rejected`. This is
an auditable quality record only; it cannot retrain the frozen model, change
forecasts, or authorize an investment decision.

Before relying on a benchmark in a research review, combine its aggregate
report and the private review history with the read-only readiness gate:

```bash
uv run edgar-moe research-copilot-readiness \
  --benchmark /tmp/edgar-moe-copilot-benchmark/evaluation.json \
  --history /tmp/edgar-moe-copilot-review-history.json \
  --min-pass-rate 1.0 \
  --output /tmp/edgar-moe-copilot-readiness.json
```

The gate revalidates both inputs, checks that the current benchmark answer
hashes are reviewed, and exits non-zero unless the benchmark is complete and
passing, the history is accepted, and the benchmark has a consistent agent
identity. Legacy or mixed agent identities produce `review_required` even when
the structural score and review history pass. Its content-addressed output
contains only check statuses, blocker codes, counts, and identities; it never
includes questions, answers, provider payloads, or credentials. A `ready`
result is a structural quality signal, not a truth guarantee, investment
recommendation, or permission to retrain the frozen v1 model.

## Failure behavior and verification

Invalid tool arguments are returned as a rejected read-only result; unknown
tools never execute. Provider errors expose only a coarse failure type, not
provider response bodies or authorization headers. Redirect responses fail
closed and are not retried. Exceeding the tool-call budget fails the command
rather than allowing an unbounded loop.

The contract is covered by unit tests that exercise endpoint validation,
prompt/tool boundaries, citation hashes, unconfigured forward status, and the
tool-call budget. The offline evaluation contract is covered separately and
does not require an API key. The benchmark is the only command in this layer
that contacts an LLM, and it remains operator-run. Use the normal repository
checks before opening a PR:

```bash
uv run ruff check .
uv run mypy src
uv run pytest --cov=edgar_moe --cov-report=term-missing --cov-fail-under=80
```
