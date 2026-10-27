# Isolated Linux kernel and TLS proof

This source-only CI fixture addresses the remaining Linux native-package and
libpq TLS branches of the portable #290 kernel. It creates no hosted study,
cloud target, admission authority, scheduler or live traffic window.

The branch-scoped workflow uses a standard Ubuntu runner and a fresh PostgreSQL
18 service container. It installs only the existing 12-distribution hash-pinned
dependency lock and assembles the 15-file application closure from its reviewed
Git commit. A fresh Python 3.12 reader interpreter runs with isolation enabled;
application files must load from the built bundle, research packages are absent,
and Node/subprocess execution is unavailable in that reader.

The owner fixture refuses outside explicit Linux GitHub Actions execution,
direct new ignored artifact paths and a runner-provided hexadecimal container
identity. It first requires an otherwise empty PostgreSQL cluster. Its only
database address is `127.0.0.1:5432`; no environment database URL is used.

The fixture creates per-run test certificate authorities and a hostname-bound
server certificate, enables TLS on that local container, and creates a new
SELECT-only SCRAM role with two allowed connections. The reader receives only
that role's generated password through stdin; it receives no owner password.
The owner password is a public CI-only service-fixture value.

The deliberately invented Neon-shaped hostname exercises the kernel's hosted
metadata branch, while explicit `hostaddr=127.0.0.1` pins every connection to the
local service. It performs no Neon DNS lookup or Neon connection. Four actual
kernel calls cover status, page, fixed filter and performance with
`sslmode=verify-full` and `channel_binding=require`. Negative cases check a
different hostname, an unrelated CA, unverified SSL mode and disabled channel
binding. The last two must refuse before any kernel statement; driver/dialect
initialization remains a separate operation class.

The nine-table fixture is deliberately empty except for migration metadata.
It verifies packaging, native imports, TLS authentication and actual query/DTO
execution. It is not a representative-volume, uncertainty-bootstrap, platform
cold-start, database-resume, Neon-TLS or hosted latency proof. Existing larger
local and uncertainty-regime packets remain separate and unchanged.

Postflight compares the complete schema and all table digests, verifies no
reader sessions remain, drops the new database and role, and removes the host
key directory. Actions owns service-container teardown. Only allowlisted JSON
receipts and the source inventory are uploaded for seven days; no password,
private key, raw response body or private dataset is uploaded.

The workflow is limited to the dedicated source-proof branch, has read-only
repository permissions, no repository secrets, no deployment command, no cron
and an eight-minute job deadline. Repository visibility was verified public
before running it. This uses standard public-repository Actions infrastructure,
not an account-specific cloud deployment allowance.

Completion of #290 still requires a reviewed complete hosted adapter and
authority/observer lifecycle, separate target/window approval, representative
safe plans, complete budgets, provider attribution, seven actual hosted days,
latency/error comparisons and then SLO decisions. No issue is closed by this
packaging proof.
