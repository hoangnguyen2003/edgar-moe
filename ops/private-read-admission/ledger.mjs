/** Offline candidate: persistent admission only; no database/provider client. */
export const LIMITS = Object.freeze({ total: 360, daily: 80, lifetimeMs: 168 * 60 * 60 * 1000 });
const HEX = /^[a-f0-9]{64}$/;
const STOP_REASONS = new Set(["read_timeout", "read_start_failed", "read_failed", "closure_unconfirmed", "clock_invalid", "pilot_expired", "timing_unavailable"]);
const POLICY_KEYS = ["pilotId", "targetSha256", "startsAtMs", "expiresAtMs"];

class Refusal extends Error {
  constructor(reason) { super("Admission refused"); this.reason = reason; }
}

function exactKeys(value, keys) {
  return value && typeof value === "object" && !Array.isArray(value) &&
    Object.keys(value).sort().join(",") === [...keys].sort().join(",");
}

function validTime(value) {
  return Number.isSafeInteger(value) && value > 0 && value <= 8640000000000000;
}

function policyValid(policy) {
  return exactKeys(policy, POLICY_KEYS) && typeof policy.pilotId === "string" &&
    typeof policy.targetSha256 === "string" && HEX.test(policy.pilotId) &&
    HEX.test(policy.targetSha256) && validTime(policy.startsAtMs) && validTime(policy.expiresAtMs) &&
    policy.expiresAtMs - policy.startsAtMs === LIMITS.lifetimeMs;
}

function samePolicy(left, right) {
  return POLICY_KEYS.every(key => left[key] === right[key]);
}

function day(time) { return new Date(time).toISOString().slice(0, 10); }

function validateState(state, policy) {
  if (!exactKeys(state, ["version", "policy", "lastSeenMs", "requests", "daily", "active", "stopped"]) ||
      state.version !== 1 || !policyValid(state.policy) || !samePolicy(state.policy, policy) ||
      !validTime(state.lastSeenMs) || state.lastSeenMs < policy.startsAtMs ||
      !Array.isArray(state.requests) || state.requests.length > LIMITS.total ||
      !state.daily || typeof state.daily !== "object" || Array.isArray(state.daily) ||
      (state.stopped !== null && !STOP_REASONS.has(state.stopped))) {
    throw new Refusal("state_invalid");
  }
  const ids = new Set();
  const expectedDaily = {};
  let last = policy.startsAtMs;
  let active = null;
  for (const [index, entry] of state.requests.entries()) {
    if (!exactKeys(entry, ["id", "sequence", "admittedAtMs", "closed"]) ||
        typeof entry.id !== "string" || !HEX.test(entry.id) || ids.has(entry.id) ||
        entry.sequence !== index + 1 || !validTime(entry.admittedAtMs) ||
        entry.admittedAtMs < last || entry.admittedAtMs >= policy.expiresAtMs ||
        entry.admittedAtMs > state.lastSeenMs || typeof entry.closed !== "boolean") {
      throw new Refusal("state_invalid");
    }
    ids.add(entry.id);
    last = entry.admittedAtMs;
    const date = day(last);
    expectedDaily[date] = (expectedDaily[date] ?? 0) + 1;
    if (expectedDaily[date] > LIMITS.daily) throw new Refusal("state_invalid");
    if (!entry.closed) {
      if (active !== null || index !== state.requests.length - 1) throw new Refusal("state_invalid");
      active = entry.sequence;
    }
  }
  if (active !== state.active || Object.keys(expectedDaily).length !== Object.keys(state.daily).length ||
      Object.entries(expectedDaily).some(([date, count]) => state.daily[date] !== count)) {
    throw new Refusal("state_invalid");
  }
  return state;
}

function result(reason) { return { status: "refused", reason }; }

export class AdmissionLedger {
  #store;
  #policy;

  constructor(store, policy) {
    if (!policyValid(policy)) throw new Refusal("policy_invalid");
    this.#store = store;
    this.#policy = Object.freeze({ ...policy });
  }

  #transaction(fn) {
    try {
      return this.#store.transaction(raw => {
        if (raw === null) throw new Refusal("not_initialized");
        let state;
        try { state = JSON.parse(raw); } catch { throw new Refusal("state_invalid"); }
        // This record is written only by JSON.stringify below, never imported
        // from an external JSON document. Require a lossless round trip before
        // using its counters: JSON.parse otherwise silently takes the last of
        // duplicate keys, discarding contradictory policy/reservation evidence.
        // Refuse without rewriting/normalizing the original stored bytes.
        if (typeof raw !== "string" || JSON.stringify(state) !== raw) {
          throw new Refusal("state_invalid");
        }
        return fn(validateState(state, this.#policy));
      });
    } catch (error) {
      return result(error instanceof Refusal ? error.reason : "storage_unavailable");
    }
  }

  // Only an offline fixture/bootstrap helper may call this. Not exposed over RPC.
  // A missing record on any ordinary operation refuses; it never auto-recreates.
  initializeOfflineFixture() {
    try {
      return this.#store.transaction(raw => {
        if (raw !== null) return { result: result("already_initialized") };
        return { next: JSON.stringify({ version: 1, policy: this.#policy,
          lastSeenMs: this.#policy.startsAtMs, requests: [], daily: {}, active: null, stopped: null }),
        result: { status: "initialized" } };
      });
    } catch { return result("storage_unavailable"); }
  }

  admit(requestId, nowMs) {
    if (typeof requestId !== "string" || !HEX.test(requestId) || !validTime(nowMs)) {
      return result("request_invalid");
    }
    return this.#transaction(state => {
      if (state.stopped === "pilot_expired") return { result: result("pilot_expired") };
      if (state.stopped !== null) return { result: result("pilot_stopped") };
      // Before the first in-window observation, initialization's start timestamp
      // is not a clock observation. Thereafter even a pre-start rollback stops.
      if (nowMs < this.#policy.startsAtMs && state.lastSeenMs === this.#policy.startsAtMs &&
          state.requests.length === 0) return { result: result("pilot_not_started") };
      if (nowMs < state.lastSeenMs) {
        state.stopped = "clock_invalid";
        return { next: JSON.stringify(state), result: result("clock_invalid") };
      }
      // Valid denied requests are clock observations too. Persist their high-water
      // mark without consuming allowance; observed expiry is irreversible.
      state.lastSeenMs = nowMs;
      const deny = reason => ({ next: JSON.stringify(state), result: result(reason) });
      if (nowMs >= this.#policy.expiresAtMs) {
        state.stopped = "pilot_expired";
        return deny("pilot_expired");
      }
      if (state.requests.some(entry => entry.id === requestId)) return deny("request_replayed");
      if (state.active !== null) return deny("completion_unconfirmed");
      if (state.requests.length >= LIMITS.total) return deny("total_budget_exhausted");
      const date = day(nowMs);
      if ((state.daily[date] ?? 0) >= LIMITS.daily) return deny("daily_budget_exhausted");
      const sequence = state.requests.length + 1;
      state.requests.push({ id: requestId, sequence, admittedAtMs: nowMs, closed: false });
      state.daily[date] = (state.daily[date] ?? 0) + 1;
      state.active = sequence;
      return { next: JSON.stringify(state), result: { status: "admitted", sequence,
        totalRemaining: LIMITS.total - sequence, dailyRemaining: LIMITS.daily - state.daily[date] } };
    });
  }

  // Trusted executor calls only after result settlement AND backend-close proof.
  confirmClosed(requestId, sequence) {
    if (typeof requestId !== "string" || !HEX.test(requestId) || !Number.isSafeInteger(sequence)) {
      return result("completion_invalid");
    }
    return this.#transaction(state => {
      const entry = state.requests[sequence - 1];
      if (!entry || entry.id !== requestId || state.active !== sequence || entry.closed) {
        return { result: result("completion_invalid") };
      }
      entry.closed = true;
      state.active = null;
      return { next: JSON.stringify(state), result: { status: "closed" } };
    });
  }

  stop(requestId, sequence, reason) {
    if (!STOP_REASONS.has(reason)) return result("stop_invalid");
    return this.#transaction(state => {
      const entry = state.requests[sequence - 1];
      if (!entry || entry.id !== requestId || state.active !== sequence) {
        return { result: result("stop_invalid") };
      }
      state.stopped = reason;
      return { next: JSON.stringify(state), result: { status: "stopped" } };
    });
  }

  summary() {
    return this.#transaction(state => ({ result: { status: "observed", admitted: state.requests.length,
      active: state.active !== null, stopped: state.stopped !== null,
      totalRemaining: LIMITS.total - state.requests.length } }));
  }
}
