/**
 * Optional Cloudflare Workers Cron adapter for the prospective forward cycle.
 *
 * This module only dispatches the existing GitHub Actions workflow. It never
 * receives market, registry, R2, model, or provider credentials. The adapter
 * is intentionally dormant until an operator deploys it and removes the
 * best-effort GitHub schedule in a separate reviewed change.
 */

const GITHUB_API_BASE = "https://api.github.com";
const GITHUB_API_VERSION = "2022-11-28";
const DEFAULT_WORKFLOW = "forward-production.yml";
const DEFAULT_REF = "main";
const DEFAULT_DEVICE = "cpu";
const REQUEST_TIMEOUT_MS = 10_000;
const ACTIVE_STATUSES = new Set(["queued", "in_progress", "pending", "waiting", "requested"]);
const REPOSITORY_PATTERN = /^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/;
const WORKFLOW_PATTERN = /^[A-Za-z0-9_.-]+$/;
const REF_PATTERN = /^[A-Za-z0-9_.\/-]+$/;
const DEVICES = new Set(["cpu"]);

export default {
  async scheduled(controller, env, context) {
    context.waitUntil(runScheduledDispatch(env, controller.cron));
  },

  // There is deliberately no public trigger endpoint. A Cron invocation is
  // the only supported entry point; GitHub credentials never cross HTTP.
  async fetch() {
    return new Response("Not found", { status: 404 });
  },
};

/**
 * Dispatch one workflow run unless the same workflow already has an active
 * run on the configured ref. The active-run check is a duplicate guard, not a
 * distributed lock; GitHub Actions concurrency remains the final backstop.
 */
export async function runScheduledDispatch(env, cron = null) {
  const config = validateSchedulerConfig(env);
  const activeRun = await findActiveRun(config);
  if (activeRun !== null) {
    const result = {
      status: "skipped",
      reason: "active_run",
      run_id: activeRun.id,
      cron,
    };
    console.log(JSON.stringify(result));
    return result;
  }

  await dispatchWorkflow(config);
  const result = {
    status: "dispatched",
    repository: config.repository,
    workflow: config.workflow,
    ref: config.ref,
    device: config.device,
    cron,
  };
  console.log(JSON.stringify(result));
  return result;
}

/** @typedef {{ token: string, repository: string, workflow: string, ref: string, device: string }} SchedulerConfig */

/**
 * Validate only non-secret scheduler configuration and return the token for
 * the internal request boundary. No error message includes secret material.
 *
 * @param {Record<string, string | undefined>} env
 * @returns {SchedulerConfig}
 */
export function validateSchedulerConfig(env) {
  const token = requiredValue(env.GITHUB_TOKEN, "GITHUB_TOKEN");
  const repository = requiredValue(env.GITHUB_REPOSITORY, "GITHUB_REPOSITORY");
  const workflow = env.GITHUB_WORKFLOW?.trim() || DEFAULT_WORKFLOW;
  const ref = env.GITHUB_REF?.trim() || DEFAULT_REF;
  const device = env.GITHUB_DEVICE?.trim() || DEFAULT_DEVICE;

  if (!REPOSITORY_PATTERN.test(repository)) {
    throw new Error("GITHUB_REPOSITORY must be owner/repository");
  }
  if (!WORKFLOW_PATTERN.test(workflow)) {
    throw new Error("GITHUB_WORKFLOW must be a workflow filename");
  }
  if (!REF_PATTERN.test(ref) || ref.includes("..")) {
    throw new Error("GITHUB_REF contains unsupported characters");
  }
  if (!DEVICES.has(device)) {
    throw new Error("GITHUB_DEVICE must be cpu");
  }

  return { token, repository, workflow, ref, device };
}

function requiredValue(value, name) {
  if (typeof value !== "string" || !value.trim()) {
    throw new Error(`${name} is required`);
  }
  return value.trim();
}

async function findActiveRun(config) {
  const url = new URL(
    `/repos/${config.repository}/actions/workflows/${encodeURIComponent(config.workflow)}/runs`,
    GITHUB_API_BASE,
  );
  url.searchParams.set("branch", config.ref);
  url.searchParams.set("per_page", "20");
  const response = await githubRequest(url, { method: "GET" }, config.token);
  if (!response.ok) {
    throw new Error(`GitHub workflow lookup returned HTTP ${response.status}`);
  }
  let payload;
  try {
    payload = await response.json();
  } catch {
    throw new Error("GitHub workflow lookup returned invalid JSON");
  }
  if (!payload || !Array.isArray(payload.workflow_runs)) {
    throw new Error("GitHub workflow lookup returned an invalid payload");
  }
  const active = payload.workflow_runs.find(
    (run) => run && ACTIVE_STATUSES.has(run.status) && Number.isInteger(run.id),
  );
  return active ? { id: active.id, status: active.status } : null;
}

async function dispatchWorkflow(config) {
  const url = new URL(
    `/repos/${config.repository}/actions/workflows/${encodeURIComponent(config.workflow)}/dispatches`,
    GITHUB_API_BASE,
  );
  const response = await githubRequest(
    url,
    {
      method: "POST",
      body: JSON.stringify(buildDispatchPayload(config)),
    },
    config.token,
  );
  if (response.status !== 204) {
    throw new Error(`GitHub workflow dispatch returned HTTP ${response.status}`);
  }
}

/** @param {Pick<SchedulerConfig, "ref" | "device">} config */
export function buildDispatchPayload(config) {
  return {
    ref: config.ref,
    inputs: { device: config.device },
  };
}

async function githubRequest(url, init, token) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
  try {
    return await fetch(url, {
      ...init,
      signal: controller.signal,
      headers: {
        Accept: "application/vnd.github+json",
        Authorization: `Bearer ${token}`,
        "Content-Type": "application/json",
        "User-Agent": "edgar-moe-cloudflare-scheduler",
        "X-GitHub-Api-Version": GITHUB_API_VERSION,
      },
    });
  } catch (error) {
    if (error?.name === "AbortError") {
      throw new Error("GitHub API request timed out");
    }
    throw new Error("GitHub API request failed");
  } finally {
    clearTimeout(timeout);
  }
}
