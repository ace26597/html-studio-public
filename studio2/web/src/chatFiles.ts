// The project's file listing as the chat's file cards need it: each path's size now, and whether
// the listing is whole (so a path missing from it is really gone).

import { useEffect, useMemo, useState } from "react";
import { api } from "./api";
import { deliverablesOf } from "./deliverables";
import type { ThreadState } from "./thread";

/** The listing route stops at this many files (`GET /api/projects/{id}/files`). */
export const LISTING_LIMIT = 5000;

export interface ChatFiles {
  sizes: ReadonlyMap<string, number> | null;
  complete: boolean;
}

/** Whether any finished request in the thread has a file card (only then is the listing read). */
export function threadHasCards(thread: ThreadState): boolean {
  return thread.entries.some(
    (entry) =>
      entry.type === "turn" &&
      entry.status !== "running" &&
      deliverablesOf(
        entry.items.filter((item) => item.type === "checkpoint"),
        entry.attachments,
      ).shown.length > 0,
  );
}

/**
 * Reads the listing when the workspace changed and no request is running (the cards appear
 * after a request ends, so the many changes during one are not each fetched).
 */
export function useChatFiles(projectId: string, thread: ThreadState): ChatFiles {
  const running = thread.runningTurnId !== null;
  const needed = useMemo(() => !running && threadHasCards(thread), [running, thread]);
  const [state, setState] = useState<ChatFiles>({ sizes: null, complete: false });
  useEffect(() => {
    if (!needed) return;
    let cancelled = false;
    api.listFiles(projectId).then(
      (list) => {
        if (cancelled) return;
        setState({ sizes: new Map(list.map((entry) => [entry.path, entry.size])), complete: list.length < LISTING_LIMIT });
      },
      () => {
        // Not known: the cards still open the file, without a size.
        if (!cancelled) setState({ sizes: null, complete: false });
      },
    );
    return () => {
      cancelled = true;
    };
  }, [projectId, needed, thread.filesRev]);
  return state;
}
