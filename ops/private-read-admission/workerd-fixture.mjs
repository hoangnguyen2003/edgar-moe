/** SYNTHETIC EMULATOR FIXTURE ONLY. No cloud configuration or real reader/auth. */
import { DurableObject } from "cloudflare:workers";
import { AdmissionLedger, LIMITS } from "./ledger.mjs";
import { SqliteAdmissionStore } from "./sqlite-store.mjs";
import { OfflineAdmissionExecutor, createOfflineDurableAdmissionClass } from "./executor.mjs";

const startsAtMs = Date.parse("2026-10-06T00:00:00Z");
const policy = Object.freeze({ pilotId: "a".repeat(64), targetSha256: "b".repeat(64),
  startsAtMs, expiresAtMs: startsAtMs + LIMITS.lifetimeMs });
const Candidate = createOfflineDurableAdmissionClass(DurableObject, ctx => {
  const store = new SqliteAdmissionStore(ctx.storage);
  // Test setup only: NOT part of the candidate's callable surface.
  store.prepareOfflineFixture();
  const ledger = new AdmissionLedger(store, policy);
  const initialized = ledger.initializeOfflineFixture();
  if (initialized.status !== "initialized" && initialized.reason !== "already_initialized") {
    throw new Error("Synthetic fixture unavailable");
  }
  return new OfflineAdmissionExecutor({ ledger, clock: () => startsAtMs,
    authorize: async authentication => authentication === "synthetic-emulator-only",
    startRead: ({ route }) => ({
      result: new Promise(resolve => setTimeout(() => resolve(true), 100)),
      closed: Promise.resolve(route !== "performance"),
    }),
  });
});
export class FixtureAdmission extends Candidate {}

export default {
  async fetch(request, env) {
    const object = env.LOCAL_FIXTURE.getByName(policy.pilotId);
    const input = await request.json();
    return Response.json(await object.runRead(input, request.headers.get("Authorization")));
  },
};
