import { Check, Copy } from "lucide-react";
import { useCopyToClipboard } from "../lib/useCopyToClipboard";

/**
 * An icon-only copy control for a value a reader is meant to check, such as a
 * 64-character fingerprint. Comparing one by hand, on a phone, is the reason
 * this page is hard to actually use.
 */
export function CopyValue({ value, label }: { value: string; label: string }) {
  const { supported, copied, failed, copy } = useCopyToClipboard(value);
  if (!supported) return null;
  return (
    <button
      type="button"
      className="icon-button"
      onClick={() => void copy()}
      aria-label={failed ? `Copying ${label} was blocked` : `Copy ${label}`}
      title={failed ? "Copying was blocked" : `Copy ${label}`}
    >
      {copied ? <Check size={16} aria-hidden="true" /> : <Copy size={16} aria-hidden="true" />}
      <span role="status" className="sr-only">
        {copied ? `${label} copied to the clipboard` : ""}
      </span>
    </button>
  );
}
