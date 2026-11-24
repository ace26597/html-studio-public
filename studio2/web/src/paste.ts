// Long pasted text becomes an attachment, not a wall of text in the message box (CONTRACT.md,
// "Long pasted text becomes an attachment"). Pure parts.

export const PASTE_CHARS = 2000;
export const PASTE_LINES = 40;

export interface PastedText {
  text: string;
  chars: number;
  lines: number;
  /** Also saved in the project's top folder ("Keep in the project"). */
  kept: boolean;
}

export function countLines(text: string): number {
  if (!text) return 0;
  const body = text.replace(/\r\n?/g, "\n").replace(/\n+$/, "");
  return body ? body.split("\n").length : 1;
}

/** Long enough to be kept as a file. */
export function isLongPaste(text: string): boolean {
  return text.length >= PASTE_CHARS || countLines(text) >= PASTE_LINES;
}

export function describePaste(text: string): PastedText {
  return { text, chars: text.length, lines: countLines(text), kept: false };
}

const two = (value: number) => String(value).padStart(2, "0");

/** `pasted-text-20261124-153012.txt`; `index` tells apart two pastes within the same second. */
export function pastedName(now: Date, index = 0): string {
  const day = `${now.getFullYear()}${two(now.getMonth() + 1)}${two(now.getDate())}`;
  const time = `${two(now.getHours())}${two(now.getMinutes())}${two(now.getSeconds())}`;
  return `pasted-text-${day}-${time}${index ? `-${index + 1}` : ""}.txt`;
}

/** A file this studio made from a paste (by its name, wherever it is kept). */
export function isPastedText(path: string): boolean {
  return /(^|\/)pasted-text-\d{8}-\d{6}(-\d+)?\.txt$/.test(path);
}

/** "4,812 characters · 120 lines" */
export function pasteSize(chars: number, lines: number): string {
  const count = chars.toLocaleString("en-US");
  return `${count} character${chars === 1 ? "" : "s"} · ${lines.toLocaleString("en-US")} line${lines === 1 ? "" : "s"}`;
}

/** The pasted text written into the message at the cursor; `caret` is where the cursor goes. */
export function insertText(draft: string, text: string, start: number, end: number): { draft: string; caret: number } {
  const from = Math.max(0, Math.min(start, draft.length));
  const to = Math.max(from, Math.min(end, draft.length));
  return { draft: draft.slice(0, from) + text + draft.slice(to), caret: from + text.length };
}
