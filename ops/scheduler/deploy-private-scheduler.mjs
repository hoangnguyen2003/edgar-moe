/** Capture useful pinned-CLI errors without exposing credential/account values. */
import {spawnSync} from "node:child_process";
import {mkdirSync, writeFileSync} from "node:fs";
import {dirname, resolve} from "node:path";
import {pathToFileURL} from "node:url";

export function redactDeploymentOutput(raw, env) {
  let text = String(raw);
  const values = Object.entries(env).filter(([name, value]) =>
    /TOKEN|SECRET|PASSWORD|ACCOUNT_ID/.test(name) && typeof value === "string" && value.length >= 8)
    .map(([, value]) => value).sort((a, b) => b.length - a.length);
  for (const value of values) text = text.split(value).join("[REDACTED]");
  return text.replace(/https?:\/\/[^\s<>"']+/g, "[URL]")
    .replace(/\b[a-f0-9]{32,}\b/gi, "[IDENTIFIER]")
    .replace(/\x1b\[[0-9;]*m/g, "").slice(-12000);
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  const output = process.argv[2];
  if (!output) throw new Error("Missing redacted deployment receipt path");
  const result = spawnSync("npx", ["--yes", "--package=wrangler@4.136.2", "wrangler",
    "deploy", "--config", "ops/scheduler/wrangler.toml"], {
    encoding: "utf8", env: {...process.env, WRANGLER_SEND_METRICS: "false"},
    timeout: 180000, maxBuffer: 2 * 1024 * 1024,
  });
  const text = redactDeploymentOutput(`${result.stdout ?? ""}\n${result.stderr ?? ""}`, process.env);
  const report = {status: result.status === 0 ? "succeeded" : "failed", exit_code: result.status,
    execution_error_code: result.error?.code ?? null, cli_version: "4.136.2",
    command: "deploy --config ops/scheduler/wrangler.toml", redacted_output: text};
  mkdirSync(dirname(output), {recursive: true, mode: 0o700});
  writeFileSync(output, JSON.stringify(report, null, 2) + "\n", {flag: "wx", mode: 0o600});
  console.log(JSON.stringify(report));
  if (result.status !== 0) process.exitCode = 1;
}
