// The home page's search (CONTRACT.md, "Find words in a project's pages and across projects"):
// the same matching rules as the server's find.py, for filtering the cards by name at once and for
// making the matched words bold in a snippet the server sent.

/**
 * Accents dropped, case ignored, white space collapsed and trimmed. JavaScript has no `casefold`;
 * `toLowerCase` agrees with it except for a few letters (ß stays ß here, "ss" on the server).
 */
export function fold(text: string): string {
  return text.normalize("NFKD").replace(/\p{Mn}/gu, "").toLowerCase().replace(/\s+/g, " ").trim();
}

/** Each character folded on its own, with the index in `text` it came from. */
function foldWithOrigin(text: string): { folded: string; origin: number[] } {
  let folded = "";
  const origin: number[] = [];
  let index = 0;
  for (const char of text) {
    const out = /\s/.test(char) ? " " : char.normalize("NFKD").replace(/\p{Mn}/gu, "").toLowerCase();
    for (let i = 0; i < out.length; i += 1) origin.push(index);
    folded += out;
    index += char.length;
  }
  return { folded, origin };
}

/**
 * The query's words and phrases, folded: text in double quotes (or “ ”) is one phrase, an unclosed
 * quote runs to the end, the rest is split into words. Empty when there is nothing to find (the
 * query without quotes is under two characters).
 */
export function queryTerms(raw: string): string[] {
  const text = raw.replace(/[“”]/g, '"');
  const parts = text.split('"');
  const terms: string[] = [];
  const rest: string[] = [];
  parts.forEach((part, index) => {
    if (index % 2) {
      const phrase = fold(part);
      if (phrase) terms.push(phrase);
    } else {
      rest.push(part);
    }
  });
  for (const word of fold(rest.join(" ")).split(" ")) if (word) terms.push(word);
  if (fold(text.replace(/"/g, " ")).length < 2) return [];
  return [...new Set(terms)];
}

/** Whether `text` holds every term. */
export function matchesAll(text: string, terms: string[]): boolean {
  if (!terms.length) return false;
  const folded = fold(text);
  return terms.every((term) => folded.includes(term));
}

export interface Segment {
  text: string;
  match: boolean;
}

/** `text` cut into plain and matched pieces, every occurrence of every term matched. */
export function highlight(text: string, terms: string[]): Segment[] {
  if (!text) return [];
  const { folded, origin } = foldWithOrigin(text);
  const marked: [number, number][] = [];
  for (const term of terms) {
    if (!term) continue;
    let at = folded.indexOf(term);
    while (at !== -1) {
      const start = origin[at];
      const lastOrigin = origin[at + term.length - 1];
      // The end of the original character the last folded character came from.
      const end = lastOrigin + (text.codePointAt(lastOrigin)! > 0xffff ? 2 : 1);
      marked.push([start, end]);
      at = folded.indexOf(term, at + term.length);
    }
  }
  if (!marked.length) return [{ text, match: false }];
  marked.sort((a, b) => a[0] - b[0]);
  const merged: [number, number][] = [];
  for (const range of marked) {
    const last = merged[merged.length - 1];
    if (last && range[0] <= last[1]) last[1] = Math.max(last[1], range[1]);
    else merged.push([...range]);
  }
  const out: Segment[] = [];
  let at = 0;
  for (const [start, end] of merged) {
    if (start > at) out.push({ text: text.slice(at, start), match: false });
    out.push({ text: text.slice(start, end), match: true });
    at = end;
  }
  if (at < text.length) out.push({ text: text.slice(at), match: false });
  return out;
}
