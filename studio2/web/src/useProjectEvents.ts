// One EventSource per open project. The whole log is replayed from seq 0 on
// load, so the thread rebuilds itself after a browser reload, even mid-turn.

import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "./api";
import { goToSignIn, needsSignIn } from "./signIn";
import { STALL_CHECK_MS, streamStalled } from "./streamWatch";
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
    // When anything (an event, a keep-alive, the connection opening) last arrived.
    let heardAt = Date.now();
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

    const reconnectLater = () => {
      if (disposed) return;
      window.clearTimeout(retry);
      retry = window.setTimeout(connect, 2000);
    };

    const connect = () => {
      if (disposed) return;
      // The browser reconnects by itself with Last-Event-ID; `after` covers the
      // case where we have to open a fresh EventSource.
      source = new EventSource(api.eventsUrl(projectId, lastSeq));
      heardAt = Date.now();
      source.onopen = () => {
        heardAt = Date.now();
        if (!disposed) setStream("open");
      };
      // The server's keep-alive: nothing to apply, only proof that the connection is alive.
      source.addEventListener("ping", () => {
        heardAt = Date.now();
      });
      source.onmessage = (message) => {
        heardAt = Date.now();
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
          // The browser gave up on an answer that was not a stream. A 401 means the access key
          // expired or changed: that is the sign-in page, not a retry every 2 s for ever.
          api.health().then(
            () => reconnectLater(),
            (error) => {
              if (disposed) return;
              if (needsSignIn(error)) goToSignIn();
              else reconnectLater();
            },
          );
        }
      };
    };

    connect();

    // A half-open connection (after a sleep or a change of network) keeps saying "open" and
    // delivers nothing, so the thread would say "Working…" for ever. Silence for three
    // keep-alives means it is dead: open a new one from the last seq received.
    const watch = window.setInterval(() => {
      if (disposed || !source || source.readyState === EventSource.CLOSED) return;
      if (!streamStalled(heardAt, Date.now())) return;
      source.close();
      setStream("reconnecting");
      connect();
    }, STALL_CHECK_MS);

    return () => {
      disposed = true;
      source?.close();
      window.clearTimeout(frame);
      window.clearTimeout(retry);
      window.clearInterval(watch);
    };
  }, [projectId, enabled]);

  const receivedSeq = useCallback(() => receivedRef.current, []);
  const applyAppInfo = useCallback((info: AppInfo, requestedAtSeq: number) => {
    setThread((state) => seedApp(state, info, requestedAtSeq));
  }, []);

  return { thread, stream, receivedSeq, applyAppInfo };
}
