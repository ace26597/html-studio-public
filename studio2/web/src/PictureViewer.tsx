// A picture from the page, opened over the whole studio: drag to move, zoom with the wheel, a
// pinch, the buttons or the keyboard (CONTRACT.md, "A picture in the page opens large").
//
// The picture is untrusted: it is only ever the source of an <img> (an SVG shown that way never
// runs a script), and the colour behind it was checked in enlarge.ts.

import { useCallback, useEffect, useRef, useState } from "react";
import { useDialogFocus } from "./dialogFocus";
import type { LargePicture } from "./enlarge";
import { Icon } from "./icons";
import { clampView, fitView, startView, wheelFactor, zoomAt, type Size, type View } from "./zoom";

const STEP = 1.3;
const NUDGE = 60;

export function PictureViewer({ picture, onClose }: { picture: LargePicture; onClose: () => void }) {
  const dialog = useRef<HTMLDivElement>(null);
  const stage = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState<Size | null>(null);
  const [view, setView] = useState<View>({ k: 1, x: 0, y: 0 });
  const [failed, setFailed] = useState(false);
  const sizeRef = useRef<Size | null>(null);
  sizeRef.current = size;
  const latestClose = useRef(onClose);
  latestClose.current = onClose;

  // The bytes become a `data:` address, never a `blob:` one. A blob address belongs to the
  // studio's own origin: opened in a tab of its own ("Open image in new tab"), an SVG a page had
  // made would run its script as the studio. A data address has no origin and browsers do not
  // open one as a page. Its type is the one enlarge.ts accepted, not whatever the file claims.
  const [src, setSrc] = useState<string | null>(picture.blob ? null : picture.url);
  useEffect(() => {
    if (!picture.blob || !picture.type) {
      setSrc(picture.url);
      return;
    }
    let current = true;
    const type = picture.type;
    const reader = new FileReader();
    reader.onload = () => {
      const encoded = String(reader.result ?? "");
      const comma = encoded.indexOf(",");
      if (current && comma !== -1) setSrc(`data:${type};base64,${encoded.slice(comma + 1)}`);
      else if (current) setFailed(true);
    };
    reader.onerror = () => {
      if (current) setFailed(true);
    };
    reader.readAsDataURL(picture.blob);
    return () => {
      current = false;
    };
  }, [picture]);

  useDialogFocus(dialog);

  const room = useCallback((): Size => {
    const element = stage.current;
    return { w: element?.clientWidth ?? window.innerWidth, h: element?.clientHeight ?? window.innerHeight };
  }, []);

  const change = useCallback(
    (next: (view: View, picture: Size, stage: Size) => View) => {
      const current = sizeRef.current;
      if (current) setView((old) => next(old, current, room()));
    },
    [room],
  );
  const zoomBy = useCallback(
    (factor: number, at?: { x: number; y: number }) =>
      change((old, pictureSize, stageSize) =>
        zoomAt(old, old.k * factor, at?.x ?? stageSize.w / 2, at?.y ?? stageSize.h / 2, pictureSize, stageSize),
      ),
    [change],
  );
  const fit = useCallback(() => change((_old, pictureSize, stageSize) => fitView(pictureSize, stageSize)), [change]);
  const nudge = useCallback(
    (dx: number, dy: number) =>
      change((old, pictureSize, stageSize) => clampView({ ...old, x: old.x + dx, y: old.y + dy }, pictureSize, stageSize)),
    [change],
  );

  // The window changed size (a phone turned): keep the picture in sight.
  useEffect(() => {
    const onResize = () => change((old, pictureSize, stageSize) => clampView(old, pictureSize, stageSize));
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, [change]);

  // The wheel must be cancelled or the page behind scrolls and a trackpad pinch zooms the whole
  // studio; React's own wheel listener cannot cancel.
  useEffect(() => {
    const element = stage.current;
    if (!element) return;
    const onWheel = (event: WheelEvent) => {
      event.preventDefault();
      const box = element.getBoundingClientRect();
      zoomBy(wheelFactor(event.deltaY, event.ctrlKey), { x: event.clientX - box.left, y: event.clientY - box.top });
    };
    element.addEventListener("wheel", onWheel, { passive: false });
    return () => element.removeEventListener("wheel", onWheel);
  }, [zoomBy]);

  // When focus has fallen to the page body, the dialog's own key handler hears nothing.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      if (document.activeElement && document.activeElement !== document.body) return;
      event.preventDefault();
      latestClose.current();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  // One finger or the mouse drags; two fingers pinch.
  const points = useRef(new Map<number, { x: number; y: number }>());
  const spread = useRef(0);
  const onPointerDown = (event: React.PointerEvent<HTMLDivElement>) => {
    if (event.button !== 0) return;
    event.currentTarget.setPointerCapture(event.pointerId);
    points.current.set(event.pointerId, { x: event.clientX, y: event.clientY });
    spread.current = 0;
  };
  const onPointerMove = (event: React.PointerEvent<HTMLDivElement>) => {
    const was = points.current.get(event.pointerId);
    if (!was) return;
    points.current.set(event.pointerId, { x: event.clientX, y: event.clientY });
    if (points.current.size === 1) {
      nudge(event.clientX - was.x, event.clientY - was.y);
      return;
    }
    const [a, b] = [...points.current.values()];
    const distance = Math.hypot(a.x - b.x, a.y - b.y);
    const box = event.currentTarget.getBoundingClientRect();
    if (spread.current > 0 && distance > 0) {
      zoomBy(distance / spread.current, { x: (a.x + b.x) / 2 - box.left, y: (a.y + b.y) / 2 - box.top });
    }
    spread.current = distance;
  };
  const onPointerEnd = (event: React.PointerEvent<HTMLDivElement>) => {
    points.current.delete(event.pointerId);
    spread.current = 0;
  };

  const label = picture.alt ? `Picture, enlarged: ${picture.alt}` : "Picture, enlarged";
  return (
    <div
      className="picture-viewer"
      role="dialog"
      aria-modal="true"
      aria-label={label}
      tabIndex={-1}
      ref={dialog}
      onKeyDown={(event) => {
        const moves: Record<string, [number, number]> = {
          ArrowLeft: [NUDGE, 0],
          ArrowRight: [-NUDGE, 0],
          ArrowUp: [0, NUDGE],
          ArrowDown: [0, -NUDGE],
        };
        if (event.key === "Escape") onClose();
        else if (event.key === "+" || event.key === "=") zoomBy(STEP);
        else if (event.key === "-" || event.key === "_") zoomBy(1 / STEP);
        else if (event.key === "0") fit();
        else if (moves[event.key]) nudge(...moves[event.key]);
        else return;
        event.preventDefault();
        event.stopPropagation();
      }}
    >
      <div
        className={`picture-stage${size ? "" : " is-loading"}`}
        ref={stage}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerEnd}
        onPointerCancel={onPointerEnd}
        onDoubleClick={(event) => {
          const box = event.currentTarget.getBoundingClientRect();
          zoomBy(1.6, { x: event.clientX - box.left, y: event.clientY - box.top });
        }}
      >
        {failed ? (
          <p className="picture-failed">This picture could not be shown.</p>
        ) : src === null ? null : (
          <img
            src={src}
            alt={picture.alt}
            draggable={false}
            style={{
              width: size ? size.w : undefined,
              height: size ? size.h : undefined,
              transform: `translate(${view.x}px, ${view.y}px) scale(${view.k})`,
              background: picture.background ?? "#ffffff",
              visibility: size ? "visible" : "hidden",
            }}
            onError={() => setFailed(true)}
            onLoad={(event) => {
              const image = event.currentTarget;
              // The page's own measure when it gave one (a canvas is drawn at more pixels than it shows).
              const own: Size =
                picture.width && picture.height
                  ? { w: picture.width, h: picture.height }
                  : { w: image.naturalWidth || 800, h: image.naturalHeight || 600 };
              setSize(own);
              setView(startView(own, room()));
            }}
          />
        )}
      </div>
      {/* The studio's own words, above the picture: a page cannot pass a picture off as the studio. */}
      <span className="picture-from">A picture from the page</span>
      <span className="picture-scale" aria-live="polite">
        {size ? `${Math.round(view.k * 100)}%` : ""}
      </span>
      <div className="picture-bar">
        <button type="button" aria-label="Zoom out" title="Zoom out (-)" onClick={() => zoomBy(1 / STEP)}>
          −
        </button>
        <button type="button" aria-label="Zoom in" title="Zoom in (+)" onClick={() => zoomBy(STEP)}>
          <Icon name="plus" size={18} />
        </button>
        <button type="button" className="picture-fit" title="Fit to the window (0)" onClick={fit}>
          Fit
        </button>
        <button type="button" aria-label="Close" title="Close (Esc)" onClick={onClose}>
          <Icon name="x" size={18} />
        </button>
      </div>
    </div>
  );
}
