// App logs: a floating panel on the right (the same place as Versions) that
// follows the project's app output by polling `GET …/app/logs?after=<n>`.

import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { ApiError, api, errorMessage } from "./api";
import { emptyLogBuffer, mergeLogs, type LogBuffer } from "./appLogs";
import { appStateLabel } from "./format";
import { Icon, Spinner } from "./icons";
import type { AppStatus } from "./thread";
import { IconButton } from "./ui";

const POLL_MS = 1000;

interface LogsDrawerProps {
  projectId: string;
  app: AppStatus | null;
  onClose: () => void;
}

function emptyText(app: AppStatus | null, noApp: boolean): string {
  if (noApp || (app && !app.configured)) {
    return "This project has no app. Add a studio.json with a run command to give it a back end.";
  }
  switch (app?.state) {
    case "stopped":
      return "No output yet. The app is stopped.";
    case "crashed":
      return "No output yet. The app exited without writing anything.";
    case "starting":
      return "No output yet. The app is starting.";
    default:
      return "No output yet.";
  }
}

export function LogsDrawer({ projectId, app, onClose }: LogsDrawerProps) {
  const [buffer, setBuffer] = useState<LogBuffer>(emptyLogBuffer);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [noApp, setNoApp] = useState(false);
  const [showJump, setShowJump] = useState(false);
  const scroller = useRef<HTMLDivElement>(null);
  const stick = useRef(true);

  useEffect(() => {
    let disposed = false;
    let timer = 0;
    let controller: AbortController | null = null;
    // The cursor lives here, not in React state, so each poll asks with the newest one.
    let current: LogBuffer = emptyLogBuffer;

    setBuffer(emptyLogBuffer);
    setLoaded(false);
    setError(null);
    setNoApp(false);

    const poll = async () => {
      if (disposed) return;
      controller = new AbortController();
      const after = current.after;
      let again = POLL_MS;
      try {
        const answer = await api.appLogs(projectId, after, controller.signal);
        if (disposed) return;
        const merged = mergeLogs(current, answer, after);
        current = merged.buffer;
        setBuffer(current);
        setLoaded(true);
        setError(null);
        setNoApp(false);
        // The app restarted: its lines start again at 1. Ask from the start straight away.
        if (merged.restarted) again = 0;
      } catch (cause) {
        if (disposed || (cause as Error)?.name === "AbortError") return;
        if (cause instanceof ApiError && cause.code === "no_app") {
          setNoApp(true);
          setError(null);
        } else {
          setError(errorMessage(cause));
        }
        setLoaded(true);
      }
      if (!disposed) timer = window.setTimeout(() => void poll(), again);
    };

    void poll();
    return () => {
      disposed = true;
      window.clearTimeout(timer);
      controller?.abort();
    };
  }, [projectId]);

  // Follow new output unless the reader scrolled up.
  useLayoutEffect(() => {
    const element = scroller.current;
    if (element && stick.current) element.scrollTop = element.scrollHeight;
  }, [buffer]);

  const onScroll = () => {
    const element = scroller.current;
    if (!element) return;
    const atBottom = element.scrollHeight - element.scrollTop - element.clientHeight < 24;
    stick.current = atBottom;
    setShowJump(!atBottom);
  };

  const jump = () => {
    const element = scroller.current;
    if (!element) return;
    stick.current = true;
    setShowJump(false);
    element.scrollTop = element.scrollHeight;
  };

  const status = app && app.configured ? appStateLabel(app) : null;

  return (
    <aside
      className="panel panel-right logs-panel"
      aria-label="App logs"
      onKeyDown={(event) => {
        if (event.key === "Escape" && !event.defaultPrevented) {
          event.stopPropagation();
          onClose();
        }
      }}
    >
      <header className="panel-header">
        <h2>App logs</h2>
        {status && <span className={`app-state-text is-${status.tone}`}>{status.label}</span>}
        <span className="panel-spacer" />
        <IconButton icon="x" label="Close app logs" tip="Close (Esc)" tipAlign="end" onClick={onClose} />
      </header>
      {error && (
        <div className="viewer-banner logs-error" role="alert">
          <Icon name="alert" size={14} />
          <span>{error} Retrying…</span>
        </div>
      )}
      <div className="logs-body">
        <div
          className="logs-scroll"
          ref={scroller}
          onScroll={onScroll}
          tabIndex={0}
          role="log"
          aria-label="App output"
          aria-live="off"
        >
          {!loaded && (
            <div className="panel-empty">
              <Spinner />
            </div>
          )}
          {loaded && buffer.lines.length === 0 && !error && <div className="panel-empty">{emptyText(app, noApp)}</div>}
          {buffer.lines.length > 0 && (
            <pre className="logs-text">
              {buffer.trimmed && <span className="logs-note">Earlier lines are not shown.{"\n"}</span>}
              {buffer.lines.map((line) => (
                <span key={line.n} className="logs-line">
                  {line.text}
                  {"\n"}
                </span>
              ))}
            </pre>
          )}
        </div>
        {showJump && buffer.lines.length > 0 && (
          <button type="button" className="jump" onClick={jump}>
            <Icon name="arrow-down" size={13} strokeWidth={2} /> Jump to latest
          </button>
        )}
      </div>
    </aside>
  );
}
