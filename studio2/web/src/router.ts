// A tiny History API router: `/` is home, `/p/:id` is a project, `/library` and `/setup` are pages.

import { useCallback, useEffect, useRef, useState } from "react";

export type Route =
  | { name: "home" }
  | { name: "library" }
  | { name: "setup" }
  | { name: "project"; id: string }
  | { name: "missing" };

export function parseRoute(path: string): Route {
  const pathname = path.replace(/[?#].*$/, "");
  if (pathname === "/" || pathname === "") return { name: "home" };
  if (pathname === "/library" || pathname === "/library/") return { name: "library" };
  if (pathname === "/setup" || pathname === "/setup/") return { name: "setup" };
  const match = /^\/p\/([A-Za-z0-9_-]+)\/?$/.exec(pathname);
  if (match) return { name: "project", id: match[1] };
  return { name: "missing" };
}

/**
 * A project's address, optionally on one of its files and with words to find (CONTRACT.md, "Find
 * words in a project's pages and across projects"): `/p/<id>?page=<path>&find=<words>`.
 */
export function projectHref(id: string, options: { page?: string | null; find?: string | null } = {}): string {
  const params = new URLSearchParams();
  if (options.page) params.set("page", options.page);
  const find = options.find?.trim();
  if (find) params.set("find", find);
  const query = params.toString();
  return `/p/${id}${query ? `?${query}` : ""}`;
}

/** The `page` and `find` a project's address carries (`location.search`), null when absent. */
export function projectParams(search: string): { page: string | null; find: string | null } {
  const params = new URLSearchParams(search);
  return { page: params.get("page") || null, find: params.get("find")?.trim() || null };
}

export type Navigate =(to: string, options?: { replace?: boolean }) => void;

/** A page that holds something unsaved: asks the person, true when it is fine to leave. */
export type LeaveGuard = () => boolean;

let leaveGuard: LeaveGuard | null = null;

/**
 * The open page's question before it is left (the file viewer's unsaved edits). Returns a
 * clean-up that clears it only if it is still this page's, so a late clean-up cannot drop the
 * next page's question.
 */
export function setLeaveGuard(guard: LeaveGuard | null): () => void {
  leaveGuard = guard;
  return () => {
    if (leaveGuard === guard) leaveGuard = null;
  };
}

const pathOf = (href: string) => href.replace(/[?#].*$/, "") || "/";

/** Every way out of a page goes through here: "All pages", a link inside the app, Back. */
export function mayLeave(from: string, to: string): boolean {
  if (pathOf(from) === pathOf(to) || !leaveGuard) return true;
  return leaveGuard();
}

/** Closing or reloading the tab: the browser's own "Leave site?" question, only while there are edits. */
export function holdUnload(event: { preventDefault: () => void; returnValue: unknown }, dirty: boolean): void {
  if (!dirty) return;
  event.preventDefault();
  event.returnValue = ""; // older browsers ask only when this is set
}

/** Where an entry sits in this tab's history, kept in `history.state` so Back can be undone. */
const indexOf = (state: unknown): number | null => {
  const index = (state as { studioIndex?: unknown } | null)?.studioIndex;
  return typeof index === "number" ? index : null;
};

export function useRoute(): [Route, Navigate] {
  const [path, setPath] = useState(() => window.location.pathname);
  const shown = useRef(window.location.pathname + window.location.search + window.location.hash);
  const at = useRef(0);
  const undoing = useRef(false);
  useEffect(() => {
    const known = indexOf(window.history.state);
    if (known === null) window.history.replaceState({ ...(window.history.state ?? {}), studioIndex: 0 }, "");
    at.current = known ?? 0;
    const onPop = (event: PopStateEvent) => {
      const index = indexOf(event.state);
      if (undoing.current) {
        // The step back to where the person chose to stay.
        undoing.current = false;
        return;
      }
      const here = window.location.pathname + window.location.search + window.location.hash;
      if (!mayLeave(shown.current, here)) {
        // Back (or Forward) was pressed and the person chose to stay: step the history back to
        // this page, so a later Back still goes where it would have.
        if (index !== null && index !== at.current) {
          undoing.current = true;
          window.history.go(at.current - index);
        } else {
          window.history.pushState({ studioIndex: at.current }, "", shown.current);
        }
        return;
      }
      if (index !== null) at.current = index;
      shown.current = here;
      setPath(window.location.pathname);
    };
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);
  const navigate = useCallback<Navigate>((to, options) => {
    if (!mayLeave(shown.current, to)) return;
    shown.current = to;
    if (to !== window.location.pathname + window.location.hash) {
      if (options?.replace) {
        window.history.replaceState({ studioIndex: at.current }, "", to);
      } else {
        at.current += 1;
        window.history.pushState({ studioIndex: at.current }, "", to);
      }
    }
    setPath(to);
  }, []);
  return [parseRoute(path), navigate];
}
