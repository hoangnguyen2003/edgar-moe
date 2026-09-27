# ADR 0032: Reject invalid screen chronology before provider access

Date: 2026-09-27. Status: accepted. Supersedes the diagnostic-generation portion
of [ADR 0031](0031-universe-observation-chronology.md); its historical-membership
limitation remains.

## Context

The version-3 verifier rejected a candidate screen when the reviewed master
was observed after its historical cutoff. The `screen-universe` command did not
perform that check before fetching market bars, so an operator could spend
provider calls and retain an invalid research-looking CSV. A backdated run can
still be useful to diagnose data or software, but it must not be confused with
a historically valid research screen.

## Decision

Normal screening validates the captured master and checks that its observation
preceded the end of the requested cutoff day in New York and that the cutoff
day has finished. These checks run **before** data credentials are read or Alpaca is
called, then run again before writing output. Inputs remain hash-pinned across
the fetch. The version-4 audit has an explicit `research_screen` purpose.

The operator may opt into `--allow-retrospective-diagnostic`, but must supply
non-default CSV and audit paths. Its audit purpose is
`retrospective_diagnostic`; `verify-universe-screen` rejects it unconditionally.
The verifier also checks the same chronology and requires screen generation
before the first validation year. Older audits cannot be silently upgraded.

## Alternatives considered

- Let the later verifier reject every invalid screen: it still wastes provider
  calls and leaves a plausible-looking CSV available for accidental reuse.
- Ban retrospective diagnostics entirely: that removes a useful private
  debugging tool without improving historical source availability.
- Interpret local timestamps as proof of a complete historical security
  master: they are self-recorded and do not prove delisted or renamed membership.

## Consequences and verification

Synthetic tests assert that a late master or unfinished cutoff fails before
credential/provider access, that input mutation during fetch fails, that
diagnostics cannot target the default research paths, and that the verifier
rejects a diagnostic audit. This change does **not** establish historical
candidate membership. A reviewed archived master and a new study are still
needed for [issue #292](https://github.com/hoangnguyen2003/edgar-moe/issues/292);
source-rights review [#280](https://github.com/hoangnguyen2003/edgar-moe/issues/280)
also remains open. Frozen v1 and prospective records are unchanged.
