import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { setTimeout as wait } from "node:timers/promises";
import { pathToFileURL } from "node:url";
import { observeScheduledEvent } from "./study-delivery-tail.mjs";

const WORKER = "edgar-moe-study-delivery";
const MODULE = "study-delivery-worker.mjs";
const CRON = "17 * * * *";
const MINUTE = 60_000;
const HOUR = 60 * MINUTE;
const CREDENTIALS = ["STUDY_DELIVERY_GITHUB_TOKEN", "STUDY_DELIVERY_SIGNING_KEY"];

async function checkGitHub(token, send) {
  if (!/^[A-Za-z0-9_]{20,512}$/.test(token || "")) {
    throw new Error("delivery_github_configuration_refused");
  }
  const url = "https://api.github.com/repos/hoangnguyen2003/edgar-moe/actions/workflows/study-external-delivery.yml/dispatches";
  const response = await send(url, {
    method: "POST",
    redirect: "error",
    signal: AbortSignal.timeout(15_000),
    headers: {
      Authorization: `Bearer ${token}`,
      Accept: "application/vnd.github+json",
      "User-Agent": "edgar-moe-study-delivery-check",
      "X-GitHub-Api-Version": "2026-03-10",
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ ref: "main", inputs: {
      check_credentials: true,
      scheduled_at: "credential-check",
      signature: "credential-check",
    } }),
  });
  if (response.status !== 200) throw new Error(`delivery_github_http_${response.status}`);
  const text = await response.text();
  if (Buffer.byteLength(text) > 4096) throw new Error("delivery_github_response_refused");
  const result = JSON.parse(text);
  if (!Number.isSafeInteger(result?.workflow_run_id) || result.workflow_run_id <= 0) {
    throw new Error("delivery_github_response_refused");
  }
  return { stage: "CHECK_GITHUB", workflow_run_id: result.workflow_run_id };
}

export async function manage({ stage, env, source, send = fetch, now = Date.now,
  observe = observeScheduledEvent, sleep = wait }) {
  if (stage === "CHECK_GITHUB") return checkGitHub(env.STUDY_DELIVERY_GITHUB_TOKEN, send);
  const account = env.STUDY_DELIVERY_CLOUDFLARE_ACCOUNT_ID;
  const token = env.STUDY_DELIVERY_CLOUDFLARE_TOKEN;
  if (!["VERIFY", "VERIFY_ACTIVE", "DIAGNOSE", "START", "STOP", "STOP_EXPIRED"].includes(stage) ||
      !/^[a-f0-9]{32}$/.test(account || "") || !/^[A-Za-z0-9_-]{20,512}$/.test(token || "")) {
    throw new Error("delivery_configuration_refused");
  }
  const base = `https://api.cloudflare.com/client/v4/accounts/${account}/workers/scripts/${WORKER}`;

  async function request(path, method = "GET", body, json = false) {
    const response = await send(base + path, {
      method,
      redirect: "error",
      signal: AbortSignal.timeout(15_000),
      headers: { Authorization: `Bearer ${token}`, ...(json ? { "Content-Type": "application/json" } : {}) },
      body,
    });
    if (!response.ok) throw new Error(`delivery_cloudflare_http_${response.status}`);
    const text = await response.text();
    if (Buffer.byteLength(text) > 65536) throw new Error("delivery_response_too_large");
    const result = JSON.parse(text);
    if (result.success !== true) throw new Error("delivery_cloudflare_refused");
    return result.result;
  }

  async function schedule() {
    const result = await request("/schedules");
    if (!Array.isArray(result?.schedules) || result.schedules.length > 1 ||
        result.schedules.some((item) => item?.cron !== CRON)) {
      throw new Error("delivery_unexpected_schedule");
    }
    return result.schedules.length === 1;
  }

  async function privateUrls() {
    const urls = await request("/subdomain");
    if (urls?.enabled !== false || urls.previews_enabled !== false) {
      throw new Error("delivery_worker_urls_refused");
    }
  }

  async function activeResources() {
    const { deployments } = await request("/deployments?per_page=1");
    const [active, ...other] = deployments?.[0]?.versions ?? [];
    if (active?.percentage !== 100 || other.length ||
        !/^[a-f0-9-]{36}$/.test(active.version_id ?? "")) {
      throw new Error("delivery_active_deployment_refused");
    }
    const { resources } = await request(`/versions/${active.version_id}`);
    return resources;
  }

  const stamp = env.STUDY_DELIVERY_STARTS_AT;
  const start = Date.parse(stamp);
  function matchesStart(bindings) {
    return Array.isArray(bindings) && bindings.some((binding) =>
      binding?.name === "STUDY_DELIVERY_STARTS_AT" && binding.type === "plain_text" &&
      binding.text === stamp);
  }
  if (stage === "STOP_EXPIRED" && (!Number.isFinite(start) || start % HOUR !== 0 ||
      new Date(start).toISOString() !== stamp || now() < start + 24 * HOUR + 10 * MINUTE)) {
    throw new Error("delivery_stop_window_refused");
  }
  const wasActive = await schedule();
  if (stage === "STOP" || stage === "STOP_EXPIRED") {
    if (wasActive) {
      if (stage === "STOP_EXPIRED" && !matchesStart((await activeResources())?.bindings)) {
        throw new Error("delivery_stop_active_window_mismatch");
      }
      await request("/schedules", "PUT", "[]", true);
    }
    if (await schedule()) throw new Error("delivery_stop_not_verified");
    return { stage, cron_active: false, worker: WORKER };
  }
  await privateUrls();
  if (stage === "VERIFY") return { stage, cron_active: wasActive, worker: WORKER };
  if (stage === "VERIFY_ACTIVE") {
    if (!wasActive || !Number.isFinite(start) || new Date(start).toISOString() !== stamp) {
      throw new Error("delivery_active_configuration_refused");
    }
    const resources = await activeResources();
    const handlers = resources?.script?.handlers;
    const bindings = resources?.bindings;
    return {
      stage, worker: WORKER, cron_active: true,
      scheduled_handler_present: Array.isArray(handlers) && handlers.includes("scheduled"),
      start_matches: matchesStart(bindings),
      expected_secret_bindings_present: Array.isArray(bindings) && CREDENTIALS.every((name) =>
        bindings.some((binding) => binding?.name === name && binding.type === "secret_text")),
    };
  }
  if (stage === "DIAGNOSE") {
    const hour = 60 * MINUTE;
    if (!wasActive || !Number.isFinite(start) || start % hour !== 0 ||
        new Date(start).toISOString() !== stamp) {
      throw new Error("delivery_diagnostic_inactive");
    }
    const current = now();
    const scheduled = Math.max(start, Math.ceil((current + 15_000 - 17 * MINUTE) / hour) * hour) + 17 * MINUTE;
    const ends = scheduled + 6 * MINUTE;
    if (scheduled >= start + 24 * hour || scheduled - current > hour + 15_000) {
      throw new Error("delivery_diagnostic_outside_window");
    }
    await sleep(Math.max(0, scheduled - current - 90_000));
    const tail = await request("/tails", "POST", JSON.stringify({ filters: [] }), true);
    if (!/^[a-f0-9]{32}$/.test(tail?.id || "")) {
      throw new Error("delivery_tail_identity_refused");
    }
    try {
      if (Date.parse(tail.expires_at) < ends || !Number.isFinite(Date.parse(tail.expires_at))) {
        throw new Error("delivery_tail_expiry_refused");
      }
      const event = await observe({ url: tail.url, scheduled, ends, cron: CRON, worker: WORKER });
      return { stage, worker: WORKER, scheduled_at: new Date(scheduled).toISOString(), ...event };
    } finally {
      await request(`/tails/${tail.id}`, "DELETE");
    }
  }
  if (wasActive) throw new Error("delivery_worker_already_active");
  if (!Number.isFinite(start) || start % (60 * MINUTE) !== 0 ||
      new Date(start).toISOString() !== stamp || start - now() < 20 * MINUTE ||
      start - now() > 48 * 60 * MINUTE ||
      !/^[a-f0-9]{64}$/.test(env.STUDY_DELIVERY_SIGNING_KEY || "") ||
      !/^[A-Za-z0-9_]{20,512}$/.test(env.STUDY_DELIVERY_GITHUB_TOKEN || "") ||
      !source || Buffer.byteLength(source) > 32_768) {
    throw new Error("delivery_start_bindings_refused");
  }
  const settings = await request("/settings");
  if (!Array.isArray(settings?.bindings) || settings.bindings.some((binding) =>
    binding?.name === "STUDY_DELIVERY_STARTS_AT" ? binding.type !== "plain_text" :
      !CREDENTIALS.includes(binding?.name) || binding.type !== "secret_text")) {
    throw new Error("delivery_unexpected_worker_bindings");
  }
  const metadata = {
    main_module: MODULE,
    compatibility_date: "2026-11-25",
    bindings: [
      { name: "STUDY_DELIVERY_STARTS_AT", type: "plain_text", text: stamp },
      ...CREDENTIALS.map((name) => ({ name, type: "secret_text", text: env[name] })),
    ],
    logpush: false,
    observability: { enabled: false },
    tail_consumers: [],
  };
  const body = new FormData();
  body.set("metadata", new Blob([JSON.stringify(metadata)], { type: "application/json" }));
  body.set(MODULE, new Blob([source], { type: "application/javascript+module" }), MODULE);
  await request("", "PUT", body);
  await privateUrls();
  if (await schedule() || start - now() < 20 * MINUTE) {
    throw new Error("delivery_activation_boundary_refused");
  }
  await request("/schedules", "PUT", JSON.stringify([{ cron: CRON }]), true);
  if (!(await schedule())) throw new Error("delivery_activation_not_verified");
  return { stage, cron_active: true, worker: WORKER, starts_at: stamp,
    expires_at: new Date(start + 24 * 60 * MINUTE).toISOString(),
    source_sha256: createHash("sha256").update(source).digest("hex") };
}

// A peer can leave Node's native WebSocket close handshake pending. This CLI
// has already awaited the Cloudflare DELETE before finishing a diagnostic.
export function writeDiagnosticResult(output, line, exitCode, exit = process.exit) {
  output.write(`${line}\n`, () => exit(exitCode));
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const stage = process.env.STUDY_DELIVERY_STAGE;
  try {
    const report = await manage({ stage, env: process.env,
      source: readFileSync(new URL(MODULE, import.meta.url), "utf8") });
    const line = JSON.stringify(report);
    if (stage === "DIAGNOSE") writeDiagnosticResult(process.stdout, line, 0);
    else console.log(line);
  } catch (error) {
    const message = error instanceof Error && /^delivery_[a-z0-9_]+$/.test(error.message)
      ? error.message : "delivery_management_failed";
    if (stage === "DIAGNOSE") writeDiagnosticResult(process.stderr, message, 1);
    else {
      console.error(message);
      process.exitCode = 1;
    }
  }
}
