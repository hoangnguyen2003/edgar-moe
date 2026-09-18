## Summary

<!-- What changed, and why? Link an issue or decision record when applicable. -->

## Change category

- [ ] Feature or product behavior
- [ ] Bug fix
- [ ] Research / evaluation
- [ ] Operations / deployment
- [ ] Documentation / tooling

## Validation

<!-- List exact commands and CI checks. Include data or artifact identities when relevant. -->

- [ ] Local checks run
- [ ] CI checks are green

## Architecture and safety

- **Boundary affected:** <!-- Python / web / CI / database / R2 / public bundle / none -->
- **Data and trust impact:** <!-- credentials, external sources, append-only evidence -->
- **Failure behavior:** <!-- what happens when dependencies fail? -->
- **Rollback or recovery:** <!-- how is this safely reversed or reconciled? -->

## Research integrity

- [ ] No prospective outcome was used to retrain or tune the frozen v1 model.
- [ ] Official 20-session evaluation remains distinct from diagnostics.
- [ ] No secrets, private raw data, or local exports are committed.
- [ ] Documentation/ADR updated if a contract or architecture boundary changed.

## Reviewer notes

<!-- Call out known limitations, follow-up work, and decisions reviewers should focus on. -->
