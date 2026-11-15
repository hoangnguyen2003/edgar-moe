/** Optional local SDK test: requires an ALREADY installed Miniflare module path. */
import assert from "node:assert/strict";
import test from "node:test";
import { createHash } from "node:crypto";
import { mkdtempSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const localModule = process.env.OFFLINE_MINIFLARE_MODULE;
const directory = fileURLToPath(new URL(".", import.meta.url));
const id = number => createHash("sha256").update(`synthetic-${number}`).digest("hex");

test("actual local workerd SQLite/RPC contract, contention and eviction; outbound traffic blocked", {
  skip: !localModule && "Set OFFLINE_MINIFLARE_MODULE to an already-installed local SDK; never install/download automatically",
  timeout: 30000,
}, async t => {
  assert(localModule.startsWith("/") && localModule.endsWith("/index.js"));
  const { Miniflare, Log, LogLevel, convertV4MiniflareOptions } = await import(pathToFileURL(localModule).href);
  const root = mkdtempSync(join(tmpdir(), "edgar-offline-workerd-admission-"));
  let outbound = 0;
  const modules = ["workerd-fixture.mjs", "ledger.mjs", "sqlite-store.mjs", "executor.mjs"].map(name => ({
    type: "ESModule", path: join(directory, name), contents: readFileSync(join(directory, name), "utf8"),
  }));
  const mf = new Miniflare(convertV4MiniflareOptions({ cf: false, telemetry: { enabled: false },
    host: "127.0.0.1", port: 0, inspectorHost: "127.0.0.1", inspectorPort: 0,
    resourcePersistencePath: join(root, "local-persistence"),
    isolatedResourcePersistencePath: join(root, "local-isolated"), resourceTmpPath: join(root, "local-tmp"),
    log: new Log(LogLevel.NONE),
    workers: [{ name: "offline-fixture", modules, modulesRoot: directory, rootPath: root,
      compatibilityDate: "2026-09-21",
      outboundService: () => { outbound++; throw new Error("Outbound networking prohibited"); },
      durableObjects: { LOCAL_FIXTURE: { className: "FixtureAdmission", useSQLite: true } },
    }],
  }));
  t.after(() => mf.dispose());
  async function run(number, route = "status", authentication = "synthetic-emulator-only") {
    // dispatchFetch targets local workerd; it is not an Internet fetch.
    const response = await mf.dispatchFetch("http://offline.invalid/synthetic", { method: "POST",
      headers: { Authorization: authentication, "Content-Type": "application/json" },
      body: JSON.stringify({ requestId: id(number), route }) });
    assert.equal(response.status, 200);
    return response.json();
  }
  assert.equal((await run(1, "status", "wrong")).reason, "unauthorized");
  const first = await run(1);
  assert.equal(first.status, "completed");
  assert.equal(first.totalRemaining, 359);
  const concurrent = await Promise.all(Array.from({ length: 8 }, (_, index) => run(index + 2)));
  assert.equal(concurrent.filter(value => value.status === "completed").length, 1);
  assert.equal(concurrent.filter(value => value.reason === "completion_unconfirmed").length, 7);
  assert.equal((await run(1)).reason, "request_replayed");
  const object = { name: "a".repeat(64) };
  await mf.unsafeEvictDurableObject("offline-fixture", "FixtureAdmission", object);
  const next = await run(10);
  assert.equal(next.status, "completed"); assert.equal(next.totalRemaining, 357);
  assert.equal((await run(11, "performance")).reason, "closure_unconfirmed");
  await mf.unsafeEvictDurableObject("offline-fixture", "FixtureAdmission", object);
  assert.equal((await run(12)).reason, "pilot_stopped");
  assert.equal(outbound, 0);
});
