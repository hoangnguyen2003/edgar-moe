/** Fixed-resource state checks and one new signing secret. No credential output. */
import { createHash } from "node:crypto";
import { mkdirSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { pathToFileURL } from "node:url";

const WORKER = "edgar-moe-forward-scheduler";
const ID = "d8be7a3ac48547e98a48e99f1eb145a1";
const ACCOUNT_SHA = "d55626b5e28b4d2ac50ff058e39cd722f2779d300ca7db10f76e3e3d7966d386";
const CRON = "17 7 * * 2-6";
export async function managePrivateScheduler({phase, env, expectedAccountSha256 = ACCOUNT_SHA, fetchImpl = globalThis.fetch}) {
let requests = 0;
let writes = 0;
const report = {status: "refused", phase, request_count: 0, external_writes: 0,
  cron_enabled: false, private_urls_verified: false, dispatch_secret_present: false,
  signature_secret_present: false, forecast_margin_proven: false};
try {
  if (!["BEFORE_PREPARE", "INSTALL_KEY", "BEFORE_ACTIVATE", "VERIFY_DISABLED", "VERIFY_ACTIVE", "BEFORE_STOP"].includes(phase)) throw new Error("phase_refused");
  const account = env.CLOUDFLARE_ACCOUNT_ID;
  const token = env.CLOUDFLARE_API_TOKEN;
  if (!/^[a-f0-9]{32}$/.test(account ?? "") || !token ||
      createHash("sha256").update(JSON.stringify(account)).digest("hex") !== expectedAccountSha256) throw new Error("account_refused");
  const base = `https://api.cloudflare.com/client/v4/accounts/${account}`;
  async function request(path, method = "GET", body = null) {
    requests++;
    if (method !== "GET") writes++;
    const response = await fetchImpl(base + path, {method, redirect: "error", signal: AbortSignal.timeout(15000),
      headers: {Authorization: `Bearer ${token}`, "Content-Type": "application/json"},
      ...(body === null ? {} : {body: JSON.stringify(body)})});
    if (!response.ok) throw new Error(`api_http_${response.status}`);
    const text = await response.text();
    if (Buffer.byteLength(text) > 32768) throw new Error("response_limit");
    const result = JSON.parse(text);
    if (result.success !== true) throw new Error("api_refused");
    return result.result;
  }
  const worker = await request(`/workers/workers/${ID}`);
  if (worker?.id !== ID || worker.name !== WORKER || worker.subdomain?.enabled !== false ||
      worker.subdomain?.previews_enabled !== false || worker.observability?.enabled !== false ||
      worker.logpush !== false || !Array.isArray(worker.tail_consumers) || worker.tail_consumers.length) throw new Error("worker_boundary_refused");
  report.private_urls_verified = true;
  const schedules = await request(`/workers/scripts/${WORKER}/schedules`);
  if (!Array.isArray(schedules?.schedules)) throw new Error("schedule_response_refused");
  const crons = schedules.schedules.map(item => item.cron);
  report.cron_enabled = crons.length === 1 && crons[0] === CRON;
  const expectActive = ["VERIFY_ACTIVE", "BEFORE_STOP"].includes(phase);
  if (expectActive ? !report.cron_enabled : crons.length !== 0) throw new Error("schedule_boundary_refused");
  const secrets = await request(`/workers/scripts/${WORKER}/secrets`);
  if (!Array.isArray(secrets) || secrets.some(item => item.type !== "secret_text" ||
      !["GITHUB_TOKEN", "SCHEDULER_SIGNATURE_KEY"].includes(item.name))) throw new Error("secret_metadata_refused");
  report.dispatch_secret_present = secrets.some(item => item.name === "GITHUB_TOKEN");
  report.signature_secret_present = secrets.some(item => item.name === "SCHEDULER_SIGNATURE_KEY");
  if (!report.dispatch_secret_present) throw new Error("dispatch_secret_missing");
  if (phase === "INSTALL_KEY") {
    if (report.signature_secret_present || !/^[a-f0-9]{64}$/.test(env.SCHEDULER_SIGNATURE_KEY ?? "")) throw new Error("signing_key_install_refused");
    await request(`/workers/scripts/${WORKER}/secrets`, "PUT", {
      name: "SCHEDULER_SIGNATURE_KEY", type: "secret_text", text: env.SCHEDULER_SIGNATURE_KEY});
    report.signature_secret_present = true;
  } else if (phase !== "BEFORE_PREPARE" && !report.signature_secret_present) throw new Error("signing_key_missing");
  report.status = "passed";
} catch (error) {
  report.reason = /^([a-z_]+|api_http_\d{3})$/.test(error.message ?? "") ? error.message : "state_check_failed";
}
report.request_count = requests;
report.external_writes = writes;
return report;
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
const [phase, output] = process.argv.slice(2);
if (!output) throw new Error("Missing receipt output path");
const report = await managePrivateScheduler({phase, env: process.env});
mkdirSync(dirname(output), {recursive: true, mode: 0o700});
writeFileSync(output, JSON.stringify(report, null, 2) + "\n", {flag: "wx", mode: 0o600});
console.log(JSON.stringify(report));
if (report.status !== "passed") process.exitCode = 1;

}
