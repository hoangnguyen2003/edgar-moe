# ADR 0034: Keep public serving synthetic while source rights are reviewed

- Status: accepted
- Date: 2026-09-29
- Deciders: project maintainer

## Context

The repository's historical v1 snapshot and prospective v2 registry were
previously reachable through anonymous public pages and API routes. Source and
redistribution rights for those research-derived outputs have not been
confirmed. Merely replacing the primary snapshot would not close the boundary:
the application could still query prospective records from Postgres, and an
older deployment function bundle could include the archived evidence catalog.

This decision addresses current application serving only. It does not establish
that any source or output is lawful to retain, redistribute, or use privately.
Historical reports, the catalog, Git objects, workflow artifacts, caches, and
prior deployments may still contain research output and require separate
retention and rights review.

## Decision

- The public application serves a content-addressed synthetic fixture only.
- The public application does not connect to the forward registry. The public
  status route reports a fixed `withheld_review` policy, and routes for
  prospective runs, forecasts, performance, and quality return `410 Gone` with
  `Cache-Control: no-store`.
- The frontend explains that prospective records are withheld; it does not
  present the policy state as a database outage or imply that private records
  are absent.
- Deployment validation excludes the archived evidence catalog and private
  research data from the serving function bundle.
- Private runner, registry, migration, auditor, and local diagnostic workflows
  remain separate, least-privileged operational paths. This publication hold
  does not authorize private data use.
- Do not describe this change as a repository purge, artifact deletion, legal
  clearance, or resolution of source-rights questions.

## Alternatives considered

- **Keep the existing public research output while reviewing rights.** This
  leaves the unresolved redistribution decision active for every visitor.
- **Replace only the snapshot.** The function could still serve derived
  registry records or package the archived evidence catalog.
- **Disable the whole website and API.** This prevents the useful software demo
  from remaining accessible even though a synthetic-only surface is available.
- **Delete repository history and retained artifacts now.** This is a separate,
  potentially destructive retention operation with broader scope and is not
  necessary to stop current application serving; it requires an inventory and
  explicit retention decision.

## Consequences and limits

The public demo remains available, while historical and prospective research
claims are not served by the current app. Forward performance, status, and
quality cannot be observed through its public API during the hold. Operators
must use authorized private workflows for operational checks. The UI/API status
is a publication policy, not proof of the registry's health or contents.

This is a containment control, not a rights determination or complete takedown.
Existing deployments may continue to serve old content until the replacement
deployment is confirmed, and retained history/artifacts may remain. Any broader
purge or access revocation needs a separate inventory, owner, and verification.

## Verification

`tests/unit/test_public_snapshot_lock.py` and `scripts/verify_public_snapshot_lock.py`
pin the synthetic fixture identity. `tests/integration/test_forward_api.py`
asserts no public registry connection and non-cacheable withholding responses.
`tests/unit/test_deployment_contract.py` and
`scripts/validate_deployment_contract.py` assert the function exclusions.
`scripts/smoke_deployment.py` checks the live deployment's governance and
withheld-route contract without retaining response bodies. A successful
production smoke is required after deployment; it does not verify deletion of
historical copies.
