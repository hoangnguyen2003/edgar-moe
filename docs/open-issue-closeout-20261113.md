# Open-issue evidence and recovery, November 13, 2026

This review covers every open issue in `hoangnguyen2003/edgar-moe`: 156, 290
and 292. It preserves their original acceptance criteria. None is complete.
No paid resource, replacement observation, source-backed forecast or new study
window was created during this review.

| Issue | Verified result | Required to finish |
| --- | --- | --- |
| [156: recording margin](https://github.com/hoangnguyen2003/edgar-moe/issues/156) | The November 7/10/11 scope has zero eligible forecast observations. The closeout is incomplete. Provider cleanup returned HTTP 401. | Renew the existing Worker's scoped credential, verify delivery and dispatch credentials, then bind a separate finite future scope and obtain actual nonzero forecasts with the required pre-open margin. |
| [290: hosted read evidence](https://github.com/hoangnguyen2003/edgar-moe/issues/290) | The seven-day policy expired November 13 at 08:15 UTC. Only 12 pairs / 24 HTTP reads on November 6–8 were observed; 156 of 168 modeled slots are missing. The observer and finalizer are now disabled. | Restore and verify reliable future delivery before binding a new independently identified study and SELECT-only credential window. Collect at least seven real days and review attribution, errors, safe plans and environment-separated comparisons. |
| [292: historical candidates](https://github.com/hoangnguyen2003/edgar-moe/issues/292) | The retained append-only identity evidence remains 487/503, with 16 unresolved candidates. Historical market continuity and a new checkpoint/processed-dataset chain are unverified. | Resolve the remaining source identities and class/continuity evidence, then bind and verify the complete new source-to-selection-to-dataset chain under the reviewed source protocol. |

The current token was refused for GraphQL analytics on November 9. The expired
scheduler cleanup then returned HTTP 401 on November 12 and the November 13
retry. Resource access and analytic access are distinct: do not infer that one
passing implies the other is available. Changing an expired token requires the
account owner's credential update; the existing workflow cannot grant itself
new permissions. Do not paste credentials into issues, artifacts or chat.

## Retained study, independently reanalyzed

The original finalizer run
[43425554275](https://github.com/hoangnguyen2003/edgar-moe/actions/runs/43425554275)
successfully retained its packet and disabled both study workflows. That means
finalization succeeded; it does not mean measurement acceptance succeeded.
Its status was `incomplete` and all original receipts remain intact.

The [separate reanalysis](../ops/private-read-study/evidence/closeout-20261113/summary.md)
verifies the original artifact hashes and uses the actual Actions run metadata.
Its [JSON](../ops/private-read-study/evidence/closeout-20261113/summary.json)
binds the original summary/manifest, run metadata and new summarizer code.
All original cohort quantiles, HTTP counts, missing slots, identities and
absolute window remain identical. Reanalysis made no application/provider
requests and did not replace the original scheduled artifacts.

There were zero HTTP/contract errors among the 24 recorded reads, **one refused
platform trace**, one degraded pair and one failed observation workflow. The
pair-start span was 220,539.413535 seconds on three UTC dates. The reporting fix
exposes these separate failure and coverage facts in both JSON and Markdown;
zero HTTP errors must not hide metadata failures or imply seven measured days.
Native local distributions and all hosted collector/route/phase/platform/
database-state/process groups remain separate. There is no supported SLO,
production-scale or alpha claim.

## Cleanup acknowledgement

The old closeout submitted a STOP workflow, then disabled itself without
checking whether STOP succeeded. Its child run
[43287234137](https://github.com/hoangnguyen2003/edgar-moe/actions/runs/43287234137)
failed before any Cloudflare write. The November 13 retry
[43461322417](https://github.com/hoangnguyen2003/edgar-moe/actions/runs/43461322417)
retained the same HTTP 401 refusal. These are failures, not verified shutdowns.
The finite Worker date guard is independently past its final permitted date;
no later source-backed run is admitted under that scope.

The cleanup fix dispatches only `STOP` to the fixed main-branch management
workflow. It uses GitHub's versioned dispatch acknowledgement to bind an exact
run ID, then checks that run's repository, branch, event, workflow, attempt,
source commit and terminal conclusion. Queued, failed, foreign, ambiguous or
timed-out runs cannot verify STOP. Polling has both a three-minute deadline and
36-read cap; an ambiguous dispatch is never repeated.

A separate cleanup receipt is redaction-checked, hashed and retained even on
failure. The expired closeout is disabled independently, and failed cleanup
makes the cleanup job fail after evidence retention. The coordinator receives
only its existing Actions credential, never the Cloudflare or database secret.
No cutoff, filing coverage, forecast data, source-use hold, frozen v1 bundle or
append-only forward record is changed.

## Historical evidence remains separate

The latest three historical evidence packets contain 2,851 preserved files.
They must be verified against their immutable manifests before further source
work. An incorporated legacy governing-reference path was reviewed during this
audit; it did not establish a new verified class or continuity anchor. No
preferred/common/ordinary/capital-stock equivalence, class label, voting right,
source CIK correction or candidate exclusion is inferred to force completion.
The existing provider-publication gate remains in force.

Neither an expired window nor an empty forecast scope can be repaired by
waiting. A replacement must have fresh identifiers and future dates, retain
the failed predecessors and validate prerequisites before new observations.
