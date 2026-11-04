/** Three fixed GETs; verifies inert preparation and secret names, never secret values. */
import { createHash } from "node:crypto";
import { mkdirSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { pathToFileURL } from "node:url";

const WORKER = "edgar-moe-forward-scheduler";
const WORKER_ID = "d8be7a3ac48547e98a48e99f1eb145a1";
const ACCOUNT_HASH = "d55626b5e28b4d2ac50ff058e39cd722f2779d300ca7db10f76e3e3d7966d386";
const LIMIT = 32768;
class Refusal extends Error {
  constructor(reason, status = null) {
    super("Prepared scheduler check refused");
    this.reason = reason;
    this.status = status;
  }
}
async function boundedJson(response) {
  if (!response.body) throw new Refusal("invalid_api_response");
  const reader = response.body.getReader();
  const chunks = [];
  let size = 0;
  try {
    while (true) {
      const {done, value} = await reader.read();
      if (done) break;
      size += value.byteLength;
      if (size > LIMIT) {
        await reader.cancel();
        throw new Refusal("response_size_limit");
      }
      chunks.push(Buffer.from(value));
    }
    return JSON.parse(Buffer.concat(chunks).toString("utf8"));
  } finally { reader.releaseLock(); }
}
export async function verifyPreparedScheduler({accountId, apiToken, expectedAccountSha256 = ACCOUNT_HASH, fetchImpl = globalThis.fetch}) {
  const report = {
    schema_version: 1, status: "refused", reason: "invalid_configuration", http_status: null,
    worker_name: WORKER, request_count: 0, external_writes: 0, worker_match: false,
    private_urls_verified: false, cron_disabled: false, dispatch_secret_present: false,
    dispatch_credential_validity_proven: false, scheduler_invocations: 0,
    data_provider_requests: 0, forecast_margin_proven: false,
  };
  try {
    if (!/^[a-f0-9]{32}$/.test(accountId ?? "") || typeof apiToken !== "string" ||
        apiToken.length < 20 || /\s/.test(apiToken)) throw new Refusal("invalid_configuration");
    if (createHash("sha256").update(JSON.stringify(accountId)).digest("hex") !== expectedAccountSha256) {
      throw new Refusal("account_mismatch");
    }
    const base = `https://api.cloudflare.com/client/v4/accounts/${accountId}`;
    const paths = [
      `/workers/workers/${WORKER_ID}`,
      `/workers/scripts/${WORKER}/schedules`,
      `/workers/scripts/${WORKER}/secrets`,
    ];
    async function get(index) {
      if (index !== report.request_count || index >= paths.length) throw new Refusal("request_boundary");
      report.request_count++;
      const response = await fetchImpl(base + paths[index], {
        method: "GET", headers: {Authorization: `Bearer ${apiToken}`, Accept: "application/json"},
        redirect: "error", signal: AbortSignal.timeout(15000),
      });
      if (!response.ok) throw new Refusal(response.status === 401 ? "authentication_refused" :
        response.status === 403 ? "permission_refused" : "api_request_failed", response.status);
      const payload = await boundedJson(response);
      if (payload?.success !== true) throw new Refusal("invalid_api_response");
      return payload.result;
    }
    const worker = await get(0);
    if (worker?.id !== WORKER_ID || worker?.name !== WORKER) throw new Refusal("worker_mismatch");
    report.worker_match = true;
    if (worker.subdomain?.enabled !== false || worker.subdomain?.previews_enabled !== false ||
        worker.logpush !== false || worker.observability?.enabled !== false ||
        !Array.isArray(worker.tail_consumers) || worker.tail_consumers.length !== 0) {
      throw new Refusal("private_worker_boundary_changed");
    }
    report.private_urls_verified = true;
    const schedules = await get(1);
    if (!Array.isArray(schedules?.schedules)) throw new Refusal("invalid_schedule_response");
    if (schedules.schedules.length !== 0) throw new Refusal("cron_is_enabled");
    report.cron_disabled = true;
    const secrets = await get(2);
    if (!Array.isArray(secrets) || secrets.length > 1 || secrets.some(item =>
      item?.name !== "GITHUB_TOKEN" || item.type !== "secret_text" ||
      Object.keys(item).some(key => !["name", "type"].includes(key)))) {
      throw new Refusal("unexpected_secret_metadata");
    }
    report.dispatch_secret_present = secrets.length === 1;
    report.status = "passed";
    report.reason = "prepared_without_cron_verified";
  } catch (error) {
    report.reason = error instanceof Refusal ? error.reason : "request_or_response_failed";
    report.http_status = error instanceof Refusal ? error.status : null;
  }
  return report;
}
async function main() {
  const output = process.argv[2];
  if (!output || process.argv.length !== 3) throw new Error("invalid output argument");
  const report = await verifyPreparedScheduler({
    accountId: process.env.CLOUDFLARE_ACCOUNT_ID, apiToken: process.env.CLOUDFLARE_API_TOKEN,
  });
  mkdirSync(dirname(resolve(output)), {recursive: true, mode: 0o700});
  writeFileSync(output, JSON.stringify(report, null, 2) + "\n", {flag: "wx", mode: 0o600});
  console.log(JSON.stringify({status: report.status, reason: report.reason, request_count: report.request_count}));
  process.exitCode = report.status === "passed" ? 0 : 1;
}
if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  main().catch(() => { console.error("Prepared scheduler check stopped; details withheld."); process.exitCode = 1; });
}
