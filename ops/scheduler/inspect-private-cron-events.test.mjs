import assert from "node:assert/strict";
import {createHash} from "node:crypto";
import test from "node:test";
import {inspectPrivateCronEvents} from "./inspect-private-cron-events.mjs";

const account = "a".repeat(32);
const token = "sentinel-private-token";
const env = {CLOUDFLARE_ACCOUNT_ID: account, CLOUDFLARE_API_TOKEN: token};
const now = new Date("2026-11-09T08:00:00Z");
const event = {scriptName: "edgar-moe-forward-scheduler", cron: "17 7 * * TUE-SAT",
  datetime: "2026-11-07T07:17:01Z", scheduledDatetime: "2026-11-07T07:17:00Z", status: "exception"};
const document = events => ({data: {viewer: {accounts: [{accountTag: account, workersInvocationsScheduled: events}]}}});
async function inspect(body, override = {}) {
  const calls = [];
  const report = await inspectPrivateCronEvents({env, now,
    expectedAccountSha256: createHash("sha256").update(JSON.stringify(account)).digest("hex"),
    fetchImpl: async (url, init) => {
      calls.push({url, init});
      // Cloudflare's core Analytics schema uses the custom `string` scalar,
      // not the conventional GraphQL `String`. Model that provider boundary.
      if (/\$account:\s*String\b/.test(JSON.parse(init.body).query)) {
        return new Response(JSON.stringify({errors: [{message: "unknown type String"}]}));
      }
      return new Response(JSON.stringify(body));
    }, ...override});
  const serialized = JSON.stringify(report);
  assert.equal(serialized.includes(token), false);
  assert.equal(serialized.includes(account), false);
  assert.equal(report.external_writes, 0);
  assert.equal(report.worker_invocations, 0);
  assert.equal(report.forecast_margin_proven, false);
  assert.equal(report.dispatch_origin_proven, false);
  return {report, calls};
}

test("reads one fixed metadata query and reports actual events without implying forecast success", async () => {
  const {report, calls} = await inspect(document([event]));
  assert.equal(report.status, "passed");
  assert.deepEqual(report.events, [{observed_at: "2026-11-07T07:17:01.000Z", scheduled_at: "2026-11-07T07:17:00.000Z",
    expected_cron: true, outcome: "exception"}]);
  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, "https://api.cloudflare.com/client/v4/graphql");
  assert.equal(calls[0].init.method, "POST");
  assert.equal(calls[0].init.redirect, "error");
  const body = JSON.parse(calls[0].init.body);
  assert.deepEqual(body.variables, {account, from: "2026-11-07T00:00:00.000Z", to: now.toISOString()});
  assert.match(body.query, /workersInvocationsScheduled\(limit: 100/);
  assert.match(body.query, /scriptName: "edgar-moe-forward-scheduler"/);
  assert.doesNotMatch(body.query, /mutation|logs|secrets|source/);
});

test("empty and limited inventories preserve uncertainty rather than proving no invocation", async () => {
  for (const count of [0, 100]) {
    const {report} = await inspect(document(Array(count).fill(event)));
    assert.equal(report.status, "passed");
    assert.equal(report.event_absence_proves_no_invocation, false);
    assert.equal(report.inventory_may_be_truncated, count === 100);
  }
});

test("wrong resource and expired finite diagnostic scope refuse before network", async () => {
  for (const override of [
    {expectedAccountSha256: "0".repeat(64)}, {now: new Date("2026-11-06T23:59:59Z")},
    {now: new Date("2026-11-12T00:00:00Z")}, {now: new Date("invalid")},
  ]) {
    const {report, calls} = await inspect(document([]), override);
    assert.equal(report.status, "refused");
    assert.equal(calls.length, 0);
  }
});

test("provider refusal, invalid schema, resource drift and out-of-window rows fail closed", async () => {
  for (const body of [
    {errors: [{message: "sentinel-secret-provider-error"}], ...document([event])},
    {data: {viewer: {accounts: []}}},
    {data: {viewer: {accounts: [{accountTag: "b".repeat(32), workersInvocationsScheduled: []}]}}},
    document(null), document(Array(101).fill(event)),
    document([{...event, scriptName: "unrelated-worker"}]),
    document([{...event, datetime: "2026-11-10T00:00:00Z"}]),
    document([{...event, scheduledDatetime: "2026-11-06T07:17:00Z"}]),
    document([{...event, scheduledDatetime: "2026-11-07T07:17:00+00:00"}]),
    document([event, {...event, datetime: "private-secret"}]),
  ]) {
    const {report, calls} = await inspect(body);
    assert.equal(report.status, "refused");
    assert.deepEqual(report.events, []);
    assert.equal(calls.length, 1);
    assert.doesNotMatch(JSON.stringify(report), /sentinel|unrelated-worker|private-secret/);
  }
});

test("unknown provider fields and statuses cannot leak into retained receipt", async () => {
  const {report} = await inspect(document([{...event, cron: "private-cron", status: "private-status", extra: token}]));
  assert.equal(report.status, "passed");
  assert.equal(report.events[0].outcome, "unrecognized");
  assert.equal(report.events[0].expected_cron, false);
  assert.doesNotMatch(JSON.stringify(report), /private-cron|private-status/);
});

test("transport, auth, malformed and oversized bodies are never retried", async () => {
  for (const response of [new Error("private-error"), new Response("private-error", {status: 403}),
    new Response("not-json"), new Response("x".repeat(65537))]) {
    let calls = 0;
    const {report} = await inspect(null, {fetchImpl: async () => {
      calls++;
      if (response instanceof Error) throw response;
      return response;
    }});
    assert.equal(report.status, "refused");
    assert.equal(calls, 1);
    assert.equal(report.request_count, 1);
    assert.doesNotMatch(JSON.stringify(report), /private-error|not-json/);
  }
});

test("provider query refusals retain only bounded diagnostic categories without retries", async () => {
  for (const [message, expected] of [
    ["unknown type String", "query_contract_refused"],
    ["Cannot query field private-field on type private-type", "query_contract_refused"],
    ["not authorized for this account", "access_refused"],
    ["account does not have access to dataset", "access_refused"],
    ["cannot request data older than 3600s", "dataset_limit_refused"],
    ["query time range is too large", "dataset_limit_refused"],
    ["unable to execute query, please try again later", "service_unavailable"],
    ["private-unknown-error", "unknown"],
  ]) {
    const {report, calls} = await inspect({errors: [{message: message + " " + token, path: [token]}]});
    assert.equal(report.status, "refused");
    assert.equal(report.reason, "metadata_query_refused");
    assert.deepEqual(report.query_failure_categories, [expected]);
    assert.deepEqual(report.events, []);
    assert.equal(calls.length, 1);
    assert.doesNotMatch(JSON.stringify(report), /private-field|private-type|private-unknown-error|3600|try again/);
  }
});

test("mixed or malformed provider errors cannot create unbounded diagnostic output", async () => {
  for (const [errors, expected] of [
    [[{message: "unknown type String"}, {message: "forbidden"}, {message: "forbidden"}], ["access_refused", "query_contract_refused"]],
    [[{message: 123}], ["unknown"]],
    ["private-value", ["unknown"]],
    [Array(17).fill({message: token}), ["unknown"]],
    [[{message: "x".repeat(4096) + " forbidden"}], ["unknown"]],
  ]) {
    const {report, calls} = await inspect({errors});
    assert.equal(report.status, "refused");
    assert.deepEqual(report.query_failure_categories, expected);
    assert.equal(calls.length, 1);
    assert.equal(JSON.stringify(report).length < 1000, true);
  }
});
