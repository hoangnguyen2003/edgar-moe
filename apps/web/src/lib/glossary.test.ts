import { describe, expect, it } from "vitest";
import { GLOSSARY, GLOSSARY_ORDER } from "./glossary";

describe("glossary", () => {
  it("shows every term it defines", () => {
    // A term defined here but missing from the order simply never appears on
    // How it works, and nothing else would report that.
    expect([...GLOSSARY_ORDER].sort()).toEqual(Object.keys(GLOSSARY).sort());
  });

  it("lists each term once", () => {
    expect(new Set(GLOSSARY_ORDER).size).toBe(GLOSSARY_ORDER.length);
  });

  it("gives every term a name and a definition a reader can use", () => {
    for (const [key, entry] of Object.entries(GLOSSARY)) {
      expect(entry.term, key).toBeTruthy();
      expect(entry.definition.length, key).toBeGreaterThan(40);
      expect(entry.definition.trim().endsWith("."), `${key} definition ends mid-sentence`).toBe(true);
    }
  });
});
