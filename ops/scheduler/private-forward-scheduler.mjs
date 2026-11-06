/** Finite private #156 slots. No public trigger; signatures bind the Cron timestamp. */
import { runScheduledDispatch, validateSchedulerConfig } from "./cloudflare-forward-scheduler.mjs";

export const DATES = ["2026-11-07", "2026-11-10", "2026-11-11"];
export const CRON = "17 7 * * TUE-SAT";
const WORKFLOW = "forward-private-scheduler.yml";

export async function privateInputs(controller, env, now = Date.now()) {
  const timestamp = controller.scheduledTime;
  if (!Number.isFinite(timestamp)) throw new Error("Missing provider scheduled time");
  const stamp = new Date(timestamp).toISOString();
  if (!DATES.some(day => stamp === `${day}T07:17:00.000Z`)) return null;
  if (controller.cron !== CRON || now < timestamp || now - timestamp > 90 * 60000) {
    throw new Error("Private schedule outside freshness boundary");
  }
  const config = validateSchedulerConfig(env);
  if (config.workflow !== WORKFLOW || config.ref !== "main" || config.device !== "cpu" ||
      config.repository !== "hoangnguyen2003/edgar-moe" || !/^[a-f0-9]{64}$/.test(env.SCHEDULER_SIGNATURE_KEY ?? "")) {
    throw new Error("Private scheduler configuration refused");
  }
  const bytes = new TextEncoder();
  const key = await crypto.subtle.importKey("raw", bytes.encode(env.SCHEDULER_SIGNATURE_KEY),
    {name: "HMAC", hash: "SHA-256"}, false, ["sign"]);
  const message = `issue156-private-v1\n${stamp}\n${CRON}\nmain\ncpu\n${WORKFLOW}`;
  const signature = await crypto.subtle.sign("HMAC", key, bytes.encode(message));
  return {device: "cpu", scheduled_at: stamp,
    signature: Array.from(new Uint8Array(signature), byte => byte.toString(16).padStart(2, "0")).join("")};
}

export async function dispatchPrivate(controller, env, now = Date.now()) {
  const inputs = await privateInputs(controller, env, now);
  if (inputs === null) return {status: "skipped", reason: "outside_private_window"};
  return runScheduledDispatch(env, controller.cron, inputs);
}

export default {
  async scheduled(controller, env, context) { context.waitUntil(dispatchPrivate(controller, env)); },
  async fetch() { return new Response("Not found", {status: 404}); },
};
