# Architecture and data flow

## Invariants

1. Source payloads are immutable and content-hashed.
2. A feature is valid only when `available_at <= accepted_at` for its filing event.
3. Market features use observations through the previous completed session.
4. XBRL facts use the SEC filing timestamp, not the fiscal period end.
5. Model preprocessing is fitted independently inside each training fold.
6. The locked test is evaluated after the model family, features, costs, and constraints are frozen.
7. Deployed services read an immutable derived snapshot and never need raw licensed market data.

## Storage layers

- `raw`: cached source responses, ignored by Git.
- `interim`: parsed filings, security mappings, and normalized facts.
- `processed`: partitioned Parquet feature and label tables.
- `artifacts`: model states, preprocessors, experiment manifests, and reports.
- `demo`: small public snapshot consumed by FastAPI and React.

DuckDB acts as the local catalog and query engine. Each written asset records its path, row count, SHA-256 digest, and creation timestamp.

## Failure behavior

- SEC requests identify the application, run below the published maximum rate, retry transient failures, and cache filing HTML.
- Ambiguous CIK/security mappings receive a confidence status; low-confidence events are excluded from primary results.
- Missing text or fundamentals activate a modality mask rather than zero-valued evidence.
- Missing or unresolved terminal corporate actions censor the primary label and appear in an attrition report.
- A failed weekly refresh leaves the last successful public snapshot intact and marks it stale.

## Deployment boundary

Training uses PyTorch and Transformers locally or in a free GPU notebook. The deployed FastAPI function contains no training stack; it serves compact JSON derived from the frozen research run. The React application performs visualization and filtering but no model inference or order routing.
