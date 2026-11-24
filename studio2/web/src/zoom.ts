// A picture opened large (CONTRACT.md, "A picture in the page opens large"): where it sits and
// how far it is zoomed. Pure, so the rules are testable.

export interface View {
  /** Scale: 1 is the picture's own size. */
  k: number;
  /** Where the picture's top left corner is, in the stage. */
  x: number;
  y: number;
}

export interface Size {
  w: number;
  h: number;
}

export const MAX_ZOOM = 4;
/** Fitted under this, a picture is too small to read: it starts at READABLE_START instead. */
export const READABLE_BELOW = 0.6;
export const READABLE_START = 0.8;
/** Room kept around a fitted picture, and under it for the buttons. */
export const PAD = 16;
export const BAR = 76;
/** This much of the picture always stays in the stage, so it cannot be lost. */
export const KEEP = 64;

/** The scale at which the whole picture shows, never above its own size. */
export function fitScale(picture: Size, stage: Size): number {
  if (picture.w <= 0 || picture.h <= 0) return 1;
  const k = Math.min((stage.w - 2 * PAD) / picture.w, (stage.h - PAD - BAR) / picture.h, 1);
  return Math.max(k, 0.02);
}

/** The whole picture, centred in the room above the buttons. */
export function fitView(picture: Size, stage: Size): View {
  const k = fitScale(picture, stage);
  return {
    k,
    x: (stage.w - picture.w * k) / 2,
    y: Math.max(PAD, (stage.h - BAR - picture.h * k) / 2),
  };
}

/** How it opens: fitted, unless that leaves it too small to read (a wide diagram on a phone). */
export function startView(picture: Size, stage: Size): View {
  const fit = fitView(picture, stage);
  if (fit.k >= READABLE_BELOW) return fit;
  return { k: READABLE_START, x: PAD, y: PAD };
}

/** Keep part of the picture in the stage whatever the drag. */
export function clampView(view: View, picture: Size, stage: Size): View {
  const w = picture.w * view.k;
  const h = picture.h * view.k;
  return {
    k: view.k,
    x: Math.min(stage.w - KEEP, Math.max(KEEP - w, view.x)),
    y: Math.min(stage.h - KEEP, Math.max(KEEP - h, view.y)),
  };
}

/** Zoom to `to`, keeping the point (cx, cy) of the stage on the same spot of the picture. */
export function zoomAt(view: View, to: number, cx: number, cy: number, picture: Size, stage: Size): View {
  const least = Math.min(fitScale(picture, stage), READABLE_START);
  const k = Math.min(MAX_ZOOM, Math.max(least, to));
  const ratio = k / view.k;
  return clampView({ k, x: cx - (cx - view.x) * ratio, y: cy - (cy - view.y) * ratio }, picture, stage);
}

/** One wheel event as a zoom factor. A pinch on a trackpad arrives as a wheel with ctrlKey and small steps. */
export function wheelFactor(deltaY: number, pinch: boolean): number {
  const step = Math.max(-240, Math.min(240, deltaY));
  return Math.exp(-step * (pinch ? 0.01 : 0.002));
}
