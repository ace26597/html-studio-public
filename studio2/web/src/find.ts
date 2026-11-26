// Find: the matching rules, the chat search and the messages from the page (CONTRACT.md,
// "Find on the page and in the chat"). No model calls: plain substring matching.
//
// The page helper (`static/bridge.js`) has a copy of fold / parseQuery / matchRanges / rank
// between the markers "find: matching" and "find: end of matching"; `find.test.ts` runs the
// same cases on both, so the two sides cannot drift apart.

import type { ThreadEntry } from "./thread";

/** Most rows a group lists; the page says when it had more. */
export const FIND_CAP = 200;

/** Text folded for matching (accents dropped, lower case, white space collapsed), with the
 * index in the original text of every folded character. */
export interface Folded {
  text: string;
  map: number[];
}

export function fold(source: string): Folded {
  let text = "";
  const map: number[] = [];
  let space = true; // drops leading white space
  for (let i = 0; i < source.length; i++) {
    const ch = source[i];
    if (/\s/.test(ch)) {
      if (!space) {
        text += " ";
        map.push(i);
        space = true;
      }
      continue;
    }
    space = false;
    const parts = ch.normalize("NFKD").replace(/\p{Mn}/gu, "").toLowerCase();
    for (let j = 0; j < parts.length; j++) {
      text += parts[j];
      map.push(i);
    }
  }
  if (text.endsWith(" ")) {
    text = text.slice(0, -1);
    map.pop();
  }
  return { text, map };
}

export interface Query {
  /** Every word and every quoted phrase, folded. A piece matches when it holds all of them. */
  terms: string[];
  /** The whole query, quotes removed, folded: a piece holding it as one run ranks higher. */
  whole: string;
}

/** Words, and "exact phrases" in straight or curly double quotes (an unclosed quote runs to the end).
 * Null when there is nothing to look for (under two characters). */
export function parseQuery(raw: string): Query | null {
  const source = String(raw ?? "").replace(/[“”]/g, '"');
  const terms: string[] = [];
  const add = (part: string) => {
    const folded = fold(part).text;
    if (folded && terms.indexOf(folded) === -1) terms.push(folded);
  };
  const pieces = source.split('"');
  for (let i = 0; i < pieces.length; i++) {
    if (i % 2 === 1) add(pieces[i]);
    else pieces[i].split(/\s+/).forEach(add);
  }
  const whole = fold(source.replace(/"/g, " ")).text;
  if (whole.length < 2 || !terms.length) return null;
  return { terms, whole };
}

/** Where the terms are in `text`, as [start, end) in the original text, in order and merged;
 * null when the text does not hold every term. */
export function matchRanges(text: string, query: Query | null): Array<[number, number]> | null {
  if (!query) return null;
  const folded = fold(text);
  const found: Array<[number, number]> = [];
  for (const term of query.terms) {
    let at = folded.text.indexOf(term);
    if (at === -1) return null;
    while (at !== -1) {
      const end = at + term.length - 1;
      found.push([folded.map[at], folded.map[end] + 1]);
      at = folded.text.indexOf(term, at + term.length);
    }
  }
  found.sort((a, b) => a[0] - b[0] || a[1] - b[1]);
  const merged: Array<[number, number]> = [];
  for (const range of found) {
    const last = merged[merged.length - 1];
    if (last && range[0] <= last[1]) last[1] = Math.max(last[1], range[1]);
    else merged.push([range[0], range[1]]);
  }
  return merged;
}

/** The order rows are listed in: headings, then pieces holding the whole query as one run, then
 * the rest, each in page order. Returns the indexes of `items` in that order. */
export function rank(items: ReadonlyArray<{ text: string; heading: boolean }>, query: Query): number[] {
  const tier = (item: { text: string; heading: boolean }) =>
    item.heading ? 0 : fold(item.text).text.indexOf(query.whole) !== -1 ? 1 : 2;
  return items
    .map((item, index) => ({ index, tier: tier(item) }))
    .sort((a, b) => a.tier - b.tier || a.index - b.index)
    .map((entry) => entry.index);
}

/** A short piece of `text` round its first match, with an ellipsis where it was cut. */
export function snippetAround(text: string, query: Query | null, before = 50, length = 160): string {
  const flat = text.replace(/\s+/g, " ").trim();
  const ranges = matchRanges(flat, query);
  const at = ranges && ranges.length ? ranges[0][0] : 0;
  let start = Math.max(0, at - before);
  if (start > 0) {
    const space = flat.indexOf(" ", start);
    if (space !== -1 && space < at) start = space + 1;
  }
  let end = Math.min(flat.length, start + length);
  if (end < flat.length) {
    const space = flat.lastIndexOf(" ", end);
    if (space > at) end = space;
  }
  return (start > 0 ? "…" : "") + flat.slice(start, end) + (end < flat.length ? "…" : "");
}

/** `text` cut into plain and matched parts, for drawing the matched words bold. */
export function markParts(text: string, query: Query | null): Array<{ text: string; hit: boolean }> {
  const ranges = matchRanges(text, query);
  if (!ranges) {
    // Not every term is in a cut snippet: still bold the ones that are.
    if (!query) return [{ text, hit: false }];
    const some = query.terms
      .map((term) => matchRanges(text, { terms: [term], whole: term }))
      .filter((found): found is Array<[number, number]> => found !== null)
      .flat()
      .sort((a, b) => a[0] - b[0]);
    return split(text, merge(some));
  }
  return split(text, ranges);
}

function merge(ranges: Array<[number, number]>): Array<[number, number]> {
  const out: Array<[number, number]> = [];
  for (const range of ranges) {
    const last = out[out.length - 1];
    if (last && range[0] <= last[1]) last[1] = Math.max(last[1], range[1]);
    else out.push([range[0], range[1]]);
  }
  return out;
}

function split(text: string, ranges: Array<[number, number]>) {
  const parts: Array<{ text: string; hit: boolean }> = [];
  let at = 0;
  for (const [start, end] of ranges) {
    if (start > at) parts.push({ text: text.slice(at, start), hit: false });
    parts.push({ text: text.slice(start, end), hit: true });
    at = end;
  }
  if (at < text.length) parts.push({ text: text.slice(at), hit: false });
  return parts;
}

// ---- the panel's groups (a seam: more groups can be added without changing the panel) ----

export type FindKind = "heading" | "text" | "you" | "agent";

export interface FindRow {
  key: string;
  /** The heading the match is under, when there is one. */
  section: string | null;
  snippet: string;
  kind: FindKind;
  /** Indent of an outline row (heading level 1–6). */
  level?: number;
  /** The match is in a part of the page that is not shown (a hidden tab, `display:none`). */
  hidden?: boolean;
  onGo: () => void;
}

export interface FindGroup {
  id: string;
  title: string;
  rows: FindRow[];
  /** Said under the title: more matches than listed, the page did not answer, … */
  note?: string | null;
}

/** All rows of all groups, in the order Enter and the arrows walk them. */
export function flatRows(groups: readonly FindGroup[]): FindRow[] {
  return groups.flatMap((group) => group.rows);
}

/** The next row's index from `current` (-1: none yet), wrapping round. */
export function stepIndex(current: number, count: number, step: 1 | -1): number {
  if (count <= 0) return -1;
  if (current < 0) return step === 1 ? 0 : count - 1;
  return (current + step + count) % count;
}

// ---- the chat ---------------------------------------------------------------------------

export interface ChatHit {
  /** `data-find` of the element the chat draws for it. */
  key: string;
  kind: "you" | "agent";
  snippet: string;
  /** For the agent's words: the start of the person's message they answer, as the row's section. */
  section: string | null;
}

/** The `data-find` value of a turn's own message and of one of the agent's messages in it. */
export const chatKey = {
  you: (turnKey: string) => `${turnKey}:you`,
  agent: (itemKey: string) => `agent:${itemKey}`,
};

/** The person's messages and the agent's prose (no tool output, no thinking) that hold the query. */
export function searchChat(entries: readonly ThreadEntry[], raw: string, cap = FIND_CAP): { hits: ChatHit[]; capped: boolean } {
  const query = parseQuery(raw);
  if (!query) return { hits: [], capped: false };
  const items: Array<ChatHit & { text: string; heading: boolean }> = [];
  for (const entry of entries) {
    if (entry.type !== "turn") continue;
    const section = clipLine(entry.text) || "Your message";
    if (entry.text && matchRanges(entry.text, query)) {
      items.push({ key: chatKey.you(entry.key), kind: "you", section: null, snippet: "", text: entry.text, heading: false });
    }
    for (const item of entry.items) {
      if (item.type !== "message" || !item.text || !matchRanges(item.text, query)) continue;
      items.push({ key: chatKey.agent(item.key), kind: "agent", section, snippet: "", text: item.text, heading: false });
    }
  }
  const order = rank(items, query);
  return {
    hits: order.slice(0, cap).map((index) => {
      const { text, heading: _heading, ...hit } = items[index];
      return { ...hit, snippet: snippetAround(text, query) };   // cut only for the rows that are listed
    }),
    capped: order.length > cap,
  };
}

function clipLine(text: string): string {
  const line = text.replace(/\s+/g, " ").trim();
  return line.length > 60 ? `${line.slice(0, 59)}…` : line;
}

// ---- messages from the page (checked: the page is not trusted) --------------------------

export interface PageHit {
  n: number;
  kind: "heading" | "text";
  section: string | null;
  snippet: string;
  hidden: boolean;
}

export interface Found {
  id: number;
  total: number;
  capped: boolean;
  /** The page is longer than the page helper reads: only its first part was searched. */
  partial: boolean;
  hits: PageHit[];
}

export interface OutlineItem {
  n: number;
  level: number;
  text: string;
  hidden: boolean;
}

function str(value: unknown, max: number): string {
  return typeof value === "string" ? value.slice(0, max) : "";
}

function count(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) && value >= 0 ? Math.floor(value) : null;
}

export function readFound(data: Record<string, unknown>): Found | null {
  const id = count(data.id);
  if (id === null || !Array.isArray(data.hits)) return null;
  const hits: PageHit[] = [];
  const seen = new Set<number>();
  for (const raw of data.hits.slice(0, FIND_CAP)) {
    if (!raw || typeof raw !== "object") continue;
    const hit = raw as Record<string, unknown>;
    const n = count(hit.n);
    const snippet = str(hit.snippet, 400);
    if (n === null || !snippet || seen.has(n)) continue;
    seen.add(n);
    hits.push({
      n,
      kind: hit.kind === "heading" ? "heading" : "text",
      section: str(hit.section, 200) || null,
      snippet,
      hidden: hit.hidden === true,
    });
  }
  return { id, total: Math.max(count(data.total) ?? 0, hits.length), capped: data.capped === true, partial: data.partial === true, hits };
}

export function readOutline(data: Record<string, unknown>): OutlineItem[] | null {
  if (!Array.isArray(data.items)) return null;
  const out: OutlineItem[] = [];
  for (const raw of data.items.slice(0, FIND_CAP)) {
    if (!raw || typeof raw !== "object") continue;
    const item = raw as Record<string, unknown>;
    const n = count(item.n);
    const text = str(item.text, 200);
    if (n === null || !text) continue;
    out.push({ n, level: Math.min(6, Math.max(1, count(item.level) ?? 2)), text, hidden: item.hidden === true });
  }
  return out;
}

/** Cmd+F on a Mac, Ctrl+F elsewhere (Ctrl+F on a Mac moves the cursor in a text box). */
export function isFindKey(event: Pick<KeyboardEvent, "key" | "metaKey" | "ctrlKey" | "altKey" | "shiftKey">, mac: boolean): boolean {
  if (event.altKey || event.shiftKey || String(event.key).toLowerCase() !== "f") return false;
  return mac ? event.metaKey && !event.ctrlKey : event.ctrlKey && !event.metaKey;
}

export function isMac(): boolean {
  const nav = typeof navigator === "undefined" ? null : navigator;
  return !!nav && /Mac|iPhone|iPad|iPod/.test(nav.platform || nav.userAgent || "");
}
