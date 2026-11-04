import assert from "node:assert/strict";
import {createHash} from "node:crypto";
import test from "node:test";
import {verifyPreparedScheduler} from "./verify-prepared-scheduler.mjs";
const accountId = "a".repeat(32);
const apiToken = "test-token-never-an-actual-credential";
const expectedAccountSha256 = createHash("sha256").update(JSON.stringify(accountId)).digest("hex");
const versions = [{id: "test-version-1"}];
const worker = {
  id: "d8be7a3ac48547e98a48e99f1eb145a1", name: "edgar-moe-forward-scheduler",
  subdomain: {enabled: false, previews_enabled: false}, logpush: false,
  observability: {enabled: false}, tail_consumers: [],
};
function fixture(payloads) {
  const calls = [];
  const fetchImpl = async (url, init) => {
    calls.push({url, init});
    const result = payloads[calls.length - 1];
    if (result instanceof Response) return result;
    return Response.json({success: true, result});
  };
  return {calls, args: {accountId, apiToken, expectedAccountSha256, fetchImpl}};
}
for (const present of [false, true]) {
  test(`verifies inert preparation with dispatch secret present=${present}`, async () => {
    const f = fixture([worker, versions, {schedules: []}, present ? [{name: "GITHUB_TOKEN", type: "secret_text"}] : []]);
    const report = await verifyPreparedScheduler(f.args);
    assert.equal(report.status, "passed");
    assert.equal(report.dispatch_secret_present, present);
    assert.equal(report.dispatch_credential_validity_proven, false);
    assert.equal(report.cron_disabled, true);
    assert.equal(report.request_count, 4);
    assert.equal(report.uploaded_version_seen, true);
    assert.equal(report.external_writes, 0);
    assert.equal(f.calls.length, 4);
    for (const c of f.calls) {
      assert.equal(c.init.method, "GET");
      assert.equal(c.init.redirect, "error");
      assert.equal(c.init.headers.Authorization, `Bearer ${apiToken}`);
    }
    assert.match(f.calls[0].url, /\/workers\/workers\/d8be7a3ac48547e98a48e99f1eb145a1$/);
    assert.match(f.calls[1].url, /\/versions\?page=1&per_page=1$/);
    assert.match(f.calls[2].url, /\/workers\/scripts\/edgar-moe-forward-scheduler\/schedules$/);
    assert.match(f.calls[3].url, /\/workers\/scripts\/edgar-moe-forward-scheduler\/secrets$/);
    assert.ok(!JSON.stringify(report).includes(apiToken));
    assert.ok(!JSON.stringify(report).includes(accountId));
  });
}
test("wrong account refuses before network", async () => {
  const f = fixture([]);
  const r = await verifyPreparedScheduler({...f.args, expectedAccountSha256: "b".repeat(64)});
  assert.equal(r.reason, "account_mismatch");
  assert.equal(f.calls.length, 0);
});
test("wrong Worker or exposed URLs stop before schedule/secret reads", async () => {
  for (const changed of [{...worker, name: "another-worker"}, {...worker, subdomain: {enabled: true}}]) {
    const f = fixture([changed]);
    const r = await verifyPreparedScheduler(f.args);
    assert.equal(r.status, "refused");
    assert.equal(f.calls.length, 1);
  }
});
test("enabled Cron refuses before secret metadata", async () => {
  const f = fixture([worker, versions, {schedules: [{cron: "* * * * *"}]}]);
  const r = await verifyPreparedScheduler(f.args);
  assert.equal(r.reason, "cron_is_enabled");
  assert.equal(f.calls.length, 3);
});
test("authentication failure never retries or leaks response", async () => {
  const f = fixture([new Response(apiToken, {status: 401})]);
  const r = await verifyPreparedScheduler(f.args);
  assert.equal(r.reason, "authentication_refused");
  assert.equal(r.http_status, 401);
  assert.equal(f.calls.length, 1);
  assert.ok(!JSON.stringify(r).includes(apiToken));
});
test("unexpected secrets, secret values and malformed schedules refuse", async () => {
  for (const payload of [[{name: "OTHER", type: "secret_text"}], [{name: "GITHUB_TOKEN", type: "secret_text", text: apiToken}], null]) {
    const f = fixture([worker, versions, {schedules: []}, payload]);
    const r = await verifyPreparedScheduler(f.args);
    assert.equal(r.status, "refused");
    assert.equal(r.reason, "unexpected_secret_metadata");
    assert.ok(!JSON.stringify(r).includes(apiToken));
  }
  const f = fixture([worker, versions, []]);
  assert.equal((await verifyPreparedScheduler(f.args)).reason, "invalid_schedule_response");
});
test("oversized response and thrown secrets are withheld", async () => {
  const f = fixture([new Response("x".repeat(32769))]);
  assert.equal((await verifyPreparedScheduler(f.args)).reason, "response_size_limit");
  const r = await verifyPreparedScheduler({accountId, apiToken, expectedAccountSha256,
    fetchImpl: async () => {throw new Error(apiToken);}});
  assert.equal(r.reason, "request_or_response_failed");
  assert.ok(!JSON.stringify(r).includes(apiToken));
});

test("empty Worker cannot pass preparation verification", async () => {
  const f = fixture([worker, []]);
  const r = await verifyPreparedScheduler(f.args);
  assert.equal(r.reason, "worker_has_no_code");
  assert.equal(r.status, "refused");
  assert.equal(r.uploaded_version_seen, false);
  assert.equal(f.calls.length, 2);
});
