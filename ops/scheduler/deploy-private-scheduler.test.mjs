import assert from "node:assert/strict";
import test from "node:test";
import {redactDeploymentOutput} from "./deploy-private-scheduler.mjs";

test("CLI diagnostics preserve error causes while withholding tokens, accounts and URLs", () => {
  const env = {CLOUDFLARE_API_TOKEN: "sentinel-private-api-token", CLOUDFLARE_ACCOUNT_ID: "a".repeat(32), ACTIONS_RUNTIME_TOKEN: "runtime-token-sentinel"};
  const text = redactDeploymentOutput(`ERROR: invalid export [code: 10021]\n${env.CLOUDFLARE_API_TOKEN}\n${env.CLOUDFLARE_ACCOUNT_ID}\n${env.ACTIONS_RUNTIME_TOKEN}\nhttps://api.invalid/private\n`, env);
  for (const value of Object.values(env)) assert.equal(text.includes(value), false);
  assert.equal(text.includes("api.invalid"), false);
  assert.equal(text.includes("invalid export [code: 10021]"), true);
});

test("large diagnostics are bounded after redaction", () => {
  const text = redactDeploymentOutput("x".repeat(20000) + "\nsecret-at-end", {GITHUB_TOKEN: "secret-at-end"});
  assert.equal(text.length, 12000);
  assert.equal(text.includes("secret-at-end"), false);
});
