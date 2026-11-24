// The project preview: a full-bleed iframe on the project's own origin, plus
// the shell side of the postMessage bridge (see CONTRACT.md, "Preview and bridge").
//
// Reloads are flash-free: the new document loads in a second iframe stacked
// under the visible one and takes over when its bridge reports `ready`.

import { forwardRef, useCallback, useEffect, useImperativeHandle, useMemo, useRef, useState } from "react";
import { readEnlarge, type LargePicture } from "./enlarge";
import { readAutoReload } from "./reload";
import type { Drawing, DrawTool, Mode, Selection, TextEdit } from "./types";

interface Frame {
  key: number;
  src: string;
}

interface PreviewProps {
  /** The studio's server answers the page's sign-in trip (`health.page_sign_in`). Without it the notice is as before. */
  signsInByItself?: boolean;
  previewUrl: string;
  title: string;
  mode: Mode;
  /** Bump to reload the preview (debounced by the caller). */
  reloadToken: number;
  onSelect: (selection: Selection) => void;
  onTextEdit: (edit: TextEdit) => void;
  /** The visible page's `ready.auto_reload` (true when it did not say). */
  onAutoReload?: (autoReload: boolean) => void;
  /** The person pressed "Enlarge" on a picture in the page (the bridge's `enlarge`, already checked). */
  onEnlarge?: (picture: LargePicture) => void;
  /** The visible page's helper can show what changed (`ready.changes`); false for an older helper. */
  onCanShowChanges?: (can: boolean) => void;
  /** The answer to `show-changes`: how many places read differently, or null when the page has no note to compare with. */
  onChanges?: (count: number | null) => void;
  /** Draw mode's tool and colour, sent with `draw` on change and after every `ready`. */
  drawTool?: DrawTool;
  drawColor?: string;
  /** The visible frame's `drawing` message. */
  onDrawing?: (count: number, drawing: Drawing | null) => void;
  /** A page loaded in the visible frame (its `ready`); any marks on it are gone. */
  onPageLoad?: () => void;
}

export interface PreviewHandle {
  /** Post a shell message (`draw-undo`, `draw-clear`, …) to the visible frame. */
  send: (message: Record<string, unknown>) => void;
}

const SANDBOX = "allow-scripts allow-same-origin allow-forms allow-popups allow-downloads";

/** How long a page with its own name may say nothing before the shell says so (CONTRACT.md,
 * "A page with its own name that shows nothing in the studio"). */
export const SILENT_AFTER_MS = 8000;

/** True when the project's page is on another host than the studio: behind a gate it then has a
 * sign-in of its own, and a sign-in page does not show inside a frame. */
export function hasOwnName(previewUrl: string, shellHost: string = window.location.hostname): boolean {
  try {
    return new URL(previewUrl).hostname !== shellHost;
  } catch {
    return false;
  }
}

/** The tab goes to the page's address by itself only this soon after the project opened… */
export const HAND_OFF_WINDOW_MS = 4000;
/** …and not twice within this long from one tab (a trip that did not help is not repeated). */
export const HAND_OFF_AGAIN_MS = 120_000;

/** The one-pixel picture every page port serves: it arrives only when the gate lets this browser reach that address. */
export function hereUrl(origin: string, now: number): string {
  return `${origin}/__studio/here.gif?${now}`;
}

/** A trip to the page's own address that comes back here: the gate signs the person in there on the way. */
export function handOffUrl(origin: string, here: string): string {
  return `${origin}/__studio/back?to=${encodeURIComponent(here)}`;
}

export function mayHandOff(lastTried: number | null, openedAt: number, now: number): boolean {
  if (now - openedAt > HAND_OFF_WINDOW_MS) return false;
  return lastTried === null || !Number.isFinite(lastTried) || now - lastTried > HAND_OFF_AGAIN_MS;
}

const HINTS: Record<Mode, string | null> = {
  browse: null,
  select: "Click anything on the page to point the agent at it",
  edit: "Click any text on the page to change it",
  draw: null,
};

function str(value: unknown, max: number): string {
  return typeof value === "string" ? value.slice(0, max) : "";
}

/** Over the frame of a page with its own name that never loaded: what happened and the two ways on. */
export function SilentNotice({
  previewUrl,
  onTryAgain,
  backTo,
}: {
  previewUrl: string;
  onTryAgain: () => void;
  /** Where the studio is now, when its server can bring a visitor back from the page's address. */
  backTo?: string | null;
}) {
  if (backTo) {
    return (
      <div className="preview-silent" role="status">
        <p className="preview-silent-title">This page needs you signed in at its own address</p>
        <p>
          It is at <span className="preview-silent-host">{new URL(previewUrl).host}</span>, and the gate keeps a
          sign-in for each address. One tap goes there, signs you in and comes straight back.
        </p>
        <div className="preview-silent-actions">
          <a className="btn btn-primary" href={handOffUrl(new URL(previewUrl).origin, backTo)}>
            Sign in to this page
          </a>
          <button type="button" className="btn" onClick={onTryAgain}>
            Try again
          </button>
        </div>
      </div>
    );
  }
  return (
    <div className="preview-silent" role="status">
      <p className="preview-silent-title">This page has not loaded here</p>
      <p>
        It has its own address, <span className="preview-silent-host">{new URL(previewUrl).host}</span>, and nothing
        has come back from it. Most often that address wants you signed in there first, and a sign-in page cannot be
        shown inside the studio. Open the page once in its own tab, then come back.
      </p>
      <div className="preview-silent-actions">
        <a className="btn btn-primary" href={previewUrl} target="_blank" rel="noreferrer">
          Open the page
        </a>
        <button type="button" className="btn" onClick={onTryAgain}>
          Try again
        </button>
      </div>
    </div>
  );
}

export const Preview = forwardRef<PreviewHandle, PreviewProps>(function Preview(
  {
    signsInByItself,
  previewUrl,
    title,
    mode,
    reloadToken,
    onSelect,
    onTextEdit,
    onAutoReload,
    onEnlarge,
    onCanShowChanges,
    onChanges,
    drawTool = "pen",
    drawColor = "#ef4444",
    onDrawing,
    onPageLoad,
  },
  ref,
) {
  const origin = useMemo(() => new URL(previewUrl).origin, [previewUrl]);
  const [frames, setFrames] = useState<Frame[]>(() => [{ key: 1, src: previewUrl }]);
  const [activeKey, setActiveKey] = useState(1);

  const elements = useRef(new Map<number, HTMLIFrameElement>());
  const activeRef = useRef(1);
  const pendingRef = useRef<number | null>(null);
  const nextKey = useRef(2);
  const pathRef = useRef(new URL(previewUrl).pathname || "/");
  const modeRef = useRef(mode);
  const handlers = useRef({ onSelect, onTextEdit, onAutoReload, onEnlarge, onCanShowChanges, onChanges, onDrawing, onPageLoad });
  useEffect(() => {
    handlers.current = { onSelect, onTextEdit, onAutoReload, onEnlarge, onCanShowChanges, onChanges, onDrawing, onPageLoad };
  });
  const drawRef = useRef({ tool: drawTool, color: drawColor });
  // What each frame's bridge said in `ready`; a frame that never said counts as true.
  const autoReloads = useRef(new Map<number, boolean>());
  // Whether each frame's helper can show what changed (`ready.changes`).
  const canChanges = useRef(new Map<number, boolean>());
  // A page with its own name that never says `ready`: nothing of the studio's loaded in the frame.
  const ownName = useMemo(() => hasOwnName(previewUrl), [previewUrl]);
  const heard = useRef(false);
  const [silent, setSilent] = useState(false);
  const [attempt, setAttempt] = useState(0);

  const post = useCallback(
    (key: number, message: Record<string, unknown>) => {
      elements.current.get(key)?.contentWindow?.postMessage({ source: "studio-shell", ...message }, origin);
    },
    [origin],
  );

  const promote = useCallback((key: number) => {
    if (pendingRef.current !== key) return;
    pendingRef.current = null;
    activeRef.current = key;
    setActiveKey(key);
    // A fresh page: whatever was drawn on the old one is gone.
    handlers.current.onPageLoad?.();
    handlers.current.onAutoReload?.(autoReloads.current.get(key) ?? true);
    handlers.current.onCanShowChanges?.(canChanges.current.get(key) ?? false);
    setFrames((list) => list.filter((frame) => frame.key === key));
  }, []);

  // Bridge messages, validated exactly as the contract requires: the sender is
  // one of our iframes' windows, the origin is this project's preview origin,
  // and the payload is marked `studio-bridge`.
  useEffect(() => {
    const onMessage = (event: MessageEvent) => {
      if (event.origin !== origin) return;
      const data = event.data as Record<string, unknown> | null;
      if (!data || typeof data !== "object" || data.source !== "studio-bridge") return;
      let key: number | null = null;
      for (const [candidate, element] of elements.current) {
        if (element.contentWindow === event.source) key = candidate;
      }
      if (key === null) return;

      if (data.type === "ready") {
        heard.current = true;
        setSilent(false);
        autoReloads.current.set(key, readAutoReload(data));
        // A navigation inside the visible frame: the new page says again.
        if (key === activeRef.current) handlers.current.onAutoReload?.(readAutoReload(data));
        post(key, { type: "set-mode", mode: modeRef.current });
        post(key, { type: "draw", tool: drawRef.current.tool, color: drawRef.current.color });
        // Pictures in this page may offer to open large (a thumbnail's frame is never told so).
        post(key, { type: "enlarge-offer" });
        if (key === pendingRef.current) promote(key);
        else if (key === activeRef.current) handlers.current.onPageLoad?.();
        if (key === activeRef.current) pathRef.current = str(data.path, 2000) || "/";
        canChanges.current.set(key, data.changes === true);
        if (key === activeRef.current) handlers.current.onCanShowChanges?.(data.changes === true);
        return;
      }
      // Only the visible frame may drive the shell.
      if (key !== activeRef.current) return;
      if (data.type === "enlarge") {
        const picture = readEnlarge(data);
        if (picture) handlers.current.onEnlarge?.(picture);
      } else if (data.type === "changes") {
        handlers.current.onChanges?.(typeof data.count === "number" && data.count >= 0 ? Math.floor(data.count) : null);
      } else if (data.type === "drawing") {
        const count = typeof data.count === "number" ? data.count : 0;
        const drawing = data.drawing && typeof data.drawing === "object" ? (data.drawing as Drawing) : null;
        handlers.current.onDrawing?.(count, drawing);
      } else if (data.type === "navigate") {
        pathRef.current = str(data.path, 2000) || "/";
      } else if (data.type === "select") {
        handlers.current.onSelect({
          path: str(data.path, 2000) || pathRef.current,
          selector: str(data.selector, 2000),
          tag: str(data.tag, 40).toLowerCase(),
          text: str(data.text, 500),
          html: str(data.html, 4000),
          source: str(data.hint, 600),
        });
      } else if (data.type === "text-edit") {
        if (typeof data.before_text !== "string" || typeof data.after_text !== "string") return;
        handlers.current.onTextEdit({
          path: str(data.path, 2000) || pathRef.current,
          selector: str(data.selector, 2000),
          tag: str(data.tag, 40).toLowerCase(),
          before_text: data.before_text,
          after_text: data.after_text,
        });
      }
    };
    window.addEventListener("message", onMessage);
    return () => window.removeEventListener("message", onMessage);
  }, [origin, post, promote]);

  // The draw tool and colour, the same way.
  useEffect(() => {
    drawRef.current = { tool: drawTool, color: drawColor };
    for (const key of elements.current.keys()) post(key, { type: "draw", tool: drawTool, color: drawColor });
  }, [drawTool, drawColor, post]);

  useImperativeHandle(
    ref,
    () => ({
      send: (message) => post(activeRef.current, message),
    }),
    [post],
  );

  // Tell the frame(s) about mode changes; `ready` re-sends it after each load.
  useEffect(() => {
    modeRef.current = mode;
    for (const key of elements.current.keys()) post(key, { type: "set-mode", mode });
  }, [mode, post]);

  // Reload: load the same in-frame path in a fresh iframe underneath.
  useEffect(() => {
    if (reloadToken === 0) return;
    const key = nextKey.current++;
    const path = pathRef.current.startsWith("/") ? pathRef.current : `/${pathRef.current}`;
    pendingRef.current = key;
    setFrames((list) => [...list.filter((frame) => frame.key === activeRef.current), { key, src: origin + path }]);
    // A page that never reports `ready` (no bridge, slow assets) still takes over.
    const timer = window.setTimeout(() => promote(key), 4000);
    return () => window.clearTimeout(timer);
  }, [reloadToken, origin, promote]);

  // Nothing heard from a page with its own name: say so instead of leaving an empty frame.
  useEffect(() => {
    if (!ownName) return;
    heard.current = false;
    const timer = window.setTimeout(() => {
      if (!heard.current) setSilent(true);
    }, SILENT_AFTER_MS);
    return () => window.clearTimeout(timer);
  }, [ownName, attempt]);

  // A page with its own address: is this browser signed in there? If not, and the project has only
  // just opened, the tab makes the trip itself and comes back (CONTRACT.md, "A page with its own
  // address signs in by itself"). Only when the server says it answers that trip.
  useEffect(() => {
    if (!ownName || !signsInByItself) return;
    let settled = false;
    const opened = Date.now();
    const probe = new Image();
    probe.onload = () => {
      settled = true;
    };
    probe.onerror = () => {
      if (settled) return;
      settled = true;
      const key = `handoff:${origin}`;
      let last: number | null = null;
      try {
        const kept = window.sessionStorage.getItem(key);
        last = kept ? Number(kept) : null;
      } catch {
        /* no storage: treated as never tried */
      }
      if (mayHandOff(last, opened, Date.now())) {
        try {
          window.sessionStorage.setItem(key, String(Date.now()));
        } catch {
          /* the trip is made all the same */
        }
        window.location.assign(handOffUrl(origin, window.location.href));
      } else {
        setSilent(true);
      }
    };
    probe.src = hereUrl(origin, opened);
    return () => {
      settled = true;
      probe.onload = probe.onerror = null;
    };
  }, [ownName, signsInByItself, origin, attempt]);

  // Load the page again in a fresh frame (the person signed in at its address, or asked).
  const tryAgain = useCallback(() => {
    const key = nextKey.current++;
    const path = pathRef.current.startsWith("/") ? pathRef.current : `/${pathRef.current}`;
    pendingRef.current = null;
    activeRef.current = key;
    setActiveKey(key);
    setFrames([{ key, src: origin + path }]);
    setSilent(false);
    setAttempt((count) => count + 1);
  }, [origin]);

  // Back from the page's own tab: try again without being asked.
  useEffect(() => {
    if (!silent) return;
    const onVisible = () => {
      if (document.visibilityState === "visible") tryAgain();
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => document.removeEventListener("visibilitychange", onVisible);
  }, [silent, tryAgain]);

  const hint = HINTS[mode];

  return (
    <div className={`preview preview-${mode}`}>
      {frames.map((frame) => (
        <iframe
          key={frame.key}
          ref={(element) => {
            if (element) elements.current.set(frame.key, element);
            else elements.current.delete(frame.key);
          }}
          className={`preview-frame${frame.key === activeKey ? " is-active" : ""}`}
          src={frame.src}
          title={frame.key === activeKey ? `Preview of ${title}` : "Loading the updated preview"}
          aria-hidden={frame.key === activeKey ? undefined : true}
          tabIndex={frame.key === activeKey ? 0 : -1}
          sandbox={SANDBOX}
          onLoad={() => {
            // The bridge normally reports `ready` before `load`; this is the fallback.
            if (frame.key === pendingRef.current) window.setTimeout(() => promote(frame.key), 250);
          }}
        />
      ))}
      <div className="preview-ring" aria-hidden="true" />
      {silent && (
        <SilentNotice previewUrl={previewUrl} onTryAgain={tryAgain} backTo={signsInByItself ? window.location.href : null} />
      )}
      {hint && (
        <div className="preview-hint" role="status">
          {hint}
        </div>
      )}
    </div>
  );
});
