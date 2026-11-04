import assert from "node:assert/strict";
import {createHmac} from "node:crypto";
import test from "node:test";
import scheduler, {CRON, privateInputs, dispatchPrivate} from "./private-forward-scheduler.mjs";

const key = "12".repeat(32);
const stamp = "2026-11-05T07:17:00.000Z";
const controller = {cron: CRON, scheduledTime: Date.parse(stamp)};
const env = {GITHUB_TOKEN: "test-never-log-this-token", GITHUB_REPOSITORY: "hoangnguyen2003/edgar-moe",
  GITHUB_WORKFLOW: "forward-private-scheduler.yml", GITHUB_REF: "main", GITHUB_DEVICE: "cpu",
  SCHEDULER_SIGNATURE_KEY: key};

test("real controller time is bound to the exact interoperable HMAC message", async () => {
  const inputs = await privateInputs(controller, env, controller.scheduledTime + 1000);
  const message = `issue156-private-v1\n${stamp}\n${CRON}\nmain\ncpu\nforward-private-scheduler.yml`;
  assert.deepEqual(inputs, {device: "cpu", scheduled_at: stamp,
    signature: createHmac("sha256", key).update(message).digest("hex")});
  assert.equal(JSON.stringify(inputs).includes(env.GITHUB_TOKEN), false);
});

test("outside the three dates returns before credentials or GitHub requests", async () => {
  assert.deepEqual(await dispatchPrivate({cron: CRON, scheduledTime: Date.parse("2026-11-10T07:17:00.000Z")}, {}),
    {status: "skipped", reason: "outside_private_window"});
});

test("wrong cron, future, stale, changed workflow and missing key fail closed", async () => {
  for (const [event, config, now] of [
    [{...controller, cron: "* * * * *"}, env, controller.scheduledTime],
    [controller, env, controller.scheduledTime - 1],
    [controller, env, controller.scheduledTime + 91 * 60000],
    [controller, {...env, GITHUB_WORKFLOW: "forward-production.yml"}, controller.scheduledTime],
    [controller, {...env, SCHEDULER_SIGNATURE_KEY: ""}, controller.scheduledTime],
  ]) await assert.rejects(privateInputs(event, config, now));
});

test("public HTTP requests cannot trigger the signed scheduler", async () => {
  assert.equal((await scheduler.fetch()).status, 404);
});

test("signed scheduled inputs cross only the existing GitHub dispatch boundary", async () => {
  const original = globalThis.fetch;
  const calls = [];
  globalThis.fetch = async (url, options) => {
    calls.push({url: String(url), options});
    return options.method === "GET" ? new Response(JSON.stringify({workflow_runs: []})) : new Response(null, {status: 204});
  };
  try {
    assert.equal((await dispatchPrivate(controller, env, controller.scheduledTime)).status, "dispatched");
    assert.equal(calls.length, 2);
    const payload = JSON.parse(calls[1].options.body);
    assert.equal(payload.inputs.scheduled_at, stamp);
    assert.match(payload.inputs.signature, /^[a-f0-9]{64}$/);
    assert.equal(JSON.stringify(payload).includes(key), false);
    assert.equal(calls.every(call => call.url.startsWith("https://api.github.com/")), true);
  } finally { globalThis.fetch = original; }
});
