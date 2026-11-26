import { dispatchStudySlot, nominalStudySlot } from "./study-delivery-worker.mjs";

const WORKFLOW = "https://api.github.com/repos/hoangnguyen2003/edgar-moe/actions/workflows/observe-private-read-study.yml/dispatches";
const DOMAIN = "edgar-moe/private-study-observation/v1";
const HOUR = 3_600_000;
const HASH = /^[a-f0-9]{64}$/;

export async function deliverObservation(controller, env, now = Date.now(), send = fetch) {
  const startText = env.STUDY_OBSERVATION_STARTS_AT;
  const endText = env.STUDY_OBSERVATION_EXPIRES_AT;
  const start = Date.parse(startText);
  const end = Date.parse(endText);
  if (!Number.isFinite(start) || start % HOUR !== 0 || end - start !== 168 * HOUR ||
      startText !== new Date(start).toISOString() || endText !== new Date(end).toISOString()) {
    throw new Error("Study observation window refused");
  }
  const scheduled = nominalStudySlot(controller, start, end, now);
  if (scheduled === null) return "skipped";
  const policy = env.STUDY_OBSERVATION_POLICY_SHA256;
  const target = env.STUDY_OBSERVATION_HOST_SHA256;
  if (!HASH.test(policy ?? "") || !HASH.test(target ?? "")) {
    throw new Error("Study observation commitments refused");
  }
  const stamp = new Date(scheduled).toISOString();
  return dispatchStudySlot({
    workflow: WORKFLOW,
    token: env.STUDY_OBSERVATION_GITHUB_TOKEN,
    secret: env.STUDY_OBSERVATION_SIGNING_KEY,
    stamp,
    message: [DOMAIN, startText, endText, policy, target, stamp].join("\n"),
    send,
  });
}

export default {
  async scheduled(controller, env) {
    controller.noRetry();
    await deliverObservation(controller, env);
  },
  async fetch() {
    return new Response("Not found", {status: 404});
  },
};
