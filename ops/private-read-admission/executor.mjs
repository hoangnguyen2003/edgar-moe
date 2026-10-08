/** Trusted execution boundary. Dependency injection is local, never caller/RPC input. */
const ROUTES = new Set(["status", "forecasts", "performance"]);
const HASH = /^[a-f0-9]{64}$/;
const READ_TIMEOUT_MS = 10000;

function refuse(reason) { return { status: "refused", reason }; }

export class OfflineAdmissionExecutor {
  #ledger;
  #authorize;
  #startRead;
  #clock;
  #timer;
  #measure;

  constructor({ ledger, authorize, startRead, clock = Date.now,
    timer = { set: setTimeout, clear: clearTimeout }, measure = () => performance.now() }) {
    this.#ledger = ledger;
    this.#authorize = authorize;
    this.#startRead = startRead;
    this.#clock = clock;
    this.#timer = timer;
    this.#measure = measure;
  }

  #stopAfterSafeClosure(requestId, sequence, reason) {
    try {
      const stopped = this.#ledger.stop(requestId, sequence, reason);
      if (stopped.status !== "stopped") return refuse("control_unavailable");
      const closed = this.#ledger.confirmClosed(requestId, sequence);
      return refuse(closed.status === "closed" ? reason : "completion_unconfirmed");
    } catch { return refuse("control_unavailable"); }
  }

  async run(input, authentication) {
    try { return await this.#run(input, authentication); }
    catch { return refuse("execution_unavailable"); }
  }

  async #run(input, authentication) {
    try {
      if (await this.#authorize(authentication) !== true) return refuse("unauthorized");
    } catch { return refuse("unauthorized"); }
    if (!input || typeof input !== "object" || Array.isArray(input) ||
        Object.keys(input).sort().join(",") !== "requestId,route") {
      return refuse("request_invalid");
    }
    const requestId = input.requestId;
    const route = input.route;
    if (typeof requestId !== "string" || !HASH.test(requestId) || !ROUTES.has(route)) {
      return refuse("request_invalid");
    }
    let admission;
    let controlStarted;
    try {
      controlStarted = this.#measure();
      admission = this.#ledger.admit(requestId, this.#clock());
    } catch { return refuse("control_unavailable"); }
    if (admission.status !== "admitted") return admission;
    // The immutable values must not be read again from a mutable caller object.
    const sequence = admission.sequence;
    let controlElapsed;
    try { controlElapsed = this.#measure() - controlStarted; }
    catch { return this.#stopAfterSafeClosure(requestId, sequence, "timing_unavailable"); }
    if (!Number.isFinite(controlElapsed) || controlElapsed < 0) {
      return this.#stopAfterSafeClosure(requestId, sequence, "timing_unavailable");
    }
    let readStarted;
    try { readStarted = this.#measure(); }
    catch { return this.#stopAfterSafeClosure(requestId, sequence, "timing_unavailable"); }
    if (!Number.isFinite(readStarted)) {
      return this.#stopAfterSafeClosure(requestId, sequence, "timing_unavailable");
    }
    let handle;
    let resultPromise;
    let closedPromise;
    try {
      handle = this.#startRead(Object.freeze({ requestId, route }));
      if (handle instanceof Promise) handle.catch(() => {});
      resultPromise = handle?.result;
      if (resultPromise instanceof Promise) resultPromise.catch(() => {});
      closedPromise = handle?.closed;
      if (closedPromise instanceof Promise) closedPromise.catch(() => {});
      if (!(resultPromise instanceof Promise) || !(closedPromise instanceof Promise)) {
        throw new Error("Invalid trusted reader contract");
      }
    } catch {
      this.#ledger.stop(requestId, sequence, "read_start_failed");
      return refuse("read_start_failed");
    }
    let timeoutId;
    const timeout = new Promise(resolve => {
      timeoutId = this.#timer.set(() => resolve({ timeout: true }), READ_TIMEOUT_MS);
    });
    // Observe both promises to prevent rejected driver messages escaping as unhandled rejections.
    const settled = Promise.allSettled([resultPromise, closedPromise]);
    let outcome;
    try { outcome = await Promise.race([settled, timeout]); }
    finally { this.#timer.clear(timeoutId); }
    if (outcome?.timeout) {
      this.#ledger.stop(requestId, sequence, "read_timeout");
      // No automatic slot release, lease expiry, restart, retry or fallback store.
      return refuse("read_timeout");
    }
    const [readResult, closeResult] = outcome;
    if (closeResult.status !== "fulfilled" || closeResult.value !== true) {
      this.#ledger.stop(requestId, sequence, "closure_unconfirmed");
      return refuse("closure_unconfirmed");
    }
    if (readResult.status !== "fulfilled" || readResult.value !== true) {
      const stopped = this.#ledger.stop(requestId, sequence, "read_failed");
      if (stopped.status !== "stopped") return refuse("completion_unconfirmed");
      const closed = this.#ledger.confirmClosed(requestId, sequence);
      return refuse(closed.status === "closed" ? "read_failed" : "completion_unconfirmed");
    }
    // No raw reader value, headers, driver text, caller input or identity is retained/returned.
    let readElapsed;
    try { readElapsed = this.#measure() - readStarted; }
    catch { return this.#stopAfterSafeClosure(requestId, sequence, "timing_unavailable"); }
    if (!Number.isFinite(readElapsed) || readElapsed < 0) {
      return this.#stopAfterSafeClosure(requestId, sequence, "timing_unavailable");
    }
    const closed = this.#ledger.confirmClosed(requestId, sequence);
    if (closed.status !== "closed") return refuse("completion_unconfirmed");
    return { status: "completed", reason: "synthetic_execution_completed",
      controlElapsedMs: controlElapsed, readElapsedMs: readElapsed,
      totalRemaining: admission.totalRemaining, dailyRemaining: admission.dailyRemaining };
  }
}

// Candidate adapter factory, intentionally not a deployable configured Worker.
// A later reviewed integration must supply a real trusted authorization/reader contract.
// No initialize/reset/confirm/stop RPC is exposed by this class.
export function createOfflineDurableAdmissionClass(Base, dependencies) {
  return class OfflineDurableAdmission extends Base {
    #executor;
    constructor(ctx, env) {
      super(ctx, env);
      this.#executor = dependencies(ctx);
    }
    async runRead(input, authentication) { return this.#executor.run(input, authentication); }
    async fetch() { return new Response("Unavailable", { status: 404,
      headers: { "Cache-Control": "private, no-store" } }); }
  };
}
