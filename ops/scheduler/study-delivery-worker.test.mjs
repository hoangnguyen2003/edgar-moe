import assert from "node:assert/strict";
import { createHmac } from "node:crypto";
import test from "node:test";
import worker, { deliver } from "./study-delivery-worker.mjs";

const start = "2026-11-25T05:00:00.000Z";
const stamp = "2026-11-25T05:17:00.000Z";
const env = {
  STUDY_DELIVERY_STARTS_AT: start,
  STUDY_DELIVERY_SIGNING_KEY: "a".repeat(64),
  STUDY_DELIVERY_GITHUB_TOKEN: "synthetic_token_for_test_only",
};
const event = { cron: "17 * * * *", scheduledTime: Date.parse(stamp) };

test("signs and sends a single fixed dispatch", async () => {
  const calls = [];
  const status = await deliver(event, env, event.scheduledTime + 1000, async (...args) => {
    calls.push(args);
    return { status: 200 };
  });
  assert.equal(status, "dispatched");
  assert.equal(calls.length, 1);
  const [url, options] = calls[0];
  assert.equal(url, "https://api.github.com/repos/hoangnguyen2003/edgar-moe/actions/workflows/study-external-delivery.yml/dispatches");
  assert.equal(options.method, "POST");
  assert.equal(options.redirect, "manual");
  assert.equal(options.headers["User-Agent"], "edgar-moe-study-delivery");
  const signature = createHmac("sha256", env.STUDY_DELIVERY_SIGNING_KEY)
    .update(`edgar-moe/study-external-delivery/v1\n${start}\n${stamp}`).digest("hex");
  assert.deepEqual(JSON.parse(options.body), { ref: "main", inputs: { scheduled_at: stamp, signature } });
});

test("refuses stale, future, foreign and expired triggers without a network call", async () => {
  const fail = () => assert.fail("unexpected request");
  for (const [candidate, now] of [
    [event, event.scheduledTime - 1],
    [event, event.scheduledTime + 300_000],
    [{ ...event, cron: "* * * * *" }, event.scheduledTime],
    [{ ...event, scheduledTime: event.scheduledTime + 86_400_000 }, event.scheduledTime + 86_400_000],
  ]) {
    assert.equal(await deliver(candidate, env, now, fail), "skipped");
  }
  assert.equal((await worker.fetch()).status, 404);
});

test("a redirected or rejected dispatch is never followed or retried", async () => {
  for (const status of [302, 403]) {
    let attempts = 0;
    await assert.rejects(deliver(event, env, event.scheduledTime, async () => {
      attempts += 1;
      return { status };
    }), new RegExp(`GitHub declined external delivery \\(HTTP ${status}\\)`));
    assert.equal(attempts, 1);
  }
});
