const CRON = "17 * * * *";
const WORKFLOW = "https://api.github.com/repos/hoangnguyen2003/edgar-moe/actions/workflows/study-external-delivery.yml/dispatches";
const HOUR = 3_600_000;
const DOMAIN = "edgar-moe/study-external-delivery/v1";

export async function deliver(controller, env, now = Date.now(), send = fetch) {
  const start = Date.parse(env.STUDY_DELIVERY_STARTS_AT);
  const scheduled = controller.scheduledTime;
  if (!Number.isFinite(start) || start % HOUR !== 0 ||
      new Date(start).toISOString() !== env.STUDY_DELIVERY_STARTS_AT) {
    throw new Error("External delivery window refused");
  }
  if (controller.cron !== CRON || !Number.isSafeInteger(scheduled) ||
      scheduled < start || scheduled >= start + 24 * HOUR ||
      scheduled % HOUR !== 17 * 60_000 || now < scheduled || now - scheduled >= 5 * 60_000) {
    return "skipped";
  }
  if (!/^[a-f0-9]{64}$/.test(env.STUDY_DELIVERY_SIGNING_KEY || "") ||
      !/^[A-Za-z0-9_]{20,512}$/.test(env.STUDY_DELIVERY_GITHUB_TOKEN || "")) {
    throw new Error("External delivery credentials refused");
  }
  const stamp = new Date(scheduled).toISOString();
  const encoder = new TextEncoder();
  const key = await crypto.subtle.importKey(
    "raw", encoder.encode(env.STUDY_DELIVERY_SIGNING_KEY), {name: "HMAC", hash: "SHA-256"}, false, ["sign"],
  );
  const signed = await crypto.subtle.sign("HMAC", key, encoder.encode(`${DOMAIN}\n${env.STUDY_DELIVERY_STARTS_AT}\n${stamp}`));
  const signature = Array.from(new Uint8Array(signed), (b) => b.toString(16).padStart(2, "0")).join("");
  const response = await send(WORKFLOW, {
    method: "POST",
    redirect: "error",
    signal: AbortSignal.timeout(10_000),
    headers: {
      Authorization: `Bearer ${env.STUDY_DELIVERY_GITHUB_TOKEN}`,
      Accept: "application/vnd.github+json",
      "X-GitHub-Api-Version": "2026-03-10",
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ref: "main", inputs: {scheduled_at: stamp, signature}}),
  });
  if (response.status !== 200 && response.status !== 204) {
    throw new Error("GitHub declined external delivery");
  }
  return "dispatched";
}

export default {
  async scheduled(controller, env) {
    controller.noRetry();
    await deliver(controller, env);
  },
  async fetch() {
    return new Response("Not found", {status: 404});
  },
};
