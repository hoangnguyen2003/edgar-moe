import assert from "node:assert/strict";
import {createHash} from "node:crypto";
import test from "node:test";
import {managePrivateScheduler} from "./manage-private-scheduler.mjs";

const account = "a".repeat(32);
const key = "12".repeat(32);
const env = {CLOUDFLARE_ACCOUNT_ID: account, CLOUDFLARE_API_TOKEN: "test-token-never-in-receipt", SCHEDULER_SIGNATURE_KEY: key};
const worker = {id: "d8be7a3ac48547e98a48e99f1eb145a1", name: "edgar-moe-forward-scheduler",
  subdomain: {enabled: false, previews_enabled: false}, logpush: false, observability: {enabled: false}, tail_consumers: []};
const secret = {name: "GITHUB_TOKEN", type: "secret_text"};
async function check(phase, results, override = {}) {
  const calls = [];
  const report = await managePrivateScheduler({phase, env,
    expectedAccountSha256: createHash("sha256").update(JSON.stringify(account)).digest("hex"),
    fetchImpl: async (url, init) => {
      calls.push({url, init});
      return new Response(JSON.stringify({success: true, result: results[calls.length - 1]}));
    }, ...override});
  assert.equal(JSON.stringify(report).includes(env.CLOUDFLARE_API_TOKEN), false);
  assert.equal(JSON.stringify(report).includes(key), false);
  return {report, calls};
}

test("installs exactly the separate signing secret after three bounded GETs", async () => {
  const {report, calls} = await check("INSTALL_KEY", [worker, {schedules: []}, [secret], {}]);
  assert.equal(report.status, "passed");
  assert.equal(report.external_writes, 1);
  assert.deepEqual(calls.map(c => c.init.method), ["GET", "GET", "GET", "PUT"]);
  const body = JSON.parse(calls[3].init.body);
  assert.deepEqual(body, {name: "SCHEDULER_SIGNATURE_KEY", type: "secret_text", text: key});
});

test("active or drifted resources cannot receive a signing key", async () => {
  for (const results of [
    [{...worker, id: "wrong"}],
    [worker, {schedules: [{cron: "17 7 * * 2-6"}]}],
    [worker, {schedules: []}, [secret, {name: "SCHEDULER_SIGNATURE_KEY", type: "secret_text"}]],
    [worker, {schedules: []}, [secret, {name: "unknown", type: "secret_text"}]],
  ]) {
    const {report, calls} = await check("INSTALL_KEY", results);
    assert.equal(report.status, "refused");
    assert.equal(report.external_writes, 0);
    assert.equal(calls.every(c => c.init.method === "GET"), true);
  }
});

test("active verification requires exact Cron and both secret names", async () => {
  const results = [worker, {schedules: [{cron: "17 7 * * 2-6"}]}, [secret, {name: "SCHEDULER_SIGNATURE_KEY", type: "secret_text"}]];
  const {report} = await check("VERIFY_ACTIVE", results);
  assert.equal(report.status, "passed");
  assert.equal(report.cron_enabled, true);
  assert.equal(report.forecast_margin_proven, false);
  assert.equal(report.external_writes, 0);
  const {report: disabled} = await check("VERIFY_DISABLED", results);
  assert.equal(disabled.status, "refused");
});

test("account mismatch refuses before network", async () => {
  const {report, calls} = await check("INSTALL_KEY", [], {expectedAccountSha256: "0".repeat(64)});
  assert.equal(report.status, "refused");
  assert.equal(calls.length, 0);
});
