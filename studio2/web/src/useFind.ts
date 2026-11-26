// Find on the page and in the chat: the panel's state, the messages to and from the page,
// and the jumps (CONTRACT.md, "Find on the page and in the chat"). No model calls.

import { useCallback, useDeferredValue, useEffect, useMemo, useRef, useState, type RefObject } from "react";
import {
  flatRows,
  fold,
  isFindKey,
  isMac,
  parseQuery,
  readFound,
  readOutline,
  searchChat,
  stepIndex,
  FIND_CAP,
  type FindGroup,
  type FindRow,
  type Found,
  type OutlineItem,
} from "./find";
import { fileOfPath, otherPageRows, otherPagesNote } from "./otherPages";
import type { PreviewHandle } from "./Preview";
import type { ThreadEntry } from "./thread";
import type { ProjectFind } from "./types";

/** How long the page may take to answer before the panel says it did not. */
export const PAGE_ANSWER_MS = 2500;
const TYPING_MS = 140;
/** The project's files are asked for after a longer pause than the page: it is a request. */
const OTHER_PAGES_MS = 250;
/** A jump to another page that has not happened by now is forgotten (the page never said it loaded:
 * a redirect, a page that is not there). It must not fire when the person reaches that page later. */
const JUMP_MS = 10_000;

interface UseFindOptions {
  preview: RefObject<PreviewHandle | null>;
  entries: readonly ThreadEntry[];
  /** Open the chat panel (a chat match is shown there). */
  openChat: () => void;
  /** Called before going to a place on the page (the chat sheet of a phone is lowered). */
  onPageGo?: () => void;
  /** More groups after the ones built here. */
  extraGroups?: FindGroup[];
  /**
   * Search the project's files (`GET /api/projects/{id}/find`). When given, the panel has a third
   * group, "Other pages". Keep its identity stable: a new function asks again.
   */
  searchPages?: (query: string, signal: AbortSignal) => Promise<ProjectFind>;
  /** Open a file that is not a web page (a note found in "Other pages"). */
  openFile?: (path: string) => void;
}

export interface FindState {
  open: boolean;
  query: string;
  setQuery: (query: string) => void;
  groups: FindGroup[];
  /** The key of the row last gone to. */
  current: string | null;
  /** About the row last gone to: it is in a hidden part of the page, … */
  note: string | null;
  /** "3 of 12", "12 matches", "No matches", or a hint while the query is too short. */
  countText: string;
  /** True while the box is empty and the list is the page's headings. */
  outlineShown: boolean;
  inputRef: RefObject<HTMLInputElement | null>;
  openFind: (text?: string) => void;
  close: () => void;
  step: (direction: 1 | -1) => void;
  /** Feed the preview's `onFind` here. */
  onFindMessage: (data: Record<string, unknown>) => void;
  /** Feed the preview's `onPageLoad` here: an open Find looks again on the new page. */
  onPageLoad: (path?: string) => void;
  /**
   * Show page `path` of the project and, when it is ready, find `query` there and go to the
   * first match whose snippet holds `text` (the first match when none does). For results from
   * other pages: the page is loaded only when it is not the one shown.
   */
  jumpToPage: (path: string, query: string, text: string) => void;
}

function plainPath(path: string): string {
  const cut = path.split(/[?#]/)[0] || "/";
  return cut.startsWith("/") ? cut : `/${cut}`;
}

function hiddenNote(section: string | null): string {
  return section
    ? `This one is in a part of the page that is not shown now (under “${section}”), so the page could not scroll to it.`
    : "This one is in a part of the page that is not shown now, so the page could not scroll to it.";
}

/** Scroll the chat (only the chat: the studio itself never scrolls) so `element` sits in its middle. */
function centreInChat(element: HTMLElement) {
  const scroller = element.closest<HTMLElement>(".chat-scroll");
  if (!scroller) return;
  const box = scroller.getBoundingClientRect();
  const rect = element.getBoundingClientRect();
  const top = scroller.scrollTop + rect.top - box.top - Math.max(0, (box.height - rect.height) / 2);
  const reduce = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
  scroller.scrollTo({ top: Math.max(0, top), behavior: reduce ? "auto" : "smooth" });
}

export function useFind({ preview, entries, openChat, onPageGo, extraGroups, searchPages, openFile }: UseFindOptions): FindState {
  const [open, setOpen] = useState(false);
  const [query, setQueryState] = useState("");
  const [found, setFound] = useState<Found | null>(null);
  const [outline, setOutline] = useState<OutlineItem[] | null>(null);
  const [silent, setSilent] = useState(false);
  const [current, setCurrent] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement | null>(null);
  const sentId = useRef(0);
  const answerTimer = useRef<number | null>(null);
  const flashTimer = useRef<number | null>(null);
  const jump = useRef<{ path: string; text: string; loading: boolean; id: number; at: number } | null>(null);
  const [others, setOthers] = useState<{ query: string; result: ProjectFind } | null>(null);
  const [othersFailed, setOthersFailed] = useState(false);
  const jumpRef = useRef<((path: string, raw: string, text: string) => void) | null>(null);
  /** A query set by a jump to another page: the look after typing is left to that page's load. */
  const setByJump = useRef<string | null>(null);
  const state = useRef({ open, query, found, outline });
  state.current = { open, query, found, outline };

  const send = useCallback((message: Record<string, unknown>) => preview.current?.send(message), [preview]);

  const waitForAnswer = useCallback(() => {
    if (answerTimer.current !== null) window.clearTimeout(answerTimer.current);
    setSilent(false);
    answerTimer.current = window.setTimeout(() => setSilent(true), PAGE_ANSWER_MS);
  }, []);
  const heard = useCallback(() => {
    if (answerTimer.current !== null) window.clearTimeout(answerTimer.current);
    answerTimer.current = null;
    setSilent(false);
  }, []);

  /** Ask the page for the current query (its headings when the box is empty). */
  const ask = useCallback(() => {
    const raw = state.current.query;
    setCurrent(null);
    setNote(null);
    if (!raw.trim()) {
      setFound(null);
      send({ type: "outline" });
      waitForAnswer();
      return;
    }
    setOutline(null);
    if (!parseQuery(raw)) {
      setFound(null);
      heard();
      send({ type: "find-clear" });
      return;
    }
    const id = ++sentId.current;
    if (jump.current && !jump.current.loading) jump.current.id = id;
    send({ type: "find", query: raw, id });
    waitForAnswer();
  }, [send, waitForAnswer, heard]);

  // Typing: look again shortly after the last key.
  useEffect(() => {
    if (!open) return;
    // A jump to another page set these words: that page is asked when it has loaded. Asking again
    // here, a moment later, would take the mark of the match off a page that loaded quickly.
    const jumped = setByJump.current === query;
    setByJump.current = null;
    if (jumped) return;
    const timer = window.setTimeout(ask, query.trim() ? TYPING_MS : 0);
    return () => window.clearTimeout(timer);
  }, [open, query, ask]);

  // The project's other pages: asked of the server a moment after the last key.
  useEffect(() => {
    if (!open || !searchPages || !parseQuery(query)) {
      setOthers(null);
      setOthersFailed(false);
      return;
    }
    const control = new AbortController();
    const timer = window.setTimeout(() => {
      searchPages(query, control.signal).then(
        (result) => {
          if (control.signal.aborted) return;
          setOthers({ query, result });
          setOthersFailed(false);
        },
        () => {
          if (control.signal.aborted) return;
          setOthers(null);
          setOthersFailed(true);
        },
      );
    }, OTHER_PAGES_MS);
    return () => {
      window.clearTimeout(timer);
      control.abort();
    };
  }, [open, query, searchPages]);

  /** The person typed in the box: a jump still waiting for another page is theirs no longer. */
  const typed = useCallback((next: string) => {
    jump.current = null;
    setQueryState(next);
  }, []);

  const openFind = useCallback((text?: string) => {
    if (text && text.trim()) setQueryState(text.trim().slice(0, 200));
    setOpen(true);
    window.requestAnimationFrame(() => {
      inputRef.current?.focus();
      inputRef.current?.select();
    });
  }, []);

  const close = useCallback(() => {
    setOpen(false);
    setCurrent(null);
    setNote(null);
    setFound(null);
    setOutline(null);
    jump.current = null;
    heard();
    send({ type: "find-clear" });
  }, [send, heard]);

  const goPage = useCallback(
    (n: number, key: string) => {
      jump.current = null;      // the person (or the jump itself) went somewhere: nothing is pending
      setCurrent(key);
      setNote(null);
      onPageGo?.();
      send({ type: "find-go", n });
    },
    [send, onPageGo],
  );

  const goChat = useCallback(
    (findKey: string, key: string) => {
      jump.current = null;
      setCurrent(key);
      setNote(null);
      openChat();
      // The chat may only now be drawn: look for the message after it is.
      let tries = 0;
      const look = () => {
        const element = document.querySelector<HTMLElement>(`[data-find="${CSS.escape(findKey)}"]`);
        if (!element) {
          if (++tries < 10) window.setTimeout(look, 50);
          else setNote("This message is not shown in the chat now.");
          return;
        }
        centreInChat(element);
        document.querySelectorAll(".is-find-flash").forEach((other) => other.classList.remove("is-find-flash"));
        element.classList.add("is-find-flash");
        if (flashTimer.current !== null) window.clearTimeout(flashTimer.current);
        flashTimer.current = window.setTimeout(() => element.classList.remove("is-find-flash"), 1800);
      };
      window.requestAnimationFrame(look);
    },
    [openChat],
  );

  // The chat is searched in the browser on every change of the words and of the thread (a chunk
  // of an answer arriving is one). Deferred, so a long chat does not hold up typing in the box.
  const chatQuery = useDeferredValue(query);
  const chatEntries = useDeferredValue(entries);
  const chat = useMemo(() => (open && chatQuery.trim() ? searchChat(chatEntries, chatQuery) : null), [open, chatEntries, chatQuery]);

  const groups = useMemo<FindGroup[]>(() => {
    if (!open) return [];
    if (!query.trim()) {
      const rows: FindRow[] = (outline ?? []).map((item) => ({
        key: `outline:${item.n}`,
        section: null,
        snippet: item.text,
        kind: "heading",
        level: item.level,
        hidden: item.hidden,
        onGo: () => {
          setCurrent(`outline:${item.n}`);
          setNote(null);
          onPageGo?.();
          send({ type: "outline-go", n: item.n });
        },
      }));
      return [
        {
          id: "outline",
          title: "Sections on this page",
          rows,
          note: silent
            ? "The page did not answer, so its sections cannot be listed."
            : outline && !rows.length
              ? "This page has no headings."
              : null,
        },
      ];
    }
    if (!parseQuery(query)) return [];
    const pageRows: FindRow[] = (found?.hits ?? []).map((hit) => ({
      key: `page:${hit.n}`,
      section: hit.section,
      snippet: hit.snippet,
      kind: hit.kind,
      hidden: hit.hidden,
      onGo: () => goPage(hit.n, `page:${hit.n}`),
    }));
    const chatRows: FindRow[] = (chat?.hits ?? []).map((hit) => ({
      key: `chat:${hit.key}`,
      section: hit.section,
      snippet: hit.snippet,
      kind: hit.kind,
      onGo: () => goChat(hit.key, `chat:${hit.key}`),
    }));
    return [
      {
        id: "page",
        title: "On this page",
        rows: pageRows,
        note: silent
          ? "The page did not answer. A file that is not a web page cannot be searched here."
          : found?.partial
            ? "This page is very long: only its first part was searched."
            : found?.capped
            ? `Showing the first ${FIND_CAP} of ${found.total}.`
            : found && !pageRows.length
              ? "Nothing on this page."
              : null,
      },
      {
        id: "chat",
        title: "In the chat",
        rows: chatRows,
        note: chat?.capped ? `Showing the first ${FIND_CAP}.` : !chatRows.length ? "Nothing in the chat." : null,
      },
      ...(searchPages ? [otherPages()] : []),
      ...(extraGroups ?? []),
    ];

    // The page shown is read here, when the list is built: `found` changes after every page load.
    function otherPages(): FindGroup {
      const shown = preview.current?.path() ?? "/";
      const asked = others?.query ?? query;
      const rows = otherPageRows(others?.result ?? null, shown, asked, {
        page: (path, text, key) => {
          setCurrent(key);
          setNote(null);
          onPageGo?.();
          jumpRef.current?.(path, asked, text);
          // The row that was clicked is about to leave the list (its page becomes "this page"):
          // the box takes the focus, so Enter and Escape still work.
          inputRef.current?.focus();
        },
        file: (path, key) => {
          setCurrent(key);
          setNote(null);
          openFile?.(path);
        },
      });
      return { id: "other", title: "Other pages", rows, note: otherPagesNote(others?.result ?? null, othersFailed, rows.length, shown) };
    }
  }, [open, query, outline, found, chat, silent, extraGroups, send, goPage, goChat, onPageGo, searchPages, others, othersFailed, openFile, preview]);

  const rows = useMemo(() => flatRows(groups), [groups]);
  const index = current ? rows.findIndex((row) => row.key === current) : -1;

  const step = useCallback(
    (direction: 1 | -1) => {
      const next = stepIndex(index, rows.length, direction);
      if (next >= 0) rows[next].onGo();
    },
    [index, rows],
  );

  const countText = !query.trim()
    ? ""
    : !parseQuery(query)
      ? "Type two letters or more"
      : rows.length === 0
        ? found || silent
          ? "No matches"
          : "Looking…"
        : index >= 0
          ? `${index + 1} of ${rows.length}`
          : `${rows.length} ${rows.length === 1 ? "match" : "matches"}`;

  const onFindMessage = useCallback(
    (data: Record<string, unknown>) => {
      if (data.type === "find-open") {
        openFind(typeof data.text === "string" ? data.text : undefined);
      } else if (data.type === "find-close") {
        if (state.current.open) close();
      } else if (data.type === "found") {
        const answer = readFound(data);
        if (!answer || answer.id !== sentId.current || !state.current.open) return;
        heard();
        setFound(answer);
        if (jump.current && Date.now() - jump.current.at > JUMP_MS) jump.current = null;
        const pending = jump.current;
        if (pending && pending.id === answer.id) {
          jump.current = null;
          const want = fold(pending.text).text;
          const hit = answer.hits.find((item) => want && fold(item.snippet).text.includes(want)) ?? answer.hits[0];
          if (hit) goPage(hit.n, `page:${hit.n}`);
        }
      } else if (data.type === "outline") {
        const items = readOutline(data);
        if (!items || !state.current.open || state.current.query.trim()) return;
        heard();
        setOutline(items);
      } else if (data.type === "find-went" || data.type === "outline-went") {
        if (data.hidden !== true) return;
        const n = typeof data.n === "number" ? data.n : -1;
        const section =
          data.type === "find-went"
            ? (state.current.found?.hits.find((hit) => hit.n === n)?.section ?? null)
            : (state.current.outline?.find((item) => item.n === n)?.text ?? null);
        setNote(hiddenNote(section));
      }
    },
    [openFind, close, heard, goPage],
  );

  const onPageLoad = useCallback((path?: string) => {
    // A jump to another page waits for THAT page: one that was still loading may answer first.
    if (jump.current && Date.now() - jump.current.at > JUMP_MS) jump.current = null;
    const pending = jump.current;
    // (`path` is what the page that loaded says; the preview's own note may already name the next one.)
    if (pending && fileOfPath(path ?? preview.current?.path() ?? "/") === fileOfPath(pending.path)) pending.loading = false;
    if (state.current.open) ask();
  }, [ask, preview]);

  const jumpToPage = useCallback(
    (path: string, raw: string, text: string) => {
      const shown = preview.current?.path() ?? "/";
      const other = fileOfPath(shown) !== fileOfPath(path);
      jump.current = { path, text, loading: other, id: -1, at: Date.now() };
      // Only when the words or the panel change does the look after typing run at all.
      if (other && (!state.current.open || state.current.query !== raw)) setByJump.current = raw;
      setQueryState(raw);
      setOpen(true);
      if (other) preview.current?.show(plainPath(path));
      else if (state.current.open && state.current.query === raw) ask();
    },
    [preview, ask],
  );

  jumpRef.current = jumpToPage;
  const closeRef = useRef(close);
  closeRef.current = close;

  // Cmd/Ctrl+F in the studio opens Find; pressed again in the box, it is the browser's own.
  useEffect(() => {
    const mac = isMac();
    const onKey = (event: KeyboardEvent) => {
      // Escape with the keyboard nowhere (a row that was clicked has left the list) closes Find.
      if (event.key === "Escape" && state.current.open && (document.activeElement === document.body || !document.activeElement)) {
        event.preventDefault();
        closeRef.current();
        return;
      }
      if (!isFindKey(event, mac)) return;
      if (inputRef.current && document.activeElement === inputRef.current) return;
      event.preventDefault();
      openFind();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [openFind]);

  useEffect(
    () => () => {
      if (answerTimer.current !== null) window.clearTimeout(answerTimer.current);
      if (flashTimer.current !== null) window.clearTimeout(flashTimer.current);
    },
    [],
  );

  return {
    open,
    query,
    setQuery: typed,
    groups,
    current,
    note,
    countText,
    outlineShown: open && !query.trim(),
    inputRef,
    openFind,
    close,
    step,
    onFindMessage,
    onPageLoad,
    jumpToPage,
  };
}
