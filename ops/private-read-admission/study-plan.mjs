/** Offline preflight only. No clock, network, timers, scheduler or provider clients. */
import { LIMITS } from "./ledger.mjs";

const HASH = /^[a-f0-9]{64}$/;
const HOUR_MS = 3600000;
const PAIR_DELAY_MS = 30000;
const ROUTES = ["status", "forecasts", "performance"];
const PHASES = ["idle_candidate", "followup"];
const POLICY_KEYS = ["pilotId", "targetSha256", "startsAtMs", "expiresAtMs"];

function fail() { throw new Error("Invalid offline study input"); }
function exact(value, keys) {
  return value && typeof value === "object" && !Array.isArray(value) &&
    Object.keys(value).length === keys.length && keys.every(key => Object.hasOwn(value, key));
}
function validTime(value) {
  return Number.isSafeInteger(value) && value > 0 && value <= 8640000000000000;
}
function validPolicy(policy) {
  return exact(policy, POLICY_KEYS) && typeof policy.pilotId === "string" && HASH.test(policy.pilotId) &&
    typeof policy.targetSha256 === "string" && HASH.test(policy.targetSha256) &&
    validTime(policy.startsAtMs) && validTime(policy.expiresAtMs) &&
    policy.expiresAtMs - policy.startsAtMs === LIMITS.lifetimeMs;
}
const date = time => new Date(time).toISOString().slice(0, 10);

export function createOfflineStudyPlan(policy) {
  if (!validPolicy(policy)) fail();
  const frozenPolicy = Object.freeze({ ...policy });
  const slots = Array.from({ length: 336 }, (_, index) => {
    const hour = Math.floor(index / 2);
    const phase = index % 2;
    return Object.freeze({ index, route: ROUTES[hour % ROUTES.length], phase: PHASES[phase],
      dueAtMs: frozenPolicy.startsAtMs + hour * HOUR_MS + phase * PAIR_DELAY_MS });
  });
  const daily = {};
  for (const slot of slots) daily[date(slot.dueAtMs)] = (daily[date(slot.dueAtMs)] ?? 0) + 1;
  // All 24 verification reads could be admitted on one date: the plan still
  // leaves sufficient daily headroom. Verification itself is not scheduled here.
  if (slots.some(slot => slot.dueAtMs >= frozenPolicy.expiresAtMs) ||
      Math.max(...Object.values(daily)) + 24 > LIMITS.daily || slots.length + 24 !== LIMITS.total) fail();
  return Object.freeze({ kind: "offline_synthetic_study_plan", policy: frozenPolicy,
    slots: Object.freeze(slots), verificationAllowance: 24, pairDelayMs: PAIR_DELAY_MS,
    measurementReads: slots.length, dailyMeasurementCounts: Object.freeze(daily) });
}

function timings(values) {
  if (!values.length) return null;
  const sorted = [...values].sort((left, right) => left - right);
  const percentile = fraction => {
    const position = (sorted.length - 1) * fraction;
    const lower = Math.floor(position);
    return sorted[lower] + (sorted[Math.ceil(position)] - sorted[lower]) * (position - lower);
  };
  return { samples: sorted.length, p50Ms: percentile(0.5), p95Ms: percentile(0.95),
    p99Ms: percentile(0.99), maxMs: sorted.at(-1) };
}

// Each input is a synthetic fixture receipt, never a hosted observation. A
// missing slot remains missing; an extra/duplicate slot cannot backfill it.
// Actual provider cold/resume/cache categories are deliberately not accepted.
export function auditOfflineStudy(policy, receipts) {
  const plan = createOfflineStudyPlan(policy);
  if (!Array.isArray(receipts) || receipts.length > plan.measurementReads) fail();
  const byIndex = new Map();
  for (const receipt of receipts) {
    if (!exact(receipt, ["kind", "pilotId", "targetSha256", "index", "dueAtMs", "route", "phase",
      "outcome", "controlElapsedMs", "readElapsedMs"]) ||
      receipt.kind !== "synthetic_fixture" || receipt.pilotId !== plan.policy.pilotId ||
      receipt.targetSha256 !== plan.policy.targetSha256 || !Number.isSafeInteger(receipt.index) ||
      receipt.index < 0 || receipt.index >= plan.measurementReads || byIndex.has(receipt.index) ||
      !["completed", "failed"].includes(receipt.outcome)) fail();
    const slot = plan.slots[receipt.index];
    if (receipt.dueAtMs !== slot.dueAtMs || receipt.route !== slot.route || receipt.phase !== slot.phase) fail();
    for (const key of ["controlElapsedMs", "readElapsedMs"]) {
      const value = receipt[key];
      if (receipt.outcome === "failed" ? value !== null :
        typeof value !== "number" || !Number.isFinite(value) || value < 0 || value > 60000) fail();
    }
    byIndex.set(receipt.index, { ...receipt });
  }
  const cohorts = [];
  for (const route of ROUTES) for (const phase of PHASES) {
    const planned = plan.slots.filter(slot => slot.route === route && slot.phase === phase);
    const observed = planned.map(slot => byIndex.get(slot.index)).filter(Boolean);
    const completed = observed.filter(receipt => receipt.outcome === "completed");
    cohorts.push({ route, phase, planned: planned.length, completed: completed.length,
      failed: observed.length - completed.length, missing: planned.length - observed.length,
      control: timings(completed.map(receipt => receipt.controlElapsedMs)),
      read: timings(completed.map(receipt => receipt.readElapsedMs)),
      providerCategory: "unobserved" });
  }
  const daily = Object.entries(plan.dailyMeasurementCounts).map(([utcDate, planned]) => {
    const observed = [...byIndex.values()].filter(receipt => date(receipt.dueAtMs) === utcDate);
    return { utcDate, planned, completed: observed.filter(receipt => receipt.outcome === "completed").length,
      failed: observed.filter(receipt => receipt.outcome === "failed").length, missing: planned - observed.length };
  });
  return { kind: "offline_synthetic_study_audit", plannedMeasurementReads: plan.measurementReads,
    reservedVerificationReads: plan.verificationAllowance, cohorts, daily,
    hostedSevenDayEvidence: false, representativeVolumeEvidence: false, providerAttributionEvidence: false,
    limitations: ["Synthetic scheduled timestamps are not actual observation dates",
      "Idle candidates and followups are not provider cold/warm/resume attribution",
      "Controller and read intervals are separate; neither is end-to-end hosted latency",
      "Sparse empirical percentiles do not prove a tail SLO or capacity"] };
}
