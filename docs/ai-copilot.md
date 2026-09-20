# Evidence-grounded research copilot

EDGAR-MoE includes an optional operator-run LLM/agentic layer for explaining the
research record. It is deliberately not part of the forecasting path: the
frozen v1 model, prospective forecast registry, labels, and deployment state are
all outside the copilot's write boundary.

## What it does

The `research-copilot` command sends a question to an OpenAI-compatible
chat-completions endpoint and gives the model seven bounded, read-only tools:

- frozen snapshot identity;
- study summary and cost scenarios;
- methodology and limitations;
- model-selection experiments;
- bounded searches of derived filing events;
- one derived event by accession number; and
- governance plus optional forward-registry status.

The agent can make at most four tool calls by default. Each tool result is
content-hashed and returned with a citation. The JSON answer envelope retains
the question, answer, frozen identity, citations, and a non-sensitive tool
trace. A report with no tool citations is marked `uncited`; it is not silently
treated as evidence.

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
- uses a zero-temperature request, bounded timeout, response-size limit, and
  token/tool-call budgets; and
- must describe missing, pending, or negative evidence instead of inventing a
  metric or turning a research result into a recommendation.

The model is an explanation and evidence-navigation assistant. It does not
replace statistical evaluation, the frozen model identity checks, a human
review, or investment/legal/compliance advice.

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
  --output /tmp/edgar-moe-copilot.json \
  "Summarize the locked result, costs, and the limitations I should disclose."
```

For a local runtime such as Ollama, use a loopback endpoint; plain HTTP to a
non-loopback host is rejected:

```bash
export EDGAR_MOE_COPILOT_ENDPOINT="http://localhost:11434/v1/chat/completions"
export EDGAR_MOE_COPILOT_MODEL="qwen2.5:7b"
uv run edgar-moe research-copilot "Which controls are enforced versus pending?"
```

To include prospective status, provide a SELECT-only reader URL through
`EDGAR_MOE_REGISTRY_READ_DATABASE_URL` or `--database-url`. With no registry,
the answer explicitly reports that forward evidence is unavailable. The CLI
prefers the reader URL and only falls back to a local SQLite writer URL for
development convenience.

Private reports are intentionally not checked into Git. If a report is shared
for review, retain the JSON identity and citations and remove the question if
it contains personal or confidential context.

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

## Failure behavior and verification

Invalid tool arguments are returned as a rejected read-only result; unknown
tools never execute. Provider errors expose only a coarse failure type, not
provider response bodies or authorization headers. Exceeding the tool-call
budget fails the command rather than allowing an unbounded loop.

The contract is covered by unit tests that exercise endpoint validation,
prompt/tool boundaries, citation hashes, unconfigured forward status, and the
tool-call budget. The offline evaluation contract is covered separately and
does not require an API key. Use the normal repository checks before opening a
PR:

```bash
uv run ruff check .
uv run mypy src
uv run pytest --cov=edgar_moe --cov-report=term-missing --cov-fail-under=80
```
