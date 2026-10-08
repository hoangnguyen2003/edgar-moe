import assert from "node:assert/strict";
import test from "node:test";
import { createHash } from "node:crypto";
import { createOfflineStudyPlan, auditOfflineStudy } from "./study-plan.mjs";
import { LIMITS } from "./ledger.mjs";
import { openFixture } from "./sqlite-fixture.mjs";
import { OfflineAdmissionExecutor } from "./executor.mjs";

const START = Date.parse("2026-10-06T00:00:00Z");
const policy = Object.freeze({ pilotId: "a".repeat(64), targetSha256: "b".repeat(64),
  startsAtMs: START, expiresAtMs: START + LIMITS.lifetimeMs });
const id = index => createHash("sha256").update(`offline-study-${index}`).digest("hex");
function receipt(slot, outcome = "completed") {
  return { kind: "synthetic_fixture", pilotId: policy.pilotId, targetSha256: policy.targetSha256,
    ...slot, outcome, controlElapsedMs: outcome === "completed" ? 2 : null,
    readElapsedMs: outcome === "completed" ? 10 : null };
}

test("predeclared 168 hourly pairs rotate queries, reserve 24 checks and fit every UTC daily budget", () => {
  for (const offset of [0, 1, 86399999]) {
    const plan = createOfflineStudyPlan({ ...policy, startsAtMs: START + offset,
      expiresAtMs: policy.expiresAtMs + offset });
    assert.equal(plan.slots.length, 336);
    assert.equal(plan.verificationAllowance, 24);
    assert(Object.isFrozen(plan) && Object.isFrozen(plan.slots) && Object.isFrozen(plan.policy));
    for (let hour = 0; hour < 168; hour++) {
      const [first, second] = plan.slots.slice(hour * 2, hour * 2 + 2);
      assert.equal(first.dueAtMs, START + offset + hour * 3600000);
      assert.equal(second.dueAtMs - first.dueAtMs, 30000);
      assert.equal(first.route, second.route);
      assert.equal(first.phase, "idle_candidate"); assert.equal(second.phase, "followup");
    }
    for (const count of Object.values(plan.dailyMeasurementCounts)) assert(count + 24 <= 80);
    for (const route of ["status", "forecasts", "performance"]) {
      assert.equal(plan.slots.filter(slot => slot.route === route).length, 112);
    }
  }
});

test("complete virtual study uses actual SQLite admission: 336 measurements + 24 checks, no 361st read", async t => {
  const fixture = openFixture(undefined, { policy }); t.after(fixture.close);
  const plan = createOfflineStudyPlan(policy);
  let now = START; let reads = 0;
  const executor = new OfflineAdmissionExecutor({ ledger: fixture.ledger, clock: () => now,
    authorize: value => value === "synthetic-only", startRead: () => {
      reads++; return { result: Promise.resolve(true), closed: Promise.resolve(true) };
    } });
  for (let index = 0; index < 24; index++) {
    assert.equal((await executor.run({ requestId: id(index), route: "status" }, "synthetic-only")).status, "completed");
  }
  const receipts = [];
  for (const slot of plan.slots) {
    now = slot.dueAtMs;
    const result = await executor.run({ requestId: id(slot.index + 24), route: slot.route }, "synthetic-only");
    assert.equal(result.status, "completed");
    receipts.push({ ...receipt(slot), controlElapsedMs: result.controlElapsedMs, readElapsedMs: result.readElapsedMs });
  }
  now = policy.expiresAtMs - 1;
  assert.equal((await executor.run({ requestId: id(360), route: "status" }, "synthetic-only")).reason, "total_budget_exhausted");
  assert.equal(reads, 360); assert.equal(fixture.ledger.summary().totalRemaining, 0);
  const audit = auditOfflineStudy(policy, receipts);
  assert.equal(audit.hostedSevenDayEvidence, false);
  assert.equal(audit.representativeVolumeEvidence, false);
  assert.equal(audit.providerAttributionEvidence, false);
  assert(audit.cohorts.every(cohort => cohort.completed === 56 && cohort.missing === 0 && cohort.failed === 0));
});

test("missing and failed slots stay visible; controller/read timing and query/phase cohorts remain separate", () => {
  const plan = createOfflineStudyPlan(policy);
  const audit = auditOfflineStudy(policy, [receipt(plan.slots[0]), receipt(plan.slots[1], "failed")]);
  assert.deepEqual(audit.cohorts[0], { route: "status", phase: "idle_candidate", planned: 56,
    completed: 1, failed: 0, missing: 55, control: { samples: 1, p50Ms: 2, p95Ms: 2, p99Ms: 2, maxMs: 2 },
    read: { samples: 1, p50Ms: 10, p95Ms: 10, p99Ms: 10, maxMs: 10 }, providerCategory: "unobserved" });
  assert.equal(audit.cohorts[1].failed, 1); assert.equal(audit.cohorts[1].read, null);
  assert.equal(audit.daily[0].missing, 46); assert.equal(audit.daily[1].missing, 48);
  assert.equal(auditOfflineStudy(policy, []).cohorts.reduce((sum, cohort) => sum + cohort.missing, 0), 336);
});

test("receipt drift, retries, fabricated attribution, credentials and malformed timings refuse safely", () => {
  const plan = createOfflineStudyPlan(policy); const valid = receipt(plan.slots[0]);
  const raw = "fixture-only-secret https://synthetic.invalid postgres://synthetic";
  for (const change of [{ kind: "hosted_observation" }, { pilotId: "c".repeat(64) },
    { targetSha256: "c".repeat(64) }, { index: 336 }, { index: 0.5 }, { dueAtMs: START + 1 },
    { route: "performance" }, { phase: "followup" }, { providerCategory: "verified_cold" },
    { credential: raw }, { readElapsedMs: raw }, { readElapsedMs: NaN }, { readElapsedMs: Infinity },
    { readElapsedMs: -1 }, { controlElapsedMs: true }, { outcome: "retried" },
    { outcome: "failed", readElapsedMs: 10 }, { readElapsedMs: 60001 }]) {
    assert.throws(() => auditOfflineStudy(policy, [{ ...valid, ...change }]), error => {
      assert.equal(error.message, "Invalid offline study input"); assert(!error.message.includes(raw)); return true;
    });
  }
  assert.throws(() => auditOfflineStudy(policy, [valid, valid]), /Invalid offline study input/);
  assert.throws(() => createOfflineStudyPlan({ ...policy, expiresAtMs: policy.expiresAtMs + 1 }), /Invalid offline study input/);
  assert.throws(() => createOfflineStudyPlan({ ...policy, endpoint: raw }), /Invalid offline study input/);
});
