import assert from "node:assert/strict";
import test from "node:test";
import { createHash } from "node:crypto";
import { fork } from "node:child_process";
import { chmodSync, mkdtempSync, readFileSync, statSync, symlinkSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { AdmissionLedger, LIMITS } from "./ledger.mjs";
import { SqliteAdmissionStore } from "./sqlite-store.mjs";
import { openFixture } from "./sqlite-fixture.mjs";
import { OfflineAdmissionExecutor, createOfflineDurableAdmissionClass } from "./executor.mjs";

const START = Date.parse("2026-10-06T00:00:00Z");
const policy = Object.freeze({ pilotId: "a".repeat(64), targetSha256: "b".repeat(64),
  startsAtMs: START, expiresAtMs: START + LIMITS.lifetimeMs });
const id = number => createHash("sha256").update(`synthetic-request-${number}`).digest("hex");
const request = number => ({ requestId: id(number), route: "status" });
const secret = "fixture-only-credential-or-driver-message-never-retained";
function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
const resolvedRead = () => ({ result: Promise.resolve(true), closed: Promise.resolve(true) });
function executor(fixture, overrides = {}) {
  return new OfflineAdmissionExecutor({ ledger: fixture.ledger,
    authorize: async value => value === secret, startRead: resolvedRead,
    clock: () => START, ...overrides });
}
function assertNoSecrets(value) {
  const text = JSON.stringify(value);
  for (const forbidden of [secret, "Bearer", "postgres://", policy.pilotId, policy.targetSha256]) {
    assert(!text.includes(forbidden));
  }
}
function consume(ledger, number, nowMs) {
  const admitted = ledger.admit(id(number), nowMs);
  assert.equal(admitted.status, "admitted");
  assert.equal(ledger.confirmClosed(id(number), admitted.sequence).status, "closed");
}

test("fixed policy validation rejects lifetime extensions, extra fields and credential input", () => {
  const store = { transaction() { throw new Error("must not touch storage"); } };
  for (const changed of [{ expiresAtMs: policy.expiresAtMs + 1 }, { startsAtMs: NaN },
    { pilotId: secret }, { targetSha256: "postgres://fixture" }, { total: 1000 }]) {
    assert.throws(() => new AdmissionLedger(store, { ...policy, ...changed }), /Admission refused/);
  }
});

test("SQLite admission persists before read; raw reader output and auth never reach receipts", async t => {
  const fixture = openFixture(undefined, { policy }); t.after(fixture.close);
  const run = executor(fixture, { startRead: () => {
    assert.deepEqual(fixture.ledger.summary(), { status: "observed", admitted: 1,
      active: true, stopped: false, totalRemaining: 359 });
    return resolvedRead();
  } });
  const result = await run.run(request(1), secret);
  assert.equal(result.status, "completed");
  assert.equal(result.totalRemaining, 359);
  assert(result.controlElapsedMs >= 0 && result.readElapsedMs >= 0);
  assertNoSecrets(result);
  const raw = fixture.database.prepare("SELECT state FROM admission_state").get().state;
  assert(!raw.includes(secret));
  assert.equal(fixture.ledger.summary().active, false);
});

test("authentication and invalid routes refuse without touching counters or starting reads", async () => {
  let touched = 0;
  const ledger = { admit() { touched++; throw new Error(secret); } };
  const run = new OfflineAdmissionExecutor({ ledger, authorize: async value => value === secret,
    startRead: () => { touched++; throw new Error(secret); } });
  for (const auth of [undefined, false, "wrong"]) {
    assert.equal((await run.run(request(1), auth)).reason, "unauthorized");
  }
  for (const input of [null, { ...request(1), route: "reset" }, { ...request(1), override: true },
    { ...request(1), requestId: secret }, { ...request(1), route: "https://fixture.invalid" }]) {
    assert.equal((await run.run(input, secret)).reason, "request_invalid");
  }
  const broken = new OfflineAdmissionExecutor({ ledger, authorize: () => { throw new Error(secret); } });
  assert.equal((await broken.run(request(1), secret)).reason, "unauthorized");
  const evil = { get requestId() { throw new Error(secret); }, route: "status" };
  const safe = await run.run(evil, secret);
  assert.equal(safe.reason, "execution_unavailable");
  assertNoSecrets(safe);
  assert.equal(touched, 0);
});

test("one shared active slot blocks concurrent executor instances until BOTH result and close settle", async t => {
  const fixture = openFixture(undefined, { policy }); t.after(fixture.close);
  const result = deferred(); const closed = deferred();
  let starts = 0;
  const first = executor(fixture, { startRead: () => { starts++; return {
    result: result.promise, closed: closed.promise }; } });
  const second = executor({ ledger: new AdmissionLedger(fixture.store, policy) }, {
    startRead: () => { starts++; return resolvedRead(); } });
  const active = first.run(request(1), secret);
  await Promise.resolve();
  assert.equal((await second.run(request(2), secret)).reason, "completion_unconfirmed");
  result.resolve(true);
  await Promise.resolve();
  assert.equal((await second.run(request(3), secret)).reason, "completion_unconfirmed");
  closed.resolve(true);
  assert.equal((await active).status, "completed");
  assert.equal((await second.run(request(4), secret)).status, "completed");
  assert.equal(starts, 2);
  assert.equal(fixture.ledger.summary().admitted, 2);
});

test("read failures consume budget and stop; confirmed closure cannot silently restart the pilot", async t => {
  const fixture = openFixture(undefined, { policy }); t.after(fixture.close);
  const failed = executor(fixture, { startRead: () => ({ result: Promise.reject(new Error(secret)),
    closed: Promise.resolve(true) }) });
  const outcome = await failed.run(request(1), secret);
  assert.equal(outcome.reason, "read_failed"); assertNoSecrets(outcome);
  assert.equal(fixture.ledger.summary().admitted, 1);
  assert.equal(fixture.ledger.summary().active, false);
  assert.equal(fixture.ledger.summary().stopped, true);
  assert.equal((await executor(fixture).run(request(2), secret)).reason, "pilot_stopped");
});

test("fulfilled driver error payloads are not mistaken for successful reads", async t => {
  for (const value of [false, undefined, secret, { error: secret }]) {
    const fixture = openFixture(undefined, { policy }); t.after(fixture.close);
    const outcome = await executor(fixture, { startRead: () => ({ result: Promise.resolve(value),
      closed: Promise.resolve(true) }) }).run(request(1), secret);
    assert.equal(outcome.reason, "read_failed"); assertNoSecrets(outcome);
    assert.equal(fixture.ledger.summary().stopped, true);
  }
});

test("ambiguous start or closure stops persistently; no reset or slot-timeout escape", async t => {
  for (const startRead of [() => { throw new Error(secret); }, () => ({}),
    () => Promise.reject(new Error(secret)),
    () => ({ result: Promise.reject(new Error(secret)) }),
    () => ({ result: Promise.resolve(secret), closed: Promise.resolve(false) }),
    () => ({ result: Promise.resolve(secret), closed: Promise.reject(new Error(secret)) })]) {
    const fixture = openFixture(undefined, { policy }); t.after(fixture.close);
    const result = await executor(fixture, { startRead }).run(request(1), secret);
    assert.equal(result.status, "refused"); assertNoSecrets(result);
    assert.equal(fixture.ledger.summary().stopped, true);
    const restarted = executor({ ledger: new AdmissionLedger(fixture.store, policy) }, {
      clock: () => START + 60000 });
    assert.equal((await restarted.run(request(2), secret)).reason, "pilot_stopped");
    assert.equal(fixture.ledger.initializeOfflineFixture().reason, "already_initialized");
  }
});

test("completion storage ambiguity holds admission; caller mutation cannot redirect the read", async t => {
  const fixture = openFixture(undefined, { policy }); t.after(fixture.close);
  const original = request(1);
  let calls = 0;
  const ledger = { admit(...args) {
    original.requestId = id(99); original.route = "reset";
    return fixture.ledger.admit(...args);
  }, confirmClosed() { throw new Error(secret); } };
  const result = await executor({ ledger }, { startRead(input) {
    calls++; assert.deepEqual(input, request(1)); assert(Object.isFrozen(input));
    return resolvedRead();
  } }).run(original, secret);
  assert.equal(result.reason, "execution_unavailable"); assertNoSecrets(result);
  assert.equal(calls, 1);
  assert.equal(fixture.ledger.summary().active, true);
  assert.equal((await executor(fixture).run(request(2), secret)).reason, "completion_unconfirmed");
});

test("10-second timeout does not cancel by assumption or release after late closure", async t => {
  const fixture = openFixture(undefined, { policy }); t.after(fixture.close);
  const result = deferred(); const closed = deferred(); let fire; let cleared;
  const run = executor(fixture, { startRead: () => ({ result: result.promise, closed: closed.promise }),
    timer: { set(callback, duration) { assert.equal(duration, 10000); fire = callback; return 7; },
      clear(value) { cleared = value; } } });
  const active = run.run(request(1), secret); await Promise.resolve(); fire();
  assert.equal((await active).reason, "read_timeout"); assert.equal(cleared, 7);
  result.resolve(secret); closed.resolve(true); await Promise.resolve();
  assert.equal(fixture.ledger.summary().active, true);
  assert.equal(fixture.ledger.summary().stopped, true);
  assert.equal((await executor(fixture).run(request(2), secret)).reason, "pilot_stopped");
});

test("absolute start and seven-day expiry are exact and are not recreated on a new executor", async t => {
  const fixture = openFixture(undefined, { policy }); t.after(fixture.close);
  assert.equal(fixture.ledger.admit(id(1), START - 1).reason, "pilot_not_started");
  assert.equal(fixture.ledger.summary().stopped, false);
  consume(fixture.ledger, 1, policy.expiresAtMs - 1);
  const restarted = new AdmissionLedger(fixture.store, policy);
  assert.equal(restarted.admit(id(2), policy.expiresAtMs).reason, "pilot_expired");
  assert.equal(restarted.admit(id(3), policy.expiresAtMs + 1).reason, "pilot_expired");
  assert.equal(restarted.summary().admitted, 1);
});

test("daily UTC budget is 80, lifetime is 360, duplicates and refusals never refund admissions", t => {
  const fixture = openFixture(undefined, { policy }); t.after(fixture.close);
  for (let number = 1; number <= 80; number++) consume(fixture.ledger, number, START);
  assert.equal(fixture.ledger.admit(id(81), START).reason, "daily_budget_exhausted");
  assert.equal(fixture.ledger.admit(id(1), START).reason, "request_replayed");
  for (let number = 81; number <= 360; number++) {
    consume(fixture.ledger, number, START + Math.floor((number - 1) / 80) * 86400000);
  }
  assert.equal(fixture.ledger.admit(id(361), START + 5 * 86400000).reason, "total_budget_exhausted");
  assert.equal(fixture.ledger.summary().totalRemaining, 0);
});

test("clock rollback persistently stops instead of resetting a UTC-day counter", t => {
  const fixture = openFixture(undefined, { policy }); t.after(fixture.close);
  consume(fixture.ledger, 1, START + 86400000);
  assert.equal(fixture.ledger.admit(id(2), START + 1).reason, "clock_invalid");
  assert.equal(fixture.ledger.admit(id(3), START + 2 * 86400000).reason, "pilot_stopped");
});

test("observed expiry is permanent across restart and clock rollback, even with zero admissions", t => {
  for (const populated of [false, true]) {
    const fixture = openFixture(undefined, { policy }); t.after(fixture.close);
    if (populated) consume(fixture.ledger, 1, START);
    assert.equal(fixture.ledger.admit(id(2), policy.expiresAtMs).reason, "pilot_expired");
    const restarted = new AdmissionLedger(fixture.store, policy);
    for (const time of [START - 1, START, policy.expiresAtMs - 1, policy.expiresAtMs + 1]) {
      assert.equal(restarted.admit(id(3), time).reason, "pilot_expired");
    }
    assert.equal(restarted.summary().admitted, populated ? 1 : 0);
    assert.equal(restarted.summary().stopped, true);
    assert.equal(restarted.initializeOfflineFixture().reason, "already_initialized");
  }
});

test("denied requests persist clock observations without consuming allowance", t => {
  for (const reason of ["request_replayed", "completion_unconfirmed", "daily_budget_exhausted", "total_budget_exhausted"]) {
    const fixture = openFixture(undefined, { policy }); t.after(fixture.close);
    if (reason === "total_budget_exhausted") {
      for (let number = 1; number <= 360; number++) {
        consume(fixture.ledger, number, START + Math.floor((number - 1) / 80) * 86400000);
      }
    } else if (reason === "daily_budget_exhausted") {
      for (let number = 1; number <= 80; number++) consume(fixture.ledger, number, START);
    } else if (reason === "request_replayed") consume(fixture.ledger, 1, START);
    else fixture.ledger.admit(id(1), START);
    const before = fixture.ledger.summary().admitted;
    const observed = START + (reason === "total_budget_exhausted" ? 5 * 86400000 : 1000);
    assert.equal(fixture.ledger.admit(id(reason === "request_replayed" ? 1 : 999), observed).reason, reason);
    assert.equal(fixture.ledger.summary().admitted, before);
    const restarted = new AdmissionLedger(fixture.store, policy);
    assert.equal(restarted.admit(id(1000), observed - 1).reason, "clock_invalid");
    assert.equal(restarted.admit(id(1001), observed + 1).reason, "pilot_stopped");
  }
});

test("pre-start time is harmless only before any admitted read; later rollback stops permanently", t => {
  const fixture = openFixture(undefined, { policy }); t.after(fixture.close);
  assert.equal(fixture.ledger.admit(id(1), START - 1).reason, "pilot_not_started");
  consume(fixture.ledger, 1, START);
  assert.equal(fixture.ledger.admit(id(2), START - 1).reason, "clock_invalid");
  assert.equal(new AdmissionLedger(fixture.store, policy).admit(id(3), START).reason, "pilot_stopped");
});

test("duplicate completion and mismatched identity cannot clear the active slot", t => {
  const fixture = openFixture(undefined, { policy }); t.after(fixture.close);
  assert.equal(fixture.ledger.admit(id(1), START).sequence, 1);
  assert.equal(fixture.ledger.confirmClosed(id(2), 1).reason, "completion_invalid");
  assert.equal(fixture.ledger.confirmClosed(id(1), 2).reason, "completion_invalid");
  assert.equal(fixture.ledger.confirmClosed(id(1), 1).status, "closed");
  assert.equal(fixture.ledger.confirmClosed(id(1), 1).reason, "completion_invalid");
});

test("different pilot/target and state corruption refuse without rewriting stored evidence", t => {
  for (const mutate of [state => { state.daily["2026-10-06"] = 1; },
    state => { state.active = 1; }, state => { state.stopped = secret; },
    state => { state.requests.push({ id: id(1), sequence: 1, admittedAtMs: START, closed: true }); },
    state => { state.version = 2; }, state => { state.override = true; }]) {
    const fixture = openFixture(undefined, { policy }); t.after(fixture.close);
    const state = JSON.parse(fixture.database.prepare("SELECT state FROM admission_state").get().state);
    mutate(state); const raw = JSON.stringify(state);
    fixture.database.prepare("UPDATE admission_state SET state = ?").run(raw);
    const result = fixture.ledger.admit(id(2), START);
    assert.equal(result.reason, "state_invalid"); assertNoSecrets(result);
    assert.equal(fixture.database.prepare("SELECT state FROM admission_state").get().state, raw);
  }
  const fixture = openFixture(undefined, { policy }); t.after(fixture.close);
  for (const change of [{ pilotId: "c".repeat(64) }, { targetSha256: "d".repeat(64) },
    { startsAtMs: START + 1000, expiresAtMs: policy.expiresAtMs + 1000 }]) {
    assert.equal(new AdmissionLedger(fixture.store, { ...policy, ...change }).admit(id(1), START).reason,
      "state_invalid");
  }
  fixture.database.prepare("UPDATE admission_state SET state = ?").run(secret);
  assert.equal(fixture.ledger.admit(id(1), START).reason, "state_invalid");
  fixture.database.exec("DELETE FROM admission_state");
  assert.equal(fixture.ledger.admit(id(1), START).reason, "not_initialized");
});

test("ambiguous persisted JSON refuses without normalizing or replacing the record", async t => {
  const variants = [
    raw => raw.replace('"version":1', '"version":2,"version":1'),
    raw => raw.replace('"active":null', '"active":1,"active":null'),
    raw => raw.replace(`"lastSeenMs":${START}`, `"lastSeenMs":${START + 1000},"lastSeenMs":${START}`),
    raw => raw.replace('"pilotId":', '"pilotId":"' + "c".repeat(64) + '","pilotId":'),
    raw => raw.replace('"version":1', '"version":1.0'),
    raw => raw.replace('"version":1', '"version":1e0'),
    raw => " " + raw,
    raw => raw.replace('"policy"', '"pol\\u0069cy"'),
  ];
  for (const mutate of variants) {
    const fixture = openFixture(undefined, { policy }); t.after(fixture.close);
    const original = fixture.database.prepare("SELECT state FROM admission_state").get().state;
    const ambiguous = mutate(original);
    assert.notEqual(ambiguous, original);
    fixture.database.prepare("UPDATE admission_state SET state = ?").run(ambiguous);
    let reads = 0;
    const outcome = await executor(fixture, { startRead: () => { reads++; return resolvedRead(); } })
      .run(request(1), secret);
    assert.equal(outcome.reason, "state_invalid");
    assert.equal(reads, 0);
    assertNoSecrets(outcome);
    assert.equal(fixture.database.prepare("SELECT state FROM admission_state").get().state, ambiguous);
  }
});

test("storage failures and failed transaction commits refuse before read; SQLite writes roll back", async t => {
  let reads = 0;
  const unavailable = new AdmissionLedger({ transaction() { throw new Error(secret); } }, policy);
  const result = await executor({ ledger: unavailable }, { startRead: () => { reads++; return resolvedRead(); } })
    .run(request(1), secret);
  assert.equal(result.reason, "storage_unavailable"); assertNoSecrets(result); assert.equal(reads, 0);
  const fixture = openFixture(undefined, { policy }); t.after(fixture.close);
  const brokenStorage = { sql: fixture.storage.sql, transactionSync(fn) {
    fixture.database.exec("BEGIN IMMEDIATE");
    try { fn(); throw new Error(secret); } finally { fixture.database.exec("ROLLBACK"); }
  } };
  const ledger = new AdmissionLedger(new SqliteAdmissionStore(brokenStorage), policy);
  assert.equal(ledger.admit(id(1), START).reason, "storage_unavailable");
  assert.equal(fixture.ledger.summary().admitted, 0);
});

test("timing loss stops the pilot before any unmeasured read or after safe closure", async t => {
  for (const badCall of [2, 3, 4]) {
    const fixture = openFixture(undefined, { policy }); t.after(fixture.close);
    let measures = 0; let reads = 0;
    const result = await executor(fixture, { measure() {
      measures++;
      if (measures === badCall) return Number.NaN;
      return measures;
    }, startRead() { reads++; return resolvedRead(); } }).run(request(badCall), secret);
    assert.equal(result.reason, "timing_unavailable");
    assert.equal(reads, badCall === 4 ? 1 : 0);
    assert.equal(fixture.ledger.summary().admitted, 1);
    assert.equal(fixture.ledger.summary().stopped, true);
    assert.equal(fixture.ledger.summary().active, false);
  }
});

test("persistent SQLite process restart preserves counts and crash-held reservation", t => {
  const directory = mkdtempSync(join(tmpdir(), "edgar-offline-admission-restart-"));
  const filename = join(directory, "synthetic.sqlite");
  let fixture = openFixture(filename, { policy });
  consume(fixture.ledger, 1, START);
  fixture.ledger.admit(id(2), START);
  fixture.close();
  fixture = openFixture(filename, { create: false, policy }); t.after(fixture.close);
  assert.equal(statSync(directory).mode & 0o777, 0o700);
  assert.equal(statSync(filename).mode & 0o777, 0o600);
  assert.equal(fixture.ledger.summary().admitted, 2);
  assert.equal(fixture.ledger.admit(id(3), START + 100000).reason, "completion_unconfirmed");
  const before = readFileSync(filename);
  assert.throws(() => openFixture(filename, { policy }));
  assert.deepEqual(readFileSync(filename), before);
});

test("persistent fixture reopening refuses symlinks and broadened file permissions", t => {
  const directory = mkdtempSync(join(tmpdir(), "edgar-offline-admission-file-policy-"));
  const filename = join(directory, "synthetic.sqlite");
  const link = join(directory, "synthetic-link.sqlite");
  let fixture = openFixture(filename, { policy }); fixture.close();
  const before = readFileSync(filename);
  symlinkSync(filename, link);
  assert.throws(() => openFixture(link, { create: false, policy }));
  assert.deepEqual(readFileSync(filename), before);
  chmodSync(filename, 0o644);
  assert.throws(() => openFixture(filename, { create: false, policy }));
  assert.deepEqual(readFileSync(filename), before);
});

test("separate processes contend on one actual SQLite file; exactly one reserves", async t => {
  const directory = mkdtempSync(join(tmpdir(), "edgar-offline-admission-contention-"));
  const filename = join(directory, "synthetic.sqlite");
  let fixture = openFixture(filename, { policy }); fixture.close();
  const workers = Array.from({ length: 8 }, () => fork(new URL("./concurrency-fixture.mjs", import.meta.url), [],
    { stdio: ["ignore", "ignore", "ignore", "ipc"] }));
  t.after(() => { for (const worker of workers) if (worker.exitCode === null) worker.kill(); });
  const outcomes = await Promise.all(workers.map((worker, index) => new Promise((resolve, reject) => {
    worker.once("error", reject);
    let response;
    worker.once("message", value => { response = value; });
    worker.once("exit", code => code === 0 && response ? resolve(response) : reject(new Error("Fixture failed")));
    worker.send({ filename, policy, requestId: id(index + 1), nowMs: START });
  })));
  assert.equal(outcomes.filter(value => value.status === "admitted").length, 1);
  assert.equal(outcomes.filter(value => value.reason === "completion_unconfirmed").length, 7);
  fixture = openFixture(filename, { create: false, policy }); t.after(fixture.close);
  assert.equal(fixture.ledger.summary().admitted, 1);
});

test("candidate Durable Object surface exposes no initialization, reset or completion RPC", async t => {
  const fixture = openFixture(undefined, { policy }); t.after(fixture.close);
  class LocalBase { constructor(ctx) { this.ctx = ctx; } }
  const Candidate = createOfflineDurableAdmissionClass(LocalBase, ctx => executor({ ledger:
    new AdmissionLedger(new SqliteAdmissionStore(ctx.storage), policy) }));
  const surface = new Candidate({ storage: fixture.storage }, {});
  assert.deepEqual(Object.getOwnPropertyNames(Candidate.prototype).sort(), ["constructor", "fetch", "runRead"]);
  assert.equal((await surface.runRead(request(1), secret)).status, "completed");
  const response = await surface.fetch(new Request("https://synthetic.invalid/reset", { method: "POST" }));
  assert.equal(response.status, 404);
  assert.equal(response.headers.get("Cache-Control"), "private, no-store");
  assert.equal(fixture.ledger.summary().admitted, 1);
});
