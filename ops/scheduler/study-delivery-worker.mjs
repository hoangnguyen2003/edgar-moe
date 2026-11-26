const CRON = "17 * * * *";
const WORKFLOW = "https://api.github.com/repos/hoangnguyen2003/edgar-moe/actions/workflows/study-external-delivery.yml/dispatches";
const HOUR = 3_600_000;
const SLOT_OFFSET = 17 * 60_000;
const MAX_DELAY = 5 * 60_000;
const DOMAIN = "edgar-moe/study-external-delivery/v1";

export function nominalStudySlot(controller, start, end, now) {
  const reported = controller.scheduledTime;
  if (controller.cron !== CRON || !Number.isFinite(reported) ||
      !Number.isSafeInteger(now) || reported < start || now < Math.floor(reported)) {
    return null;
  }
  // Cloudflare may deliver a timestamp with seconds inside the scheduled hour.
  // Sign the canonical slot so scheduler delay never changes its identity.
  const scheduled = Math.floor((reported - SLOT_OFFSET) / HOUR) * HOUR + SLOT_OFFSET;
  return scheduled >= start && scheduled < end && now - scheduled < MAX_DELAY ? scheduled : null;
}

export async function dispatchStudySlot({ workflow, token, secret, stamp, message, send }) {
  if (!/^[a-f0-9]{64}$/.test(secret || "") || !/^[A-Za-z0-9_]{20,512}$/.test(token || "")) {
    throw new Error("External delivery credentials refused");
  }
  const encoder = new TextEncoder();
  const key = await crypto.subtle.importKey(
    "raw", encoder.encode(secret), {name: "HMAC", hash: "SHA-256"}, false, ["sign"],
  );
  const signed = await crypto.subtle.sign("HMAC", key, encoder.encode(message));
  const signature = Array.from(new Uint8Array(signed), (b) => b.toString(16).padStart(2, "0")).join("");
  const response = await send(workflow, {
    method: "POST",
    redirect: "manual",
    signal: AbortSignal.timeout(10_000),
    headers: {
      Authorization: `Bearer ${token}`,
      Accept: "application/vnd.github+json",
      "User-Agent": "edgar-moe-study-delivery",
      "X-GitHub-Api-Version": "2026-03-10",
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ref: "main", inputs: {scheduled_at: stamp, signature}}),
  });
  if (response.status !== 200 && response.status !== 204) {
    throw new Error(`GitHub declined external delivery (HTTP ${response.status})`);
  }
  return "dispatched";
}

export async function deliver(controller, env, now = Date.now(), send = fetch) {
  const startText = env.STUDY_DELIVERY_STARTS_AT;
  const start = Date.parse(startText);
  if (!Number.isFinite(start) || start % HOUR !== 0 || new Date(start).toISOString() !== startText) {
    throw new Error("External delivery window refused");
  }
  const scheduled = nominalStudySlot(controller, start, start + 24 * HOUR, now);
  if (scheduled === null) return "skipped";
  const stamp = new Date(scheduled).toISOString();
  return dispatchStudySlot({
    workflow: WORKFLOW,
    token: env.STUDY_DELIVERY_GITHUB_TOKEN,
    secret: env.STUDY_DELIVERY_SIGNING_KEY,
    stamp,
    message: `${DOMAIN}\n${startText}\n${stamp}`,
    send,
  });
}

export default {
  async scheduled(controller, env) {
    controller.noRetry();
    const status = await deliver(controller, env);
    console.log(`study_delivery_${status}`);
  },
  async fetch() {
    return new Response("Not found", {status: 404});
  },
};
