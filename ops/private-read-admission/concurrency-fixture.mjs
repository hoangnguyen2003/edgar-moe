/** Child-process synthetic contention fixture; never used as a service entry point. */
import { openFixture } from "./sqlite-fixture.mjs";

if (process.send) {
  process.once("message", message => {
    let fixture;
    try {
      fixture = openFixture(message.filename, { create: false, policy: message.policy });
      const result = fixture.ledger.admit(message.requestId, message.nowMs);
      process.send(result);
    } catch { process.send({ status: "refused", reason: "fixture_unavailable" }); }
    finally { fixture?.close(); process.disconnect(); }
  });
}
