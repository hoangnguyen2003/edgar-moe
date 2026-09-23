import { Info } from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";
import { GLOSSARY, type GlossaryKey } from "../lib/glossary";

/** Width the bubble may take. It opens from the icon and slides left only as far as it must to stay on screen. */
const BUBBLE_WIDTH = 300;
const SCREEN_MARGIN = 16;

/**
 * A label followed by its definition button. The last word and the button
 * wrap together, so a narrow column never strands the icon on its own line.
 */
export function InfoLabel({ text, term }: { text: string; term?: GlossaryKey }) {
  if (!term) return <>{text}</>;
  const cut = text.lastIndexOf(" ") + 1;
  return (
    <>
      {text.slice(0, cut)}
      <span className="infotip-anchor">{text.slice(cut)}<InfoTip term={term} /></span>
    </>
  );
}

/**
 * A definition behind an info button. It opens on click or tap (never hover
 * alone), closes with Escape or an outside press, and its text is announced
 * when it appears.
 */
export function InfoTip({ term }: { term: GlossaryKey }) {
  const { term: name, definition } = GLOSSARY[term];
  const [open, setOpen] = useState(false);
  const [offset, setOffset] = useState(-10);
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
    const root = rootRef.current?.getBoundingClientRect();
    if (root) {
      const width = Math.min(BUBBLE_WIDTH, window.innerWidth - 2 * SCREEN_MARGIN);
      const left = Math.max(SCREEN_MARGIN, Math.min(root.left - 10, window.innerWidth - SCREEN_MARGIN - width));
      setOffset(left - root.left);
    }
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
      <span id={id} role="status" className="infotip__region" style={{ left: offset }}>
        {open && <span className="infotip__bubble"><strong>{name}</strong>{definition}</span>}
      </span>
    </span>
  );
}
