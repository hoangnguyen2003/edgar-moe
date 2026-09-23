import { Check, Copy } from "lucide-react";
import { useCopyToClipboard } from "../lib/useCopyToClipboard";

/**
 * A command block with a copy button. The command is meant to be run, and on a
 * phone it sits in a box that scrolls sideways, where selecting it by hand is
 * the worst part of trying the project out.
 *
 * The button appears only where the clipboard is available: it is absent in an
 * insecure context rather than present and broken.
 */
export function CopyCommand({ command, label }: { command: string; label: string }) {
  const { supported, copied, failed, copy } = useCopyToClipboard(command);

  return (
    <div className="command">
      <pre>
        <code>{command}</code>
      </pre>
      {supported && (
        <div className="command__actions">
          <button type="button" className="button button--secondary button--small" onClick={() => void copy()}>
            {copied ? <Check size={14} aria-hidden="true" /> : <Copy size={14} aria-hidden="true" />}
            {copied ? "Copied" : `Copy ${label}`}
          </button>
          <span role="status" className="sr-only">
            {copied ? `${label} copied to the clipboard` : ""}
          </span>
          {failed && <span className="command__failed">Copying was blocked; select the text instead.</span>}
        </div>
      )}
    </div>
  );
}
