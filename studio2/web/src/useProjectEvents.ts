// One EventSource per open project. The whole log is replayed from seq 0 on
// load, so the thread rebuilds itself after a browser reload, even mid-turn.

import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "./api";
import { initialThread, reduceEvents, seedApp, type ThreadState } from "./thread";
import type { AppInfo, StudioEvent } from "./types";

export type StreamStatus = "connecting" | "open" | "reconnecting";

/**
 * `onLive` is called for events that happened while this page was open (not
 * for replayed history), so the caller can reload the preview or refresh lists
 * without doing so once per historical event.
 */
export function useProjectEvents(
  projectId: string,
  onLive: (event: StudioEvent) => void,
  enabled = true,
): {
  thread: ThreadState;
  stream: StreamStatus;
  /** The highest event seq received so far (applied or about to be). */
  receivedSeq: () => number;
  /** Fold a `GET …/app` answer in; `requestedAtSeq` is `receivedSeq()` when it was requested. */
  applyAppInfo: (info: AppInfo, requestedAtSeq: number) => void;
} {
  const [thread, setThread] = useState<ThreadState>(initialThread);
  const [stream, setStream] = useState<StreamStatus>("connecting");
  const receivedRef = useRef(0);
  const onLiveRef = useRef(onLive);
  useEffect(() => {
    onLiveRef.current = onLive;
  });

  useEffect(() => {
    // A project that does not exist has no stream; do not keep retrying one.
    if (!enabled) return;
    let disposed = false;
    let source: EventSource | null = null;
    let queue: StudioEvent[] = [];
    let frame = 0;
    let retry = 0;
    let lastSeq = 0;
    receivedRef.current = 0;
    // The contract has no "replay finished" marker, so an event counts as live
    // when its timestamp is not older than this page's first connection.
    const liveAfter = Date.now() - 1500;

    setThread(initialThread);
    setStream("connecting");

    const flush = () => {
      frame = 0;
      if (disposed || !queue.length) return;
      const batch = queue;
      queue = [];
      setThread((state) => reduceEvents(state, batch));
    };

    const connect = () => {
      if (disposed) return;
      // The browser reconnects by itself with Last-Event-ID; `after` covers the
      // case where we have to open a fresh EventSource.
      source = new EventSource(api.eventsUrl(projectId, lastSeq));
      source.onopen = () => {
        if (!disposed) setStream("open");
      };
      source.onmessage = (message) => {
        let event: StudioEvent;
        try {
          event = JSON.parse(message.data) as StudioEvent;
        } catch {
          return;
        }
        if (!event || typeof event.seq !== "number" || event.seq <= lastSeq) return;
        lastSeq = event.seq;
        receivedRef.current = lastSeq;
        queue.push(event);
        // Batch per frame: a replay of thousands of events is one render.
        if (!frame) frame = window.setTimeout(flush, 16);
        const at = Date.parse(event.ts);
        if (!Number.isFinite(at) || at >= liveAfter) onLiveRef.current(event);
      };
      source.onerror = () => {
        if (disposed) return;
        setStream("reconnecting");
        if (source?.readyState === EventSource.CLOSED) {
          source.close();
          window.clearTimeout(retry);
          retry = window.setTimeout(connect, 2000);
        }
      };
    };

    connect();

    return () => {
      disposed = true;
      source?.close();
      window.clearTimeout(frame);
      window.clearTimeout(retry);
    };
  }, [projectId, enabled]);

  const receivedSeq = useCallback(() => receivedRef.current, []);
  const applyAppInfo = useCallback((info: AppInfo, requestedAtSeq: number) => {
    setThread((state) => seedApp(state, info, requestedAtSeq));
  }, []);

  return { thread, stream, receivedSeq, applyAppInfo };
}
