import { Ban, Check, Copy } from "lucide-react";
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
    // The outcome shows in the icon and its colour as well as being announced:
    // a copy that silently failed would leave a reader comparing against nothing.
    <button
      type="button"
      className={["icon-button copy-value", copied && "is-copied", failed && "is-blocked"].filter(Boolean).join(" ")}
      onClick={() => void copy()}
      aria-label={failed ? `Copying ${label} was blocked` : `Copy ${label}`}
      title={failed ? "Copying was blocked; select the text instead" : `Copy ${label}`}
    >
      {copied ? <Check size={16} aria-hidden="true" /> : failed ? <Ban size={16} aria-hidden="true" /> : <Copy size={16} aria-hidden="true" />}
      <span role="status" className="sr-only">
        {copied ? `${label} copied to the clipboard` : failed ? `Copying was blocked; select ${label} instead` : ""}
      </span>
    </button>
  );
}
