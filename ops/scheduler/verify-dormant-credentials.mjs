/** Read-only, bounded credential check. Never uploads code or invokes a Worker. */
import { createHash } from "node:crypto";
import { mkdirSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { pathToFileURL } from "node:url";

const WORKER_NAME = "edgar-moe-forward-scheduler";
const MAX_RESPONSE_BYTES = 32768;

class Refusal extends Error {
  constructor(reason, httpStatus = null) {
    super("Credential check refused");
    this.reason = reason;
    this.httpStatus = httpStatus;
  }
}

async function readJson(response) {
  if (!response.body) throw new Refusal("invalid_api_response");
  const reader = response.body.getReader();
  const chunks = [];
  let size = 0;
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      size += value.byteLength;
      if (size > MAX_RESPONSE_BYTES) {
        await reader.cancel();
        throw new Refusal("response_size_limit");
      }
      chunks.push(Buffer.from(value));
    }
    return JSON.parse(Buffer.concat(chunks).toString("utf8"));
  } finally {
    reader.releaseLock();
  }
}

export async function verifyDormantCredentials({
  accountId, apiToken, expectedAccountSha256, workerId,
  fetchImpl = globalThis.fetch,
}) {
  let requests = 0;
  const report = {
    schema_version: 1,
    status: "refused",
    worker_name: WORKER_NAME,
    request_count: 0,
    external_writes: 0,
    http_status: null,
    reason: "invalid_configuration",
    account_match: false,
    worker_match: false,
    dormant: false,
    editor_write_permission_proven: false,
    exclusive_scope_proven: false,
    billing_verified: false,
  };
  try {
    if (!/^[a-f0-9]{32}$/.test(accountId ?? "") ||
        !/^[a-f0-9]{32}$/.test(workerId ?? "") ||
        !/^[a-f0-9]{64}$/.test(expectedAccountSha256 ?? "") ||
        typeof apiToken !== "string" || apiToken.length < 20 || /\s/.test(apiToken)) {
      throw new Refusal("invalid_configuration");
    }
    // Match the bootstrap receipt's digest of the JSON-encoded Account ID.
    const actualAccountSha = createHash("sha256").update(JSON.stringify(accountId)).digest("hex");
    if (actualAccountSha !== expectedAccountSha256) throw new Refusal("account_mismatch");
    report.account_match = true;
    const target = `https://api.cloudflare.com/client/v4/accounts/${accountId}/workers/workers/${workerId}`;
    async function get(suffix) {
      if (!["", "/versions?page=1&per_page=1"].includes(suffix) || requests >= 2) {
        throw new Refusal("request_boundary");
      }
      requests++;
      const response = await fetchImpl(target + suffix, {
        method: "GET",
        headers: { Authorization: `Bearer ${apiToken}`, Accept: "application/json" },
        redirect: "error",
        signal: AbortSignal.timeout(15000),
      });
      if (!response.ok) {
        const reason = response.status === 401 ? "authentication_refused" :
          response.status === 403 ? "permission_refused" : "api_request_failed";
        throw new Refusal(reason, response.status);
      }
      const payload = await readJson(response);
      if (payload?.success !== true) throw new Refusal("invalid_api_response");
      return payload;
    }
    const worker = (await get("")).result;
    if (worker?.id !== workerId || worker?.name !== WORKER_NAME) throw new Refusal("worker_mismatch");
    report.worker_match = true;
    if (worker.subdomain?.enabled !== false || worker.subdomain?.previews_enabled !== false ||
        worker.logpush !== false || worker.observability?.enabled !== false ||
        !Array.isArray(worker.tail_consumers) || worker.tail_consumers.length !== 0) {
      throw new Refusal("worker_not_dormant");
    }
    // No version means no uploaded executable code, version bindings, or deployed scheduler.
    const versions = await get("/versions?page=1&per_page=1");
    if (!Array.isArray(versions.result) || versions.result.length !== 0 ||
        (versions.result_info?.total_count !== undefined && versions.result_info.total_count !== 0)) {
      throw new Refusal("worker_has_versions");
    }
    report.status = "passed";
    report.reason = "authenticated_read_and_dormant_worker_verified";
    report.dormant = true;
  } catch (error) {
    report.reason = error instanceof Refusal ? error.reason : "request_or_response_failed";
    report.http_status = error instanceof Refusal ? error.httpStatus : null;
  }
  report.request_count = requests;
  return report;
}

async function main() {
  const output = process.argv[2];
  if (!output || process.argv.length !== 3) {
    console.error("Usage: node ops/scheduler/verify-dormant-credentials.mjs <new-report-path>");
    process.exitCode = 1;
    return;
  }
  const report = await verifyDormantCredentials({
    accountId: process.env.CLOUDFLARE_ACCOUNT_ID,
    apiToken: process.env.CLOUDFLARE_API_TOKEN,
    expectedAccountSha256: process.env.EXPECTED_ACCOUNT_SHA256,
    workerId: process.env.EXPECTED_WORKER_ID,
  });
  try {
    mkdirSync(dirname(resolve(output)), { recursive: true, mode: 0o700 });
    writeFileSync(output, JSON.stringify(report, null, 2) + "\n", { flag: "wx", mode: 0o600 });
  } catch {
    console.error("Credential check receipt could not be retained; nothing overwritten.");
    process.exitCode = 1;
    return;
  }
  console.log(JSON.stringify({ status: report.status, reason: report.reason, request_count: report.request_count }));
  process.exitCode = report.status === "passed" ? 0 : 1;
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  main().catch(() => {
    console.error("Credential check stopped; error details withheld.");
    process.exitCode = 1;
  });
}
