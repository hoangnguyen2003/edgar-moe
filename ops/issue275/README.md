# Private paired XBRL study (#275)

This case rebuilds the legacy and duration-aware datasets from the retained
2026-07-31 authenticated checkpoint. The original v1 dataset, frozen model and
locked result are preserved. No provider credentials, network refresh, database,
R2 store or forecast workflow is used.

The owner reported that the parties agreed and explicitly asked to resolve this
issue for a personal portfolio. `private-study-scope.json` records that attestation
and the narrow new-study scope; it is not independently inspected provider
correspondence and does not release the normal CLI or publication gates.

The operator entrypoint is `scripts/run_private_paired_study.py`. It checks the
scope/configuration/checkpoint hashes and exact runtime, and refuses replacement
of existing evidence. Its two stages are:

1. `prepare`: build two new dataset identities using the same cached immutable
   FinBERT model/tokenizer revision and one new shared embedding cache. Verify
   the input-attribution audit, including entry AND label horizon before
   2025-01-01. Any non-XBRL change prevents the isolated-policy comparison.
2. Commit `prepared.json` as `input-commitment.json` here before `run`. The full
   study uses both 2023/2024 folds, all 33 roster members, the original 80-epoch
   cap/early stopping and seed 42. Retain selections, OOF hashes, paired uncertainty,
   10/25/50 bps costs and failed hypotheses in the private case directory.

Use the existing pinned Python environment and cached model only. Run with
`PYTHONPATH=src`, `HF_HUB_OFFLINE=1`, `TRANSFORMERS_OFFLINE=1` and `HF_HOME` pointing
to the retained local cache. On this Mac the execution is additionally wrapped in
`/usr/bin/sandbox-exec -p '(version 1) (allow default) (deny network*)'`.
The encoder uses existing local MPS hardware; model fitting uses CPU. Both sides
have identical runtime and settings. No paid resources are provisioned.

Artifacts stay owner-only under the ignored
`data/artifacts/issue275-private-run-20261104` directory in the shared checkout.
No private rows, model weights, financial numerical results or provider payloads
are included in this change. Hash commitments and operational protocol facts are
retained for traceability. The evidence-only audit supplements actual numerical
recomputation; its `content_bound_only` result alone does not establish validity.

This is exploratory development evidence after frozen v1's outcome was known.
Historical membership remains unverified (#292), so results describe only the
same fixed cohort and cannot establish unbiased historical performance or a
profitable alpha. V2 remains pending. Any allowlisted aggregate publication needs
a later separate review.

## Completed private execution (2026-11-04)

The two new datasets each retain 5,961 events. The strict input audit compares
4,010 eligible pre-2025 events and isolates the XBRL policy change. Its hash
commitment was pushed in commit `28a11768ebbd2a7145c8b50018e6768d72eabb73`
before either model roster started. Both full 33-model studies completed,
with matching text-only/market-only negative controls and zero locked predictions.
The numerical review recomputed saved OOF metrics and retained paired uncertainty,
all original cost scenarios and explicit failed hypotheses.

`completion-commitment.json` binds the private reports, selections and OOF archives
without publishing financial results. All 48 content-chain checks passed; this
supplements the actual numerical reviews, raw-checkpoint verification and
chronology/preservation checks recorded in the private closeout. The old pair's
missing provenance remains unresolved and was not retrospectively rewritten.

Frozen v1, every original dataset asset and the locked artifact remain unchanged.
All new outputs have owner-only permissions. The execution denied network access,
made zero provider requests and performed no registry writes. The private
interpretation remains part of the sealed case; v2 and public aggregates remain
pending separate review.
