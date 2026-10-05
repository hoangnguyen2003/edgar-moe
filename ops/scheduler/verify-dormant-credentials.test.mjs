import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import test from "node:test";
import { verifyDormantCredentials } from "./verify-dormant-credentials.mjs";

const accountId = "a".repeat(32);
const workerId = "b".repeat(32);
const apiToken = "fixture-credential-never-retained";
const config = { accountId, workerId, apiToken,
  expectedAccountSha256: createHash("sha256").update(JSON.stringify(accountId)).digest("hex") };
const worker = () => ({ id: workerId, name: "edgar-moe-forward-scheduler",
  subdomain: { enabled: false, previews_enabled: false }, logpush: false,
  observability: { enabled: false }, tail_consumers: [] });
const json = result => new Response(JSON.stringify({ success: true, result }), { status: 200 });

test("passes with exactly two fixed, GET-only requests and value-free evidence", async () => {
  const calls = [];
  const result = await verifyDormantCredentials({ ...config, fetchImpl: async (url, options) => {
    calls.push(url);
    assert.equal(options.method, "GET");
    assert.equal(options.redirect, "error");
    assert.equal(options.body, undefined);
    assert.equal(options.headers.Authorization, `Bearer ${apiToken}`);
    return json(calls.length === 1 ? worker() : []);
  } });
  assert.equal(result.status, "passed");
  assert.equal(result.request_count, 2);
  assert.equal(result.external_writes, 0);
  assert.deepEqual(calls, [
    `https://api.cloudflare.com/client/v4/accounts/${accountId}/workers/workers/${workerId}`,
    `https://api.cloudflare.com/client/v4/accounts/${accountId}/workers/workers/${workerId}/versions?page=1&per_page=1`,
  ]);
  const text = JSON.stringify(result);
  for (const value of [accountId, workerId, apiToken, "https://", "Bearer"]) assert(!text.includes(value));
  for (const key of ["editor_write_permission_proven", "exclusive_scope_proven", "billing_verified"]) {
    assert.equal(result[key], false);
  }
});

test("invalid credentials or account mismatch never make a request", async () => {
  for (const changed of [{ apiToken: "" }, { accountId: "https://evil.invalid" },
    { workerId: "../scripts" }, { expectedAccountSha256: "c".repeat(64) }]) {
    const result = await verifyDormantCredentials({ ...config, ...changed,
      fetchImpl: () => { throw new Error("must not call"); } });
    assert.equal(result.status, "refused");
    assert.equal(result.request_count, 0);
  }
});

test("auth failures do not retry, follow redirects, or expose response details", async () => {
  for (const status of [401, 403, 302, 429, 500]) {
    let calls = 0;
    const result = await verifyDormantCredentials({ ...config, fetchImpl: async () => {
      calls++;
      return new Response(apiToken, { status });
    } });
    assert.equal(result.status, "refused");
    assert.equal(result.http_status, status);
    assert.equal(calls, 1);
    assert(!JSON.stringify(result).includes(apiToken));
  }
});

test("runtime and identity drift fail closed before a version query", async () => {
  for (const modify of [value => { value.name = "other"; }, value => { value.id = "c".repeat(32); },
    value => { value.subdomain.enabled = true; }, value => { value.subdomain.previews_enabled = true; },
    value => { value.logpush = true; }, value => { value.observability.enabled = true; },
    value => { value.tail_consumers = [{ name: "other" }]; }, value => { delete value.subdomain; }]) {
    const value = worker();
    modify(value);
    const result = await verifyDormantCredentials({ ...config, fetchImpl: async () => json(value) });
    assert.equal(result.status, "refused");
    assert.equal(result.request_count, 1);
  }
});

test("any uploaded version or inconsistent pagination refuses the dormant claim", async () => {
  for (const versions of [json([{ id: "version" }]), new Response(JSON.stringify({
    success: true, result: [], result_info: { total_count: 1 },
  }))]) {
    let calls = 0;
    const result = await verifyDormantCredentials({ ...config,
      fetchImpl: async () => ++calls === 1 ? json(worker()) : versions });
    assert.equal(result.reason, "worker_has_versions");
    assert.equal(result.status, "refused");
    assert.equal(calls, 2);
  }
});

test("malformed, oversized and secret-bearing exceptions never leak details", async () => {
  for (const response of [new Response(apiToken), new Response("x".repeat(32769)),
    new Response(JSON.stringify({ success: false, errors: [{ message: apiToken }] }))]) {
    const result = await verifyDormantCredentials({ ...config, fetchImpl: async () => response });
    assert.equal(result.status, "refused");
    assert.equal(result.request_count, 1);
    assert(!JSON.stringify(result).includes(apiToken));
  }
  const result = await verifyDormantCredentials({ ...config, fetchImpl: async () => {
    throw new Error(apiToken);
  } });
  assert.equal(result.reason, "request_or_response_failed");
  assert(!JSON.stringify(result).includes(apiToken));
});
