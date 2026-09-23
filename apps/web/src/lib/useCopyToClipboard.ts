import { useEffect, useState } from "react";

/** How long a confirmation stays up before the control offers the copy again. */
export const COPY_CONFIRMATION_MS = 2_000;

export type CopyState = {
  /** False in an insecure context, where a copy control would not work. */
  supported: boolean;
  copied: boolean;
  failed: boolean;
  copy: () => Promise<void>;
};

/**
 * Copy text to the clipboard, reporting what actually happened. A refused
 * clipboard must never leave a reader believing a value was copied.
 */
export function useCopyToClipboard(value: string): CopyState {
  const [copied, setCopied] = useState(false);
  const [failed, setFailed] = useState(false);
  const supported = typeof navigator !== "undefined" && Boolean(navigator.clipboard?.writeText);

  useEffect(() => {
    if (!copied) return;
    const timer = window.setTimeout(() => setCopied(false), COPY_CONFIRMATION_MS);
    return () => window.clearTimeout(timer);
  }, [copied]);

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(value);
      setFailed(false);
      setCopied(true);
    } catch {
      setCopied(false);
      setFailed(true);
    }
  };

  return { supported, copied, failed, copy };
}
