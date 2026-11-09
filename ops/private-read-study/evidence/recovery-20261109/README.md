# November 9 collection recovery

Run [42533739899](https://github.com/hoangnguyen2003/edgar-moe/actions/runs/42533739899)
retained two successful HTTP/native reads, followed by a refused second platform
trace lookup (`trace_transport_refused`). It was therefore degraded, and the
existing failure job disabled the observer. The failure receipt is retained;
there is no evidence that the underlying failure was a transient timeout rather
than another transport refusal, because the old collector discarded that detail.

The new collector classifies a limited set of timeouts, connection resets,
temporary DNS failures and HTTP 500/502/503/504 responses as temporary. Only the
trace **GET** may retry, within the same existing maximum of three lookups.
Authentication, TLS verification, throttling, explicit Retry-After, malformed
contracts and unknown failures stop. Session creation and application reads
are never retried. Exhausted lookups still produce degraded evidence and stop
the observer; no failure is relabeled as success.

The endpoint receipts independently show idle before the read pair and active
afterward. Previously this evidence was suppressed by the overall degraded
platform status. The corrected correlator checks two successful, ordered native
samples and preserves the database transition at pair scope. It retains the
missing paired platform classification, degraded overall status and absence
of a per-request restart timestamp.

The attached [offline receipt](offline-recorrelation-receipt.json) binds the
three original input hashes, correlator source and separately saved output.
It performed **zero network/application requests**, left original receipts
unchanged, and still returned `degraded`. This reinterpretation is separate
from the original scheduled artifact and does not replace it in the finalizer.

The existing absolute window remains **November 6 08:15 UTC through November 13
08:15 UTC, 2026**. Re-enabling after the tested fix reaches main permits only
future scheduled observations in that window. Do not rerun the failed slot,
backfill missing hours, reset reader expiry, extend the window, or count manual
verification as scheduled evidence. Existing missing slots prevent this run
from meeting its predeclared 168-pair coverage target. The final report must
retain that limitation; this fix does not meet issue 290's acceptance criteria
or establish an SLO.
