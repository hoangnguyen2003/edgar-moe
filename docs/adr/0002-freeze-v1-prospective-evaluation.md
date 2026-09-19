# ADR 0002: Freeze v1 and evaluate future changes prospectively

Date: 2026-09-19  
Status: Accepted; provider-side operational evidence remains pending

## Context

The authenticated v1 study is a historical research result, not a model that
should be silently retrained as new filings and market observations arrive. A
new dataset, preprocessing change, threshold, or model artifact can change the
meaning of an observed result. Re-running the old experiment in place would
make it difficult to distinguish a genuine prospective observation from a
revised backtest.

The system therefore has two different evidence lifecycles:

- the frozen historical snapshot and model artifacts support reproducible
  explanation of the v1 result;
- the forward registry records new forecasts before their market-entry time and
  appends outcomes only after the evaluation horizon has matured.

The repository must preserve that distinction even when a later model appears
to perform better or a provider operation fails halfway through a run.

## Decision

### Preserve one immutable v1 identity

The v1 identity is the tuple of model ID/version, training dataset identity and
manifest hash, selection/configuration hashes, frozen artifact digest, locked
result digest, and public snapshot lock. A change to any member is a new
reviewed identity, not an edit to v1. The runtime and CI cross-check these
members before a forward run or public build:

- `scripts/validate_frozen_runtime.py` verifies the frozen model bundle;
- `ops/frozen/SHA256SUMS` and `config/public_snapshot.lock.json` pin bytes;
- `scripts/verify_public_snapshot_lock.py` rejects unreviewed public changes and
  cross-checks the published `data-provenance.json` identity;
- the forward workflow copies the verified model into a new run workspace.

### Evaluate future observations in an append-only prospective lane

The forward lane must:

1. build a point-in-time dataset with source and component hashes;
2. timestamp and persist forecasts before `entry_at`;
3. never update or delete a recorded forecast, model, or dataset identity;
4. settle outcomes only after the declared horizon is observable;
5. retain run, quality, artifact, and audit transitions when a later step fails;
6. evaluate drift and performance as new observations, without mutating the
   frozen v1 result or automatically retraining it.

The registry state machine, database immutability listeners, content-addressed
artifacts, pre-entry checks, and independent Go auditor enforce or verify these
rules. A partial database/object-store write is a failed run to reconcile, not
permission to rewrite the forecast.

### Treat drift history as evidence, not a promotion gate by itself

`research-drift` compares the frozen components with a later prospective
dataset. `research-drift-history` verifies child hashes, chronological dataset
IDs, one frozen model identity, and warning streaks. Fewer than the configured
minimum observations remains `insufficient_history`; a drift warning does not
authorize retraining or threshold changes. A future model can be introduced
only under a new model identity with a reviewed prospective evaluation plan.

### Keep infrastructure observations outside software claims

Repository tests and CI prove the software contract. Provider grants, managed
backup timing, RPO/RTO, object-store retention, alert delivery, and abuse
controls require redacted operator evidence. The operator packet and readiness
gate must remain blocked or stale until those observations are independently
recorded; synthetic fixtures and green CI must not be substituted for them.

## Alternatives considered

| Alternative | Rejected because |
| --- | --- |
| Retrain and overwrite the v1 artifact | Destroys the historical denominator and makes old claims non-reproducible. |
| Re-run a larger backtest whenever data changes | Blurs prospective evidence with hindsight and can leak later availability. |
| Promote a new model after one favorable forward run | One observation cannot establish stability, cost behavior, or operational safety. |
| Add a second service or queue for model governance | The current append-only registry and workflow boundaries solve the integrity requirement without recurring infrastructure cost. |

## Consequences

Positive consequences:

- historical CV/portfolio claims remain reproducible;
- every future forecast has a timestamp, lineage, and eventual outcome path;
- model improvements can be compared without changing the control result;
- operational failures remain inspectable instead of being hidden by retries.

Costs and limitations:

- the registry and evidence store require ongoing retention and reconciliation;
- meaningful drift/performance conclusions take multiple prospective cycles;
- provider-side readiness cannot be marked complete from repository evidence;
- a new model requires explicit identity, review, and a separate evaluation record.

## Verification

The following checks provide repository evidence for this decision:

```bash
uv run python scripts/validate_frozen_runtime.py
uv run python scripts/verify_public_snapshot_lock.py
uv run pytest -q tests/unit/test_frozen_runtime.py tests/unit/test_frozen_persistence.py
uv run pytest -q tests/unit/test_forward_registry.py tests/integration/test_forward_workflow.py
uv run edgar-moe research-drift-history \
  --report reports/research-drift-2026-08-06.json \
  --output /tmp/research-drift-history-check.json
```

These commands verify repository behavior only. The provider evidence packet
and readiness command remain the authoritative record for external controls.

## Revisit conditions

Revisit this decision only when a reviewed requirement needs a new model family,
an authenticated serving tier, materially different recovery guarantees, or a
measured workload that the current boundaries cannot support. Any change must
introduce a new model/version identity, preserve the v1 artifacts, and include a
prospective comparison plan before promotion.
