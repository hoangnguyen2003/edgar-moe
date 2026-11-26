import assert from "node:assert/strict";
import test from "node:test";
import { manage, writeDiagnosticResult } from "./manage-study-delivery.mjs";
import { summarizeScheduledEvent } from "./study-delivery-tail.mjs";

const account = "a".repeat(32);
const env = {
  STUDY_DELIVERY_CLOUDFLARE_ACCOUNT_ID: account,
  STUDY_DELIVERY_CLOUDFLARE_TOKEN: "synthetic_cloudflare_token_only",
  STUDY_DELIVERY_GITHUB_TOKEN: "synthetic_github_token_only",
  STUDY_DELIVERY_SIGNING_KEY: "b".repeat(64),
  STUDY_DELIVERY_STARTS_AT: "2026-11-26T05:00:00.000Z",
};
const BASE = `https://api.cloudflare.com/client/v4/accounts/${account}/workers/scripts/edgar-moe-study-delivery`;
const response = (result) => new Response(JSON.stringify({ success: true, result }));

function fixture({ cron = [], urls = false, bindings = [] } = {}) {
  let schedules = cron;
  const writes = [];
  const send = async (url, options) => {
    assert.equal(options.redirect, "error");
    assert.ok(url === BASE || url.startsWith(BASE + "/"));
    const path = url.slice(BASE.length);
    if (options.method === "PUT") {
      writes.push({ path, body: options.body });
      if (path === "/schedules") schedules = JSON.parse(options.body);
      return response({});
    }
    if (path === "/schedules") return response({ schedules });
    if (path === "/subdomain") return response({ enabled: urls, previews_enabled: false });
    if (path === "/settings") return response({ bindings });
    assert.fail("unexpected API call");
  };
  return { send, writes };
}

test("stages only the isolated worker before activating one finite cron", async () => {
  const { send, writes } = fixture();
  const result = await manage({ stage: "START", env, source: "export default {}", send,
    now: () => Date.parse("2026-11-26T04:30:00Z") });
  assert.equal(result.cron_active, true);
  assert.equal(result.expires_at, "2026-11-27T05:00:00.000Z");
  assert.deepEqual(writes.map((call) => call.path), ["", "/schedules"]);
  const metadata = JSON.parse(await writes[0].body.get("metadata").text());
  assert.equal(metadata.observability.enabled, false);
  assert.deepEqual(metadata.bindings.map(({ name, type }) => [name, type]), [
    ["STUDY_DELIVERY_STARTS_AT", "plain_text"],
    ["STUDY_DELIVERY_GITHUB_TOKEN", "secret_text"],
    ["STUDY_DELIVERY_SIGNING_KEY", "secret_text"],
  ]);
  assert.deepEqual(JSON.parse(writes[1].body), [{ cron: "17 * * * *" }]);
  assert.ok(!JSON.stringify(result).includes(env.STUDY_DELIVERY_GITHUB_TOKEN));
});

test("refuses public URLs, foreign schedules or bindings, and late starts before writes", async () => {
  for (const [overrides, clock, error] of [
    [{ urls: true }, "04:30", /urls_refused/],
    [{ cron: [{ cron: "* * * * *" }] }, "04:30", /unexpected_schedule/],
    [{ bindings: [{ name: "UNRELATED", type: "kv_namespace" }] }, "04:30", /unexpected_worker_bindings/],
    [{}, "04:59", /start_bindings_refused/],
  ]) {
    const { send, writes } = fixture(overrides);
    await assert.rejects(manage({ stage: "START", env, source: "export default {}", send,
      now: () => Date.parse(`2026-11-26T${clock}:00Z`) }), error);
    assert.deepEqual(writes, []);
  }
});

test("verify is read-only and stop only removes the known cron", async () => {
  const { send, writes } = fixture({ cron: [{ cron: "17 * * * *" }] });
  const cloudflareOnly = Object.fromEntries(Object.entries(env).filter(([key]) => key.includes("CLOUDFLARE")));
  assert.equal((await manage({ stage: "VERIFY", env: cloudflareOnly, send })).cron_active, true);
  assert.deepEqual(writes, []);
  assert.equal((await manage({ stage: "STOP", env: cloudflareOnly, send })).cron_active, false);
  assert.deepEqual(writes, [{ path: "/schedules", body: "[]" }]);
  await manage({ stage: "STOP", env: cloudflareOnly, send });
  assert.equal(writes.length, 1);
});

test("diagnose observes one existing event and deletes its temporary tail", async () => {
  const { send: manageRequest, writes } = fixture({ cron: [{ cron: "17 * * * *" }] });
  const tailId = "c".repeat(32);
  const url = `wss://tail.developers.workers.dev/${tailId}`;
  const scheduled = Date.parse("2026-11-26T05:17:00Z");
  const calls = [];
  const send = async (requestUrl, options) => {
    const path = requestUrl.slice(BASE.length);
    if (path.startsWith("/tails")) {
      calls.push([path, options.method]);
      return response({ id: tailId, url, expires_at: "2026-11-26T05:25:00Z" });
    }
    return manageRequest(requestUrl, options);
  };
  const parameters = {
    stage: "DIAGNOSE",
    env: Object.fromEntries(Object.entries(env).filter(([key]) =>
      !key.includes("GITHUB_TOKEN") && !key.includes("SIGNING_KEY"))),
    send,
    now: () => scheduled - 10 * 60_000,
    sleep: async (ms) => assert.equal(ms, 510_000),
  };
  const result = await manage({
    ...parameters,
    observe: async (options) => {
      assert.deepEqual(options, { url, scheduled, ends: scheduled + 360_000,
        cron: "17 * * * *", worker: "edgar-moe-study-delivery" });
      return { event_observed: true, outcome: "ok" };
    },
  });
  assert.equal(result.event_observed, true);
  assert.deepEqual(calls, [["/tails", "POST"], [`/tails/${tailId}`, "DELETE"]]);
  assert.deepEqual(writes, []);
  assert.ok(!JSON.stringify(result).includes(url));

  calls.length = 0;
  await assert.rejects(manage({
    ...parameters,
    observe: async () => { throw new Error("delivery_tail_connection_failed"); },
  }), /delivery_tail_connection_failed/);
  assert.deepEqual(calls, [["/tails", "POST"], [`/tails/${tailId}`, "DELETE"]]);

  calls.length = 0;
  await assert.rejects(manage({
    ...parameters,
    now: () => scheduled + 24 * 3_600_000,
    observe: async () => assert.fail("unexpected observation"),
  }), /delivery_diagnostic_outside_window/);
  assert.deepEqual(calls, []);
});

test("tail receipts omit arbitrary exception text, raw logs and unrelated events", () => {
  const scheduled = Date.parse("2026-11-26T05:17:00Z");
  const expected = { scheduled, ends: scheduled + 360_000,
    cron: "17 * * * *", worker: "edgar-moe-study-delivery" };
  const event = {
    scriptName: expected.worker,
    event: { cron: expected.cron, scheduledTime: scheduled },
    eventTimestamp: scheduled + 500,
    outcome: "exception",
    exceptions: [{ message: "GitHub declined external delivery", stack: "private stack" }],
    logs: [{ message: ["private log"] }],
  };
  assert.deepEqual(summarizeScheduledEvent(JSON.stringify(event), expected), {
    event_observed: true, scheduled_time_matches: true, delivery_delay_ms: 500,
    outcome: "failed", error: "github_declined",
  });
  for (const scriptName of [undefined, null]) {
    const workerScopedFrame = new TextEncoder().encode(JSON.stringify({ ...event, scriptName })).buffer;
    assert.equal(summarizeScheduledEvent(workerScopedFrame, expected).error, "github_declined");
  }
  event.exceptions[0].message = "GitHub declined external delivery (HTTP 403)";
  assert.equal(summarizeScheduledEvent(JSON.stringify(event), expected).error, "github_http_403");
  event.exceptions[0].message = "another private value";
  assert.equal(summarizeScheduledEvent(JSON.stringify(event), expected).error, "other");
  assert.equal(summarizeScheduledEvent(JSON.stringify({ ...event, scriptName: "other" }), expected),
    undefined);
  assert.equal(summarizeScheduledEvent(JSON.stringify({ ...event, eventTimestamp: expected.ends }),
    expected), undefined);
});

test("check GitHub dispatches only a credential check with the supplied token", async () => {
  const githubOnly = { STUDY_DELIVERY_GITHUB_TOKEN: env.STUDY_DELIVERY_GITHUB_TOKEN };
  const result = await manage({
    stage: "CHECK_GITHUB",
    env: githubOnly,
    send: async (url, options) => {
      assert.equal(url, "https://api.github.com/repos/hoangnguyen2003/edgar-moe/actions/workflows/study-external-delivery.yml/dispatches");
      assert.equal(options.method, "POST");
      assert.equal(options.redirect, "error");
      assert.equal(options.headers.Authorization, `Bearer ${githubOnly.STUDY_DELIVERY_GITHUB_TOKEN}`);
      assert.deepEqual(JSON.parse(options.body), {
        ref: "main",
        inputs: { check_credentials: true, scheduled_at: "credential-check", signature: "credential-check" },
      });
      return new Response(JSON.stringify({ workflow_run_id: 1234, private: "omit" }));
    },
  });
  assert.deepEqual(result, { stage: "CHECK_GITHUB", workflow_run_id: 1234 });
  await assert.rejects(manage({
    stage: "CHECK_GITHUB",
    env: githubOnly,
    send: async () => new Response("never logged", { status: 403 }),
  }), /delivery_github_http_403/);
});

test("checks active version metadata with the Worker-scoped token and no writes", async () => {
  const { send: fixed, writes } = fixture({ cron: [{ cron: "17 * * * *" }] });
  const versionId = "023e105f-2a42-4f8b-a1c1-73f6a2a30c0f";
  const result = await manage({
    stage: "VERIFY_ACTIVE", env,
    send: async (url, options) => {
      assert.equal(options.method, "GET");
      if (url === BASE + "/deployments?per_page=1") {
        return response({ deployments: [{ versions: [{ version_id: versionId, percentage: 100 }] }] });
      }
      if (url === BASE + `/versions/${versionId}`) {
        return response({ resources: {
          script: { handlers: ["fetch", "scheduled"] },
          bindings: [
            { name: "STUDY_DELIVERY_STARTS_AT", type: "plain_text", text: env.STUDY_DELIVERY_STARTS_AT },
            { name: "STUDY_DELIVERY_GITHUB_TOKEN", type: "secret_text", text: "sensitive" },
            { name: "STUDY_DELIVERY_SIGNING_KEY", type: "secret_text" },
          ],
        } });
      }
      return fixed(url, options);
    },
  });
  assert.deepEqual(result, {
    stage: "VERIFY_ACTIVE", cron_active: true, worker: "edgar-moe-study-delivery",
    scheduled_handler_present: true, start_matches: true, expected_secret_bindings_present: true,
  });
  assert.deepEqual(writes, []);
});

test("diagnostic CLI exits only after the final report has been flushed", () => {
  let drained;
  const exits = [];
  const output = { write(line, callback) {
    assert.equal(line, "diagnostic output\n");
    drained = callback;
  } };
  writeDiagnosticResult(output, "diagnostic output", 0, (code) => exits.push(code));
  assert.deepEqual(exits, []);
  drained();
  assert.deepEqual(exits, [0]);
});
