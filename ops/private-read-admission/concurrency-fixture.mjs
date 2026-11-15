/** Child-process synthetic contention fixture; never used as a service entry point. */
import { openFixture } from "./sqlite-fixture.mjs";

if (process.send) {
  process.once("message", message => {
    let fixture;
    let result;
    try {
      fixture = openFixture(message.filename, { create: false, policy: message.policy });
      result = fixture.ledger.admit(message.requestId, message.nowMs);
    } catch { result = { status: "refused", reason: "fixture_unavailable" }; }
    finally { fixture?.close(); }
    // Flush the synthetic receipt before closing IPC and allowing process exit.
    process.send(result, error => {
      if (error) process.exitCode = 1;
      if (process.connected) process.disconnect();
    });
  });
}
