import { describe, expect, it } from "vitest";
import { countLines, describePaste, insertText, isLongPaste, isPastedText, pastedName, pasteSize, PASTE_CHARS, PASTE_LINES } from "./paste";

describe("long pasted text", () => {
  it("counts lines whatever the line ending, ignoring the empty lines at the end", () => {
    expect(countLines("")).toBe(0);
    expect(countLines("one")).toBe(1);
    expect(countLines("one\ntwo\n")).toBe(2);
    expect(countLines("one\r\ntwo\rthree\n\n\n")).toBe(3);
  });

  it("is long from 2,000 characters or 40 lines", () => {
    expect(isLongPaste("a".repeat(PASTE_CHARS - 1))).toBe(false);
    expect(isLongPaste("a".repeat(PASTE_CHARS))).toBe(true);
    expect(isLongPaste(Array(PASTE_LINES - 1).fill("x").join("\n"))).toBe(false);
    expect(isLongPaste(Array(PASTE_LINES).fill("x").join("\n"))).toBe(true);
    expect(isLongPaste("a short note")).toBe(false);
  });

  it("names the file by the time, and tells two in the same second apart", () => {
    const at = new Date(2026, 10, 24, 15, 30, 12);
    expect(pastedName(at)).toBe("pasted-text-20261124-153012.txt");
    expect(pastedName(at, 1)).toBe("pasted-text-20261124-153012-2.txt");
    expect(pastedName(new Date(2026, 0, 5, 3, 4, 5))).toBe("pasted-text-20260105-030405.txt");
  });

  it("recognises its own files by name, in any folder", () => {
    expect(isPastedText("attachments/pasted-text-20261124-153012.txt")).toBe(true);
    expect(isPastedText("pasted-text-20261124-153012-2.txt")).toBe(true);
    expect(isPastedText("attachments/pasted-20261124-153012.png")).toBe(false);
    expect(isPastedText("attachments/my-pasted-text-20261124-153012.txt")).toBe(false);
    expect(isPastedText("attachments/notes.txt")).toBe(false);
  });

  it("says how big it is", () => {
    expect(pasteSize(4812, 120)).toBe("4,812 characters · 120 lines");
    expect(pasteSize(1, 1)).toBe("1 character · 1 line");
    const pasted = describePaste("a\nb\n");
    expect(pasted).toEqual({ text: "a\nb\n", chars: 4, lines: 2, kept: false });
  });

  it("puts the text into the message at the cursor, over a selection", () => {
    expect(insertText("Look at  please", "THIS", 8, 8)).toEqual({ draft: "Look at THIS please", caret: 12 });
    expect(insertText("replace me", "X", 0, 7)).toEqual({ draft: "X me", caret: 1 });
    expect(insertText("end", " more", 99, 99)).toEqual({ draft: "end more", caret: 8 });
    expect(insertText("", "all", 0, 0)).toEqual({ draft: "all", caret: 3 });
  });
});
