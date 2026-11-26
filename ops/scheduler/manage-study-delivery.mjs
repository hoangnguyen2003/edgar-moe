import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { setTimeout as wait } from "node:timers/promises";
import { pathToFileURL } from "node:url";
import { observeScheduledEvent } from "./study-delivery-tail.mjs";

const WORKER = "edgar-moe-study-delivery";
const MODULE = "study-delivery-worker.mjs";
const CRON = "17 * * * *";
const MINUTE = 60_000;
const CREDENTIALS = ["STUDY_DELIVERY_GITHUB_TOKEN", "STUDY_DELIVERY_SIGNING_KEY"];

export async function manage({ stage, env, source, send = fetch, now = Date.now,
  observe = observeScheduledEvent, sleep = wait }) {
  const account = env.STUDY_DELIVERY_CLOUDFLARE_ACCOUNT_ID;
  const token = env.STUDY_DELIVERY_CLOUDFLARE_TOKEN;
  if (!["VERIFY", "DIAGNOSE", "START", "STOP"].includes(stage) ||
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

  const wasActive = await schedule();
  if (stage === "STOP") {
    if (wasActive) await request("/schedules", "PUT", "[]", true);
    if (await schedule()) throw new Error("delivery_stop_not_verified");
    return { stage, cron_active: false, worker: WORKER };
  }
  await privateUrls();
  if (stage === "VERIFY") return { stage, cron_active: wasActive, worker: WORKER };
  const stamp = env.STUDY_DELIVERY_STARTS_AT;
  const start = Date.parse(stamp);
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

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  try {
    const report = await manage({ stage: process.env.STUDY_DELIVERY_STAGE, env: process.env,
      source: readFileSync(new URL(MODULE, import.meta.url), "utf8") });
    console.log(JSON.stringify(report));
  } catch (error) {
    const message = error instanceof Error && /^delivery_[a-z0-9_]+$/.test(error.message)
      ? error.message : "delivery_management_failed";
    console.error(message);
    process.exitCode = 1;
  }
}
