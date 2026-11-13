// A tiny History API router: `/` is home, `/p/:id` is a project, `/library` and `/setup` are pages.

import { useCallback, useEffect, useState } from "react";

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

export type Navigate = (to: string, options?: { replace?: boolean }) => void;

export function useRoute(): [Route, Navigate] {
  const [path, setPath] = useState(() => window.location.pathname);
  useEffect(() => {
    const onPop = () => setPath(window.location.pathname);
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);
  const navigate = useCallback<Navigate>((to, options) => {
    if (to !== window.location.pathname + window.location.hash) {
      if (options?.replace) window.history.replaceState(null, "", to);
      else window.history.pushState(null, "", to);
    }
    setPath(to);
  }, []);
  return [parseRoute(path), navigate];
}
