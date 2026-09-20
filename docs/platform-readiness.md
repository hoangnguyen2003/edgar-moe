# Cross-domain platform readiness

The individual readiness gates answer different questions: whether the public
bundle is structurally releasable, whether provider evidence is current, whether
the private copilot has passed its reviewed corpus, and whether prospective drift
has enough stable observations. `build_platform_readiness.py` composes those
already-verified summaries into one decision for an architecture review.

It is intentionally a decision aid, not a deployment or research authorization.
The aggregate never opens source reports after verification, copies answer text,
provider URLs, credentials, raw payloads, or feature data, and never authorizes
retraining or changes the frozen v1 model.

## Build and verify

Pass any retained reports that exist. An omitted report is recorded as an
explicit `missing` control and blocks the aggregate; this makes an incomplete
review visible instead of silently treating it as a pass.

```bash
uv run python scripts/build_platform_readiness.py \
  --public-release-report /tmp/public-release-readiness.json \
  --operator-report /tmp/operator-readiness.json \
  --copilot-report /tmp/edgar-moe-copilot-readiness.json \
  --drift-report /tmp/research-drift-readiness.json \
  --output /tmp/platform-readiness.json

uv run python scripts/verify_platform_readiness.py \
  --report /tmp/platform-readiness.json
```

The builder verifies each supplied report with its subsystem verifier before it
retains only the report scope, readiness status, content hash, and safe reason
codes. The output is content-addressed and refuses to overwrite an existing
file. A structurally valid `blocked` or `review_required` report is retained and
verifiable; a valid report is not evidence that the underlying control is
complete.

## Decision policy

| Control status | Platform effect |
| --- | --- |
| `ready` | No additional reason is emitted |
| `review_required` | Platform status is `review_required` unless another control blocks it |
| `stale`, `blocked`, or `missing` | Platform status is `blocked` |

The platform status is therefore `ready` only when all four controls are
explicitly ready. A missing provider packet, an unresolved redistribution
review, an unreviewed copilot answer corpus, or insufficient drift history stays
visible in the aggregate. `v1_immutable: true` and `automatic_retraining: false`
are part of the retained schema so the report cannot be mistaken for a model
promotion gate.

This report belongs in an architecture review packet alongside the source
reports. It does not replace provider-side observations, legal approval, human
review of generated prose, or statistical evaluation of future forecasts.
