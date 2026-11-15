/** One bounded read of retained Cron metadata. Never invokes or changes a Worker. */
import {createHash} from "node:crypto";
import {mkdirSync, writeFileSync} from "node:fs";
import {dirname, resolve} from "node:path";
import {pathToFileURL} from "node:url";

const ACCOUNT_SHA = "d55626b5e28b4d2ac50ff058e39cd722f2779d300ca7db10f76e3e3d7966d386";
const WORKER = "edgar-moe-forward-scheduler";
const START = "2026-11-07T00:00:00.000Z";
const END = "2026-11-12T00:00:00.000Z";
const LIMIT = 100;
const QUERY = `query PrivateCronEvents($account: string!, $from: Time!, $to: Time!) {
  viewer { accounts(filter: {accountTag: $account}) {
    accountTag
    workersInvocationsScheduled(limit: 100, filter: {
      scriptName: "edgar-moe-forward-scheduler", datetime_geq: $from, datetime_leq: $to
    }, orderBy: [datetime_DESC]) { scriptName cron datetime scheduledDatetime status }
  } }
}`;
const OUTCOMES = new Set(["ok", "success", "exception", "failure", "exceededCpu", "exceededMemory", "canceled", "unknown"]);

// Classify provider errors in memory; never retain their messages or arbitrary
// paths. Failure categories cannot establish that the query or access works.
function queryFailureCategories(errors) {
  if (!Array.isArray(errors) || errors.length > 16) return ["unknown"];
  const categories = new Set();
  for (const error of errors) {
    const message = typeof error?.message === "string" ? error.message.slice(0, 4096) : "";
    if (/not authorized|unauthorized|forbidden|permission|does not have access|authentication/i.test(message)) {
      categories.add("access_refused");
    } else if (/unknown (?:type|field|argument)|cannot query field|invalid (?:type|argument)|variable.*(?:type|invalid)/i.test(message)) {
      categories.add("query_contract_refused");
    } else if (/cannot request data older|query time range is too large|limit.*exceed|too many.*(?:fields|records)/i.test(message)) {
      categories.add("dataset_limit_refused");
    } else if (/unable to execute query|too many queries in progress|temporarily unavailable/i.test(message)) {
      categories.add("service_unavailable");
    } else categories.add("unknown");
  }
  return [...categories].sort();
}

function stamp(value) {
  if (typeof value !== "string" || !/^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,3})?Z$/.test(value) ||
      !Number.isFinite(Date.parse(value))) throw new Error("event_time_refused");
  const normalized = new Date(value).toISOString();
  if (normalized.slice(0, 19) !== value.slice(0, 19)) throw new Error("event_time_refused");
  return normalized;
}

async function boundedJson(response) {
  if (!response.ok) throw new Error("metadata_http_refused");
  if (!response.body) throw new Error("metadata_body_refused");
  const reader = response.body.getReader();
  const chunks = [];
  let size = 0;
  try {
    while (true) {
      const {done, value} = await reader.read();
      if (done) break;
      size += value.byteLength;
      if (size > 65536) throw new Error("metadata_size_refused");
      chunks.push(value);
    }
    return JSON.parse(Buffer.concat(chunks).toString("utf8"));
  } finally {
    await reader.cancel();
    reader.releaseLock();
  }
}

export async function inspectPrivateCronEvents({env, now = new Date(), fetchImpl = globalThis.fetch,
  expectedAccountSha256 = ACCOUNT_SHA}) {
  const report = {schema_version: 1, status: "refused", scope: "issue156_fixed_cron_metadata",
    request_count: 0, external_writes: 0, worker_invocations: 0,
    forecast_margin_proven: false, dispatch_origin_proven: false, events: []};
  try {
    const account = env.CLOUDFLARE_ACCOUNT_ID;
    const token = env.CLOUDFLARE_API_TOKEN;
    if (!/^[a-f0-9]{32}$/.test(account ?? "") || !token ||
        createHash("sha256").update(JSON.stringify(account)).digest("hex") !== expectedAccountSha256) {
      throw new Error("account_refused");
    }
    if (!(now instanceof Date) || !Number.isFinite(now.getTime()) || now < new Date(START) || now >= new Date(END)) {
      throw new Error("diagnostic_window_refused");
    }
    report.window = {starts_at: START, ends_at: now.toISOString()};
    report.request_count = 1;
    const response = await fetchImpl("https://api.cloudflare.com/client/v4/graphql", {
      method: "POST", redirect: "error", signal: AbortSignal.timeout(15000),
      headers: {Authorization: `Bearer ${token}`, "Content-Type": "application/json"},
      body: JSON.stringify({query: QUERY, variables: {account, from: START, to: now.toISOString()}}),
    });
    const document = await boundedJson(response);
    if (document.errors != null && (!Array.isArray(document.errors) || document.errors.length)) {
      report.query_failure_categories = queryFailureCategories(document.errors);
      throw new Error("metadata_query_refused");
    }
    const accounts = document.data?.viewer?.accounts;
    if (!Array.isArray(accounts) || accounts.length !== 1 || accounts[0].accountTag !== account) {
      throw new Error("metadata_account_refused");
    }
    const events = accounts[0].workersInvocationsScheduled;
    if (!Array.isArray(events) || events.length > LIMIT) throw new Error("event_inventory_refused");
    const normalized = events.map(event => {
      if (event?.scriptName !== WORKER) throw new Error("event_worker_refused");
      const observed = stamp(event.datetime);
      const scheduled = stamp(event.scheduledDatetime);
      if (observed < START || observed > now.toISOString() || scheduled < START || scheduled > now.toISOString()) {
        throw new Error("event_window_refused");
      }
      return {observed_at: observed, scheduled_at: scheduled,
        expected_cron: event.cron === "17 7 * * TUE-SAT",
        outcome: OUTCOMES.has(event.status) ? event.status : "unrecognized"};
    });
    report.events = normalized;
    report.retained_event_limit = LIMIT;
    report.inventory_may_be_truncated = events.length === LIMIT;
    report.event_absence_proves_no_invocation = false;
    report.status = "passed";
  } catch (error) {
    const reasons = new Set(["account_refused", "diagnostic_window_refused", "metadata_http_refused", "metadata_body_refused",
      "metadata_size_refused", "metadata_query_refused", "metadata_account_refused", "event_inventory_refused",
      "event_worker_refused", "event_time_refused", "event_window_refused"]);
    report.reason = reasons.has(error?.message) ? error.message : "metadata_read_refused";
  }
  return report;
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  const [output] = process.argv.slice(2);
  if (!output) throw new Error("Missing receipt output path");
  const report = await inspectPrivateCronEvents({env: process.env});
  mkdirSync(dirname(output), {recursive: true, mode: 0o700});
  writeFileSync(output, JSON.stringify(report, null, 2) + "\n", {flag: "wx", mode: 0o600});
  console.log(JSON.stringify(report));
  if (report.status !== "passed") process.exitCode = 1;
}
