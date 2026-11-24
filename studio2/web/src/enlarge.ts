// The bridge's `enlarge` message, checked before the shell shows anything (CONTRACT.md,
// "A picture in the page opens large"). Pure.

export interface LargePicture {
  /** The picture's bytes, or … */
  blob: Blob | null;
  /** What kind of picture the bytes are (`image/png`, `image/svg+xml`, …); null with a `url`. */
  type: string | null;
  /** … its http(s) address, when the page could not read the bytes. */
  url: string | null;
  alt: string;
  /** The colour it sat on in the page, or null when the page gave none the shell accepts. */
  background: string | null;
  /** Its own size in CSS pixels, or null: then the loaded picture says. */
  width: number | null;
  height: number | null;
}

export const MAX_PICTURE_BYTES = 25 * 1024 * 1024;
/** The kinds of file shown. Anything else a page sends as a "picture" is refused. */
const PICTURE_TYPE = /^(image\/(?:png|jpeg|gif|webp|avif|bmp|svg\+xml))(?:\s*;.*)?$/;
const COLOUR = /^rgba?\(\s*[\d.]+%?(\s*[,\s/]\s*[\d.]+%?){2,3}\s*\)$/;
const MAX_SIDE = 30000;

function side(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) && value >= 1 && value <= MAX_SIDE ? value : null;
}

/** The message as a picture to show, or null when it is not one the shell accepts. */
export function readEnlarge(data: Record<string, unknown>): LargePicture | null {
  if (data.type !== "enlarge") return null;
  const blob = typeof Blob !== "undefined" && data.blob instanceof Blob ? data.blob : null;
  let url: string | null = null;
  if (!blob && typeof data.url === "string" && data.url.length <= 4000 && /^https?:\/\//i.test(data.url)) {
    url = data.url;
  }
  if (blob && (blob.size === 0 || blob.size > MAX_PICTURE_BYTES)) return null;
  const type = blob ? (PICTURE_TYPE.exec(blob.type.toLowerCase())?.[1] ?? null) : null;
  if (blob && !type) return null;
  if (!blob && !url) return null;
  const background =
    typeof data.background === "string" && data.background.length <= 60 && COLOUR.test(data.background)
      ? data.background
      : null;
  const width = side(data.width);
  const height = side(data.height);
  return {
    blob,
    type,
    url,
    alt: typeof data.alt === "string" ? data.alt.slice(0, 300) : "",
    background,
    width: width && height ? width : null,
    height: width && height ? height : null,
  };
}
