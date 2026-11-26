const knownErrors = new Map([
  ["External delivery window refused", "window_refused"],
  ["External delivery credentials refused", "credentials_refused"],
  ["GitHub declined external delivery", "github_declined"],
  ["controller.noRetry is not a function", "no_retry_unavailable"],
]);

// The tail can contain logs and stack traces. Return only a fixed receipt.
export function summarizeScheduledEvent(data, { scheduled, ends, cron, worker }) {
  if (typeof data !== "string" || Buffer.byteLength(data) > 65_536) return undefined;
  let message;
  try {
    message = JSON.parse(data);
  } catch {
    return undefined;
  }
  if (message?.scriptName !== worker || message.event?.cron !== cron ||
      !Number.isSafeInteger(message.eventTimestamp) ||
      message.eventTimestamp < scheduled || message.eventTimestamp >= ends) {
    return undefined;
  }
  const exception = Array.isArray(message.exceptions) ? message.exceptions[0] : undefined;
  const githubStatus = typeof exception?.message === "string"
    ? /^GitHub declined external delivery \(HTTP ([1-5][0-9]{2})\)$/.exec(exception.message)
    : null;
  return {
    event_observed: true,
    scheduled_time_matches: message.event.scheduledTime === scheduled,
    delivery_delay_ms: message.eventTimestamp - scheduled,
    outcome: message.outcome === "ok" ? "ok" : "failed",
    error: githubStatus ? `github_http_${githubStatus[1]}` :
      exception ? knownErrors.get(exception.message) ?? "other" : null,
  };
}

export async function observeScheduledEvent({ url, scheduled, ends, cron, worker }) {
  if (typeof url !== "string" ||
      !/^wss:\/\/tail\.developers\.workers\.dev\/[a-f0-9]{32}$/.test(url)) {
    throw new Error("delivery_tail_url_refused");
  }
  const socket = new WebSocket(url, "trace-v1");
  let open = false;
  let timer;
  let connectTimer;
  try {
    return await new Promise((resolve, reject) => {
      connectTimer = setTimeout(() => reject(new Error("delivery_tail_connect_timeout")), 15_000);
      timer = setTimeout(() => resolve({ event_observed: false }), Math.max(0, ends - Date.now()));
      socket.addEventListener("open", () => {
        clearTimeout(connectTimer);
        open = true;
        socket.send(JSON.stringify({ debug: false }));
      }, { once: true });
      socket.addEventListener("message", ({ data }) => {
        const event = summarizeScheduledEvent(data, { scheduled, ends, cron, worker });
        if (event) resolve(event);
      });
      socket.addEventListener("error", () => reject(new Error("delivery_tail_connection_failed")),
        { once: true });
      socket.addEventListener("close", () => reject(new Error(
        open ? "delivery_tail_closed_early" : "delivery_tail_connection_failed",
      )), { once: true });
    });
  } finally {
    clearTimeout(timer);
    clearTimeout(connectTimer);
    socket.close();
  }
}
