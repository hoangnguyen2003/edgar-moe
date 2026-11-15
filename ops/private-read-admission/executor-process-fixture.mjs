/** SYNTHETIC LOCAL PROCESS FIXTURE ONLY: no provider, network, or real authentication. */
import { OfflineAdmissionExecutor } from "./executor.mjs";
import { openFixture } from "./sqlite-fixture.mjs";

let fixture;
let executor;
let resolveResult;
let resolveClosed;

process.on("message", message => {
  if (message.type === "open") {
    try {
      fixture = openFixture(message.filename, { create: false, policy: message.policy });
      executor = new OfflineAdmissionExecutor({ ledger: fixture.ledger,
        authorize: value => value === "synthetic-local-only", clock: () => message.nowMs,
        startRead: () => {
          const result = new Promise(resolve => { resolveResult = resolve; });
          const closed = new Promise(resolve => { resolveClosed = resolve; });
          process.send({ type: "started" });
          return { result, closed };
        },
      });
      process.send({ type: "opened" });
    } catch { process.send({ type: "open_failed" }); }
  } else if (message.type === "run") {
    executor.run(message.input, "synthetic-local-only")
      .then(outcome => process.send({ type: "outcome", outcome }));
  } else if (message.type === "result") {
    resolveResult?.(true);
    process.send({ type: "result_settled" });
  } else if (message.type === "closed") {
    resolveClosed?.(true);
  } else if (message.type === "exit") {
    fixture?.close();
    process.exit(0);
  }
});
