import assert from "node:assert/strict";
import test from "node:test";

import worker, {
  buildDispatchPayload,
  runScheduledDispatch,
  validateSchedulerConfig,
} from "./cloudflare-forward-scheduler.mjs";

const TOKEN = "github-token-used-only-inside-test";
const ENV = Object.freeze({
  GITHUB_TOKEN: TOKEN,
  GITHUB_REPOSITORY: "hoangnguyen2003/edgar-moe",
  GITHUB_WORKFLOW: "forward-production.yml",
  GITHUB_REF: "main",
  GITHUB_DEVICE: "cpu",
});

const originalFetch = globalThis.fetch;

test.afterEach(() => {
  globalThis.fetch = originalFetch;
});

test("dispatches one forward workflow when no run is active", async () => {
  const calls = [];
  globalThis.fetch = async (url, init) => {
    calls.push({ url: String(url), init });
    if (calls.length === 1) {
      return new Response(JSON.stringify({ workflow_runs: [] }), { status: 200 });
    }
    return new Response(null, { status: 204 });
  };

  const result = await runScheduledDispatch(ENV, "17 7 * * 2-6");

  assert.equal(result.status, "dispatched");
  assert.equal(calls.length, 2);
  assert.match(calls[0].url, /actions\/workflows\/forward-production\.yml\/runs/);
  assert.match(calls[1].url, /actions\/workflows\/forward-production\.yml\/dispatches/);
  assert.equal(calls[0].init.headers.Authorization, `Bearer ${TOKEN}`);
  assert.deepEqual(
    JSON.parse(calls[1].init.body),
    buildDispatchPayload(validateSchedulerConfig(ENV)),
  );
});

test("skips dispatch when an active run exists", async () => {
  const calls = [];
  globalThis.fetch = async (url, init) => {
    calls.push({ url: String(url), init });
    return new Response(JSON.stringify({ workflow_runs: [{ id: 42, status: "in_progress" }] }), {
      status: 200,
    });
  };

  const result = await runScheduledDispatch(ENV);

  assert.deepEqual(result, { status: "skipped", reason: "active_run", run_id: 42, cron: null });
  assert.equal(calls.length, 1);
});

test("configuration rejects unsafe repository and ref values", () => {
  assert.throws(
    () => validateSchedulerConfig({ ...ENV, GITHUB_REPOSITORY: "attacker/repo?token=leak" }),
    /owner\/repository/,
  );
  assert.throws(
    () => validateSchedulerConfig({ ...ENV, GITHUB_REF: "main..secret" }),
    /unsupported characters/,
  );
});

test("configuration errors never echo the GitHub token", () => {
  assert.throws(
    () => validateSchedulerConfig({ GITHUB_TOKEN: TOKEN, GITHUB_REPOSITORY: "bad" }),
    (error) => error instanceof Error && !error.message.includes(TOKEN),
  );
});

test("there is no unauthenticated public trigger", async () => {
  const response = await worker.fetch(new Request("https://scheduler.example/trigger"), ENV);
  assert.equal(response.status, 404);
});

test("GitHub API errors fail closed without exposing response content", async () => {
  globalThis.fetch = async () => new Response("token-like response body", { status: 503 });

  await assert.rejects(
    () => runScheduledDispatch(ENV),
    (error) => error instanceof Error && /HTTP 503/.test(error.message) && !error.message.includes("token-like"),
  );
});
