import assert from "node:assert/strict";
import { createHmac } from "node:crypto";
import test from "node:test";
import worker, { deliverObservation } from "./study-observation-worker.mjs";

const start = "2026-12-01T00:00:00.000Z";
const end = "2026-12-08T00:00:00.000Z";
const stamp = "2026-12-01T00:17:00.000Z";
const env = {
  STUDY_OBSERVATION_STARTS_AT: start,
  STUDY_OBSERVATION_EXPIRES_AT: end,
  STUDY_OBSERVATION_POLICY_SHA256: "a".repeat(64),
  STUDY_OBSERVATION_HOST_SHA256: "b".repeat(64),
  STUDY_OBSERVATION_SIGNING_KEY: "c".repeat(64),
  STUDY_OBSERVATION_GITHUB_TOKEN: "synthetic_token_for_test_only",
};
const time = Date.parse(stamp);
const event = { cron: "17 * * * *", scheduledTime: time + 22_000 };

test("signs the measurement commitments and sends only the nominated slot", async () => {
  const calls = [];
  const result = await deliverObservation(event, env, time + 22_500, async (url, options) => {
    calls.push({url, options});
    return {status: 200};
  });
  assert.equal(result, "dispatched");
  assert.equal(calls.length, 1);
  const [{url, options}] = calls;
  assert.equal(url, "https://api.github.com/repos/hoangnguyen2003/edgar-moe/actions/workflows/observe-private-read-study.yml/dispatches");
  assert.equal(options.redirect, "manual");
  const expected = createHmac("sha256", env.STUDY_OBSERVATION_SIGNING_KEY)
    .update(["edgar-moe/private-study-observation/v1", start, end, "a".repeat(64), "b".repeat(64), stamp].join("\n"))
    .digest("hex");
  assert.deepEqual(JSON.parse(options.body), {ref: "main", inputs: {scheduled_at: stamp, signature: expected}});
});

test("all 168 hours are eligible and the first expired hour is silent", async () => {
  let sends = 0;
  for (let hour = 0; hour <= 168; hour += 1) {
    const at = time + hour * 3_600_000;
    const status = await deliverObservation({ ...event, scheduledTime: at }, env, at, async () => {
      sends += 1;
      return {status: 204};
    });
    assert.equal(status, hour === 168 ? "skipped" : "dispatched");
  }
  assert.equal(sends, 168);
  assert.equal((await worker.fetch()).status, 404);
});

test("refuses a changed window or target without dispatching", async () => {
  const forbid = () => assert.fail("unexpected dispatch");
  await assert.rejects(deliverObservation(event, {
    ...env, STUDY_OBSERVATION_EXPIRES_AT: "2026-12-08T01:00:00.000Z",
  }, time + 22_500, forbid), /window refused/);
  await assert.rejects(deliverObservation(event, {
    ...env, STUDY_OBSERVATION_HOST_SHA256: "foreign",
  }, time + 22_500, forbid), /commitments refused/);
  assert.equal(await deliverObservation(event, env, time + 300_000, forbid), "skipped");
});
