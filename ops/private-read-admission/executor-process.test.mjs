import assert from "node:assert/strict";
import test from "node:test";
import { fork } from "node:child_process";
import { createHash } from "node:crypto";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { openFixture } from "./sqlite-fixture.mjs";
import { LIMITS } from "./ledger.mjs";

const startsAtMs = Date.parse("2026-10-08T00:00:00Z");
const policy = Object.freeze({ pilotId: "a".repeat(64), targetSha256: "b".repeat(64),
  startsAtMs, expiresAtMs: startsAtMs + LIMITS.lifetimeMs });
const request = number => ({ route: "status",
  requestId: createHash("sha256").update(`offline-process-${number}`).digest("hex") });

function waitFor(worker, type) {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => finish(new Error("Local fixture message deadline")), 5000);
    function finish(error, message) {
      clearTimeout(timer);
      worker.off("message", onMessage); worker.off("exit", onExit); worker.off("error", onError);
      if (error) reject(error); else resolve(message);
    }
    function onMessage(message) { if (message.type === type) finish(null, message); }
    function onExit() { finish(new Error("Local fixture exited before receipt")); }
    function onError() { finish(new Error("Local fixture process unavailable")); }
    worker.on("message", onMessage); worker.on("exit", onExit); worker.on("error", onError);
  });
}

function send(worker, type, fields = {}, expected = type) {
  const response = waitFor(worker, expected);
  worker.send({ type, ...fields });
  return response;
}

function prepare(t) {
  const filename = join(mkdtempSync(join(tmpdir(), "edgar-offline-executor-")), "synthetic.sqlite");
  const initial = openFixture(filename, { policy }); initial.close();
  const workers = [];
  t.after(async () => {
    await Promise.all(workers.filter(worker => worker.exitCode === null && worker.signalCode === null)
      .map(worker => new Promise(resolve => {
        worker.once("exit", resolve); worker.kill();
      })));
  });
  return { filename, async spawn() {
    const worker = fork(new URL("./executor-process-fixture.mjs", import.meta.url), [],
      { stdio: ["ignore", "ignore", "ignore", "ipc"] });
    workers.push(worker);
    await send(worker, "open", { filename, policy, nowMs: startsAtMs }, "opened");
    return worker;
  }, summary() {
    const fixture = openFixture(filename, { create: false, policy });
    try { return fixture.ledger.summary(); } finally { fixture.close(); }
  } };
}

test("independent executors share one read slot; result alone never releases a connection", {
  timeout: 15000,
}, async t => {
  const local = prepare(t);
  const [first, second] = await Promise.all([local.spawn(), local.spawn()]);
  const completion = waitFor(first, "outcome");
  await send(first, "run", { input: request(1) }, "started");
  assert.equal(local.summary().active, true);
  assert.equal((await send(second, "run", { input: request(2) }, "outcome")).outcome.reason,
    "completion_unconfirmed");
  await send(first, "result", {}, "result_settled");
  assert.equal((await send(second, "run", { input: request(3) }, "outcome")).outcome.reason,
    "completion_unconfirmed");
  first.send({ type: "closed" });
  assert.equal((await completion).outcome.status, "completed");
  const next = waitFor(second, "outcome");
  await send(second, "run", { input: request(4) }, "started");
  await send(second, "result", {}, "result_settled"); second.send({ type: "closed" });
  assert.equal((await next).outcome.status, "completed");
  assert.deepEqual(local.summary(), { status: "observed", admitted: 2,
    active: false, stopped: false, totalRemaining: 358 });
});

test("eight independent executor processes contend: only one synthetic read starts", {
  timeout: 15000,
}, async t => {
  const local = prepare(t);
  const workers = await Promise.all(Array.from({ length: 8 }, () => local.spawn()));
  const starts = workers.map(() => 0);
  workers.forEach((worker, index) => worker.on("message", message => {
    if (message.type === "started") starts[index]++;
  }));
  const results = workers.map(worker => waitFor(worker, "outcome"));
  const firstRead = Promise.race(workers.map((worker, index) =>
    waitFor(worker, "started").then(() => index)));
  workers.forEach((worker, index) => worker.send({ type: "run", input: request(index + 1) }));
  const winner = await firstRead;
  const refused = await Promise.all(results.filter((_, index) => index !== winner));
  assert(refused.every(value => value.outcome.reason === "completion_unconfirmed"));
  assert.equal(starts.reduce((sum, count) => sum + count, 0), 1);
  assert.equal(local.summary().admitted, 1);
  await send(workers[winner], "result", {}, "result_settled");
  workers[winner].send({ type: "closed" });
  assert.equal((await results[winner]).outcome.status, "completed");
  assert.equal(local.summary().active, false);
});

test("an executor crash preserves its active slot and consumed allowance after process restart", {
  timeout: 15000,
}, async t => {
  const local = prepare(t);
  const first = await local.spawn();
  await send(first, "run", { input: request(1) }, "started");
  await new Promise(resolve => { first.once("exit", resolve); first.kill("SIGKILL"); });
  const restarted = await local.spawn();
  assert.equal((await send(restarted, "run", { input: request(2) }, "outcome")).outcome.reason,
    "completion_unconfirmed");
  assert.deepEqual(local.summary(), { status: "observed", admitted: 1,
    active: true, stopped: false, totalRemaining: 359 });
});
