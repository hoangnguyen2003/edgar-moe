import assert from "node:assert/strict";
import {createHash} from "node:crypto";
import test from "node:test";
import {uploadPrivateWorker} from "./upload-private-worker.mjs";

const account = "a".repeat(32);
const env = {CLOUDFLARE_ACCOUNT_ID: account, CLOUDFLARE_API_TOKEN: "never-log-sentinel"};
const worker = {id: "d8be7a3ac48547e98a48e99f1eb145a1", name: "edgar-moe-forward-scheduler",
  subdomain: {enabled: false, previews_enabled: false}, logpush: false, observability: {enabled: false}, tail_consumers: []};
const secrets = ["GITHUB_TOKEN", "SCHEDULER_SIGNATURE_KEY"].map(name => ({name, type: "secret_text"}));
const bindings = [...secrets, ...["GITHUB_REPOSITORY", "GITHUB_WORKFLOW", "GITHUB_REF", "GITHUB_DEVICE"].map(name => ({name, type: "plain_text"}))];
async function upload(stage = "RESUME_PREPARE", overrides = {}) {
  const calls = [];
  const results = [worker, {schedules: stage === "STOP" ? [{cron: "17 7 * * 2-6"}] : []}, secrets, {bindings}, {}, {}];
  const result = await uploadPrivateWorker({stage, source: "export default {fetch(){}}", env,
    expectedAccountSha256: createHash("sha256").update(JSON.stringify(account)).digest("hex"),
    fetchImpl: async (url, init) => {
      calls.push({url, init});
      return new Response(JSON.stringify({success: true, result: overrides[calls.length] ?? results[calls.length - 1]}));
    }});
  return {result, calls};
}

test("inert upload uses only the existing Worker's API and strictly inherits both secrets", async () => {
  const {result, calls} = await upload();
  assert.equal(result.status, "uploaded_pending_state_verification");
  assert.equal(result.external_writes, 2);
  assert.equal(result.cron_requested, false);
  assert.equal(calls.length, 6);
  assert.equal(calls.some(call => /domains|routes|services/.test(call.url)), false);
  assert.match(calls[4].url, /edgar-moe-forward-scheduler\?bindings_inherit=strict$/);
  const metadata = JSON.parse(await calls[4].init.body.get("metadata").text());
  assert.deepEqual(metadata.bindings.filter(b => b.type === "inherit"), [
    {name: "GITHUB_TOKEN", type: "inherit", version_id: "latest"},
    {name: "SCHEDULER_SIGNATURE_KEY", type: "inherit", version_id: "latest"},
  ]);
  assert.equal(metadata.observability.enabled, false);
  assert.deepEqual(JSON.parse(calls[5].init.body), {schedules: []});
  assert.equal(JSON.stringify(result).includes(env.CLOUDFLARE_API_TOKEN), false);
});

test("unexpected bindings stop before any write", async () => {
  const {result, calls} = await upload("RESUME_PREPARE", {4: {bindings: [...bindings, {name: "unknown", type: "service"}]}});
  assert.equal(result.status, "refused");
  assert.equal(result.external_writes, 0);
  assert.equal(calls.length, 4);
});

test("activation changes only the exact original Cron and STOP never rewrites code", async () => {
  const {result, calls} = await upload("ACTIVATE");
  assert.equal(result.status, "uploaded_pending_state_verification");
  assert.deepEqual(JSON.parse(calls[5].init.body), {schedules: [{cron: "17 7 * * 2-6"}]});
  const stopped = await upload("STOP");
  assert.equal(stopped.result.code_uploaded, false);
  assert.equal(stopped.result.external_writes, 1);
  assert.deepEqual(JSON.parse(stopped.calls[3].init.body), {schedules: []});
});
