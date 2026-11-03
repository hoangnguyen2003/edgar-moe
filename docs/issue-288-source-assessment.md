# Issue #288: source assessment, 2026-11-02

**Status: owner-reported approval bound to a private verification profile on
2026-11-03; live verification pending.** The owner states that all parties have agreed. No provider
response wording, sender/date or saved approval reference has yet been supplied
in this checkout. This records the owner's report, not a verified provider
determination. The owner has authorized completing the original acceptance
criteria, including live verification. Another generic run approval is
unnecessary. Following the owner's further instruction to proceed, the private
operator path records that attestation as its permission basis without claiming
independent inspection of provider correspondence. The general source-use and
public-release decision remains unresolved.

The owner describes an individual, personal resume project with no commercial
use. Scope this verification to private research and private diagnostic
retention. Public provider-derived outputs are not needed to prove checkpoint
recovery. Publish only operational results and evidence hashes once verified.

## Source findings

These are engineering findings from the linked primary sources, not a grant
of permission or a determination about past use. Terms were checked on
2026-11-02; account-specific agreements may differ.

| Input / route | Finding | Decision for this cycle |
| --- | --- | --- |
| FRED/ALFRED: `VIXCLS`, `DGS10`, `DFF`, `BAA10Y` | [FRED terms](https://fred.stlouisfed.org/legal/terms/), prohibited-use section and API clauses (k)–(l), restrict software/ML use and storage. Personal use remains subject to these restrictions. | Existing acquisition route remains held. Obtain a source-specific determination; the owner's purpose alone does not resolve the conflict. |
| Board-origin rates obtained directly from the Federal Reserve Board | The [Board notice](https://www.federalreserve.gov/disclaimer.htm) permits copying and distribution of Board website information unless otherwise indicated, with attribution. [H.15](https://www.federalreserve.gov/releases/h15/) includes effective federal funds and Treasury constant-maturity rates. | A replacement candidate for `DFF`/`DGS10`, acquired independently of FRED. Preserve actual release/observation chronology. This does not clear third-party rates or retained FRED inputs. |
| VIX and Moody's Baa inputs | [Cboe](https://www.cboe.com/tradable_products/vix/vix_historical_data) publishes historical VIX data; download availability alone is not this project's permission record. The [Philadelphia Fed](https://www.philadelphiafed.org/surveys-and-data/bond) explicitly identifies historical Moody's Aaa/Baa rates as proprietary. | No complete, independently cleared replacement for the four-series feature set was established in this review. |
| Alpaca/IEX bars, actions and asset metadata | [Alpaca Terms and Conditions](https://files.alpaca.markets/disclosures/library/TermsAndConditions.pdf), personal/noncommercial and content sections, support that purpose while restricting publication/distribution. The [customer agreement](https://files.alpaca.markets/disclosures/library/AcctAppMarginAndCustAgmt.pdf), section 30, additionally restricts reproduction/distribution of market data. | Do not require a commercial subscription just because this is software. Bind the actual account/feed agreement to the private checkpoint/storage use before clearing it; API access alone is not that determination. |
| SEC submissions, filings and Company Facts | [SEC developer guidance](https://www.sec.gov/about/developer-resources) supports efficient automated access with bot identification and a total limit of 10 requests/second. | Retain identified, rate-limited access. Access guidance is not a blanket license for issuer-authored text or public republication. |
| `ProsusAI/finbert` | The current [model card](https://huggingface.co/ProsusAI/finbert/blob/main/README.md) lists CC BY-NC-SA 4.0 for weights, attribution and noncommercial conditions, with separate upstream-data caveats. | Bind the actual model/tokenizer revision and applicable license, not a moving model-card URL. The stated purpose is relevant; do not substitute the code repository's license for the weights license. |

## Why changing two URLs does not unblock frozen v1

`config/forward.yaml` pins model `edgar-moe-frozen-v1` to training dataset
`research-2026-07-31-3553e7ad78dd`, selection hash
`0bce6d674607af4e6f8e0930332923d1ab8f9c5409c63634c678ad4b67f1f906`,
and model SHA-256
`5cf77b2a8de255eb959236a48caac1f6c5d429767e250976c0c31324ef0bcd2c`.
That dataset used the four FRED/ALFRED series. Direct Board acquisition for
future observations would not clear those retained training inputs.

A replacement path must rebuild the affected dataset, feature schema and model
under new identities and preserve its availability evidence. Dropping VIX or
the Baa spread, renaming another rate as either series, or using current revised
values as historical initial releases would not preserve this contract.
Frozen v1 and its existing records must remain unchanged.

The owner reports that all parties have agreed. The
[private scope record](../ops/issue288/private-verification-scope.json) binds
that report to the exact configuration hashes, source profile, frozen model,
cached FinBERT revision and existing registry/storage target fingerprints.
This is an owner attestation, not inspected provider correspondence. The
[prepared inquiry](issue-288-fred-inquiry.txt)
documents the requested scope; this agent has not sent it. FRED cannot supply
another data owner's permission. Do not infer unrestricted publication or
general research clearance from approval of a private verification cycle.

## Local readiness checked on 2026-11-03

Checked the existing implementation at commit
`27d746c9580c0cc109463b19922d88796704d87b`, without changing runtime gates:

| Check | Observed result |
| --- | --- |
| Frozen runtime bundle validator | Passed; configured identities and retained bundle agree. |
| Actual inference model file | SHA-256 matches `config/forward.yaml`. No model deserialization or inference performed. |
| Training dataset manifest | Dataset identity matches the frozen configuration. No training-data rows loaded. |
| Existing refresh, runner, registry, diagnostic and evidence-manifest tests | 67 passed. Their PostgreSQL-only test was initially skipped, then run separately below. Synthetic fixtures; no data-provider calls. |
| Existing PostgreSQL append-only trigger test | Passed against migrated PostgreSQL 18 using a non-superuser owner and synthetic records. The disposable instance was stopped and removed afterward. |
| Required runtime configuration | All nine required variable names have nonempty assignments in the current process or local dotenv files. This is a presence check, not authentication, account permission or service-reachability validation. |

Together, 68 distinct existing tests passed. Local logs, JUnit results,
identity checks and a verified 16-file evidence inventory are retained in the
operator workspace at
`data/artifacts/issue288-readiness-20261103-65m7qxel/`.
The SHA-256 of `evidence-manifest.json` is
`03f28baef89399f00a2e47f509e6f86c15dd16e68048d038b05b78aef49f59f5`.
This packet is local readiness evidence, not a production-cycle diagnostic
or satisfaction of the live acceptance criteria.

## Dedicated private verification path

`scripts/verify_private_forward_cycle.py` is an explicit exception for this
owner-attested private cycle. It uses the actual production refresh, dataset,
forecast/settlement, diagnostics and artifact components; it does not set the
general source decision to cleared, monkeypatch a gate, or enable a scheduled
workflow. The source record is validated before loading credentials. Changed
inputs or registry/storage targets fail closed. New training and publication
remain excluded. All output is placed in a new private artifact directory.

The verifier interrupts after observed successful SEC responses and persisted
partial checkpoint work, then resumes the same request. It also verifies that
completed-checkpoint reuse is immutable and makes no provider requests. The
before/after registry comparison rejects changed or deleted prior rows.
Actual provider payloads and model outputs stay in the private evidence packet.

The supported invocation is:

```sh
PYTHONPATH=src /path/to/project/.venv/bin/python -B scripts/verify_private_forward_cycle.py \
  --repository-root /path/to/project
```

If an attempt stops, retain its output and use `--resume-output` with that same
directory. A verified successful output is checked without running another
cycle. Provider error messages and credentials are not printed.

## Acceptance evidence still required

Issue [#288](https://github.com/hoangnguyen2003/edgar-moe/issues/288) remains
open. PR [#287](https://github.com/hoangnguyen2003/edgar-moe/pull/287) and its
offline tests establish a code repair, not the following live results:

1. Record the permitted acquisition/use decision against the exact source
   manifest and dataset/model identities in a reviewed change. Update the
   runtime and workflow hold only for the scope actually cleared.
2. Capture the existing forward records' identities and canonical payload
   hashes before the run. Use the real refresh path and record its request
   contract before cache seeding.
3. Interrupt one partial checkpoint after the request exists; retain its
   evidence and resume that same directory with the same request. Record
   successful refresh and completed-checkpoint reuse without request changes.
4. Run forecast and settlement through the production runner. Compare the
   previous records against the before-run hashes and identify appended records.
   An empty append is not affirmative evidence of an append.
5. Retain the new diagnostic and source/dataset manifests with hashes, run
   identity and timestamps. Keep private payloads private. Close the issue
   only when these results are observed.

No provider refresh, model training, forecast, settlement, workflow activation,
or GitHub issue mutation was performed as part of this assessment. The
`unresolved` runtime source-use decision remains unchanged.
