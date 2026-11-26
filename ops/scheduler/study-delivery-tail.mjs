const MAX_FRAME_BYTES = 65_536;
const knownErrors = new Map([
  ["External delivery window refused", "window_refused"],
  ["External delivery credentials refused", "credentials_refused"],
  ["GitHub declined external delivery", "github_declined"],
  ["controller.noRetry is not a function", "no_retry_unavailable"],
]);

// The tail can contain logs and stack traces. Return only a fixed receipt.
export function summarizeScheduledEvent(data, { scheduled, ends, cron, worker }) {
  const text = data instanceof ArrayBuffer && data.byteLength <= MAX_FRAME_BYTES
    ? Buffer.from(data).toString("utf8") : data;
  if (typeof text !== "string" || Buffer.byteLength(text) > MAX_FRAME_BYTES) return undefined;
  let message;
  try {
    message = JSON.parse(text);
  } catch {
    return undefined;
  }
  if (!message || (message.scriptName != null && message.scriptName !== worker) ||
      message.event?.cron !== cron ||
      !Number.isSafeInteger(message.eventTimestamp) ||
      message.eventTimestamp < scheduled || message.eventTimestamp >= ends) {
    return undefined;
  }
  const exception = Array.isArray(message.exceptions) ? message.exceptions[0] : undefined;
  const githubStatus = typeof exception?.message === "string"
    ? /^GitHub declined external delivery \(HTTP ([1-5][0-9]{2})\)$/.exec(exception.message)
    : null;
  const reported = message.event.scheduledTime;
  const offset = Number.isFinite(reported) ? Math.floor(reported - scheduled) : null;
  const logs = Array.isArray(message.logs) ? message.logs : [];
  const dispatch = logs.find((entry) => Array.isArray(entry?.message) &&
    ["study_delivery_dispatched", "study_delivery_skipped"].includes(entry.message[0]));
  return {
    event_observed: true,
    scheduled_time_matches: reported === scheduled,
    reported_offset_ms: offset !== null && Math.abs(offset) < 3_600_000 ? offset : null,
    dispatch_status: dispatch ? dispatch.message[0].slice("study_delivery_".length) : null,
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
  socket.binaryType = "arraybuffer";
  let open = false;
  let framesSeen = 0;
  let timer;
  let connectTimer;
  try {
    return await new Promise((resolve, reject) => {
      connectTimer = setTimeout(() => reject(new Error("delivery_tail_connect_timeout")), 15_000);
      timer = setTimeout(() => resolve({ event_observed: false, frames_seen: framesSeen }), Math.max(0, ends - Date.now()));
      socket.addEventListener("open", () => {
        clearTimeout(connectTimer);
        open = true;
        socket.send(JSON.stringify({ debug: false }));
      }, { once: true });
      socket.addEventListener("message", ({ data }) => {
        framesSeen += 1;
        const event = summarizeScheduledEvent(data, { scheduled, ends, cron, worker });
        if (event) resolve({ ...event, frames_seen: framesSeen });
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
