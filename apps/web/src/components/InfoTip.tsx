import { Info } from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";
import { GLOSSARY, type GlossaryKey } from "../lib/glossary";

/** Width the bubble may take; it opens leftwards when there is no room to the right. */
const BUBBLE_WIDTH = 300;

/**
 * A definition behind an info button. It opens on click or tap (never hover
 * alone), closes with Escape or an outside press, and its text is announced
 * when it appears.
 */
export function InfoTip({ term }: { term: GlossaryKey }) {
  const { term: name, definition } = GLOSSARY[term];
  const [open, setOpen] = useState(false);
  const [alignEnd, setAlignEnd] = useState(false);
  const id = useId();
  const rootRef = useRef<HTMLSpanElement>(null);
  const buttonRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!open) return;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      setOpen(false);
      buttonRef.current?.focus();
    };
    const closeOutside = (event: PointerEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("keydown", closeOnEscape);
    document.addEventListener("pointerdown", closeOutside);
    return () => {
      document.removeEventListener("keydown", closeOnEscape);
      document.removeEventListener("pointerdown", closeOutside);
    };
  }, [open]);

  const toggle = () => {
    const rect = buttonRef.current?.getBoundingClientRect();
    if (rect) setAlignEnd(rect.left + BUBBLE_WIDTH > window.innerWidth - 16);
    setOpen((value) => !value);
  };

  return (
    <span className="infotip" ref={rootRef}>
      <button
        ref={buttonRef}
        type="button"
        className="infotip__button"
        aria-label={`What is ${name}?`}
        aria-expanded={open}
        aria-controls={id}
        onClick={toggle}
      >
        <Info size={15} aria-hidden="true" />
      </button>
      <span id={id} role="status" className={alignEnd ? "infotip__region infotip__region--end" : "infotip__region"}>
        {open && <span className="infotip__bubble"><strong>{name}</strong>{definition}</span>}
      </span>
    </span>
  );
}
