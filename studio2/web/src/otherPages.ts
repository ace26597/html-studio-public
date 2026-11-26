// Find: the matches in the project's OTHER pages and notes, as rows of the find panel
// (CONTRACT.md, "Find on the page and in the chat" and "Find words in a project's pages and
// across projects"). The server reads the files; the page shown is searched by the page itself,
// so its own file is left out here.

import { markParts, parseQuery, type FindRow } from "./find";
import type { ProjectFind } from "./types";

/** The file a preview path shows: no query or fragment, a folder is its `index.html`. */
export function fileOfPath(path: string): string {
  let clean = (path.split(/[?#]/)[0] || "/").replace(/^\/+/, "");
  try {
    clean = decodeURIComponent(clean);
  } catch {
    // a path that is not valid percent-encoding is compared as it is
  }
  if (clean === "" || clean.endsWith("/")) clean += "index.html";
  return clean;
}

/** A page the preview can show and search (its helper is only put into web pages). */
export function isWebPage(path: string): boolean {
  return /\.html?$/i.test(path);
}

/**
 * A few words round the first match of `snippet`: what is looked for again on the page once it is
 * open. The server cuts its snippets from the file and the page cuts its own from what is drawn,
 * so the two differ at the ends; the words next to the match are in both.
 */
export function coreOf(snippet: string, raw: string, around = 15): string {
  const flat = snippet.replace(/…/g, "").replace(/\s+/g, " ").trim();
  const parts = markParts(flat, parseQuery(raw));
  const at = parts.findIndex((part) => part.hit);
  if (at === -1) return flat.slice(0, 2 * around);
  const before = at > 0 ? parts[at - 1].text.slice(-around) : "";
  const after = at + 1 < parts.length ? parts[at + 1].text.slice(0, around) : "";
  return (before + parts[at].text + after).trim();
}

export interface OtherPagesGo {
  /** Show a web page of the project and go to the match holding `text`. */
  page: (path: string, text: string, key: string) => void;
  /** Open a file that is not a web page (a note) where files are read. */
  file: (path: string, key: string) => void;
}

/** One row per hit in every page but the one shown. `shown` is the preview's path. */
export function otherPageRows(result: ProjectFind | null, shown: string, raw: string, go: OtherPagesGo): FindRow[] {
  if (!result) return [];
  const here = fileOfPath(shown);
  const rows: FindRow[] = [];
  for (const page of result.pages) {
    if (page.path === here) continue;
    const name = page.title && page.title !== page.path ? `${page.title} (${page.path})` : page.path;
    page.hits.forEach((hit, index) => {
      const key = `other:${page.path}:${index}`;
      rows.push({
        key,
        section: hit.section ? `${name} · ${hit.section}` : name,
        snippet: hit.snippet,
        kind: hit.kind,
        onGo: isWebPage(page.path)
          ? () => go.page(page.path, coreOf(hit.snippet, raw), key)
          : () => go.file(page.path, key),
      });
    });
  }
  return rows;
}

/** What the "Other pages" group says under its title, null when the rows speak for themselves. */
export function otherPagesNote(result: ProjectFind | null, failed: boolean, rows: number, shown: string): string | null {
  if (failed) return "The other pages could not be searched just now.";
  if (!result) return null;
  if (result.attached) return "This is an attached app: only the page shown and the chat are searched.";
  if (result.capped) return "Not everything was searched: some files are too large or there are too many.";
  const here = fileOfPath(shown);
  const more = result.pages.reduce((sum, page) => sum + (page.path === here ? 0 : page.more), 0);
  if (more > 0) return `And ${more} more: add a word to narrow it down.`;
  return rows === 0 ? "Nothing in the other pages." : null;
}
