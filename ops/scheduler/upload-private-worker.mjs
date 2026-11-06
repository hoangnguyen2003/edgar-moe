/** Upload the credential-free bundle through the documented Worker-scoped API. */
import {createHash} from "node:crypto";
import {mkdirSync, readFileSync, readdirSync, writeFileSync} from "node:fs";
import {dirname, join, resolve} from "node:path";
import {pathToFileURL} from "node:url";
import {managePrivateScheduler} from "./manage-private-scheduler.mjs";

const WORKER = "edgar-moe-forward-scheduler";
const CRON = "17 7 * * TUE-SAT";
const VARS = {GITHUB_REPOSITORY: "hoangnguyen2003/edgar-moe",
  GITHUB_WORKFLOW: "forward-private-scheduler.yml", GITHUB_REF: "main", GITHUB_DEVICE: "cpu"};
const SECRETS = ["GITHUB_TOKEN", "SCHEDULER_SIGNATURE_KEY"];

export async function uploadPrivateWorker({stage, source, env, fetchImpl = globalThis.fetch, expectedAccountSha256}) {
  const report = {status: "refused", stage, code_uploaded: false, schedule_updated: false,
    external_writes: 0, api_error_codes: [], forecast_margin_proven: false};
  try {
    if (!["PREPARE", "RESUME_PREPARE", "ACTIVATE", "STOP"].includes(stage) ||
        typeof source !== "string" || Buffer.byteLength(source) > 32768 || !source.includes("export")) throw new Error("bundle_or_stage_refused");
    const before = await managePrivateScheduler({phase: stage === "STOP" ? "BEFORE_STOP" : "BEFORE_ACTIVATE",
      env, fetchImpl, ...(expectedAccountSha256 ? {expectedAccountSha256} : {})});
    if (before.status !== "passed") throw new Error("private_worker_precondition_refused");
    const base = `https://api.cloudflare.com/client/v4/accounts/${env.CLOUDFLARE_ACCOUNT_ID}/workers/scripts/${WORKER}`;
    async function request(url, method = "GET", body = null) {
      if (method !== "GET") report.external_writes++;
      const response = await fetchImpl(url, {method, redirect: "error", signal: AbortSignal.timeout(30000),
        headers: {Authorization: `Bearer ${env.CLOUDFLARE_API_TOKEN}`,
          ...(body instanceof FormData ? {} : {"Content-Type": "application/json"})},
        ...(body === null ? {} : {body: body instanceof FormData ? body : JSON.stringify(body)})});
      const raw = await response.text();
      if (Buffer.byteLength(raw) > 65536) throw new Error("api_response_limit");
      const payload = JSON.parse(raw);
      if (!response.ok || payload.success !== true) {
        report.api_error_codes = (payload.errors ?? []).map(item => item.code).filter(Number.isInteger);
        throw new Error(`worker_api_http_${response.status}`);
      }
      return payload.result;
    }
    if (stage !== "STOP") {
      const settings = await request(base + "/settings");
      const expected = [...Object.keys(VARS), ...SECRETS].sort();
      if (!Array.isArray(settings?.bindings) ||
          JSON.stringify(settings.bindings.map(binding => binding.name).sort()) !== JSON.stringify(expected) ||
          settings.bindings.some(binding => SECRETS.includes(binding.name) ? binding.type !== "secret_text" : binding.type !== "plain_text")) {
        throw new Error("unexpected_existing_bindings");
      }
      const metadata = {main_module: "private-forward-scheduler.mjs", compatibility_date: "2026-09-22",
        bindings: [...Object.entries(VARS).map(([name, text]) => ({name, type: "plain_text", text})),
          ...SECRETS.map(name => ({name, type: "inherit", version_id: "latest"}))],
        logpush: false, observability: {enabled: false}, tail_consumers: []};
      const body = new FormData();
      body.set("metadata", new Blob([JSON.stringify(metadata)], {type: "application/json"}));
      body.set("private-forward-scheduler.mjs", new Blob([source], {type: "application/javascript+module"}), "private-forward-scheduler.mjs");
      await request(base + "?bindings_inherit=strict", "PUT", body);
      report.code_uploaded = true;
      report.module_sha256 = createHash("sha256").update(source).digest("hex");
    }
    // The PUT request is a bare array; only the GET response wraps `schedules`.
    await request(base + "/schedules", "PUT", stage === "ACTIVATE" ? [{cron: CRON}] : []);
    report.schedule_updated = true;
    report.cron_requested = stage === "ACTIVATE";
    report.status = "uploaded_pending_state_verification";
  } catch (error) {
    report.reason = /^[a-z0-9_]+$/.test(error.message ?? "") ? error.message : "scoped_upload_failed";
  }
  return report;
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  const [directory, output] = process.argv.slice(2);
  const modules = readdirSync(directory).filter(name => /\.m?js$/.test(name));
  if (modules.length !== 1 || !output) throw new Error("Expected exactly one credential-free bundle");
  const report = await uploadPrivateWorker({stage: process.env.STAGE,
    source: readFileSync(join(directory, modules[0]), "utf8"), env: process.env});
  mkdirSync(dirname(output), {recursive: true, mode: 0o700});
  writeFileSync(output, JSON.stringify(report, null, 2) + "\n", {flag: "wx", mode: 0o600});
  console.log(JSON.stringify(report));
  if (report.status !== "uploaded_pending_state_verification") process.exitCode = 1;
}
