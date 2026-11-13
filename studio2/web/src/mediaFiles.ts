// Pictures, video and sound in the Files panel: which files they are, and the
// poster picture `render_video` writes beside a video (`media/<name>.jpg`).

export type MediaKind = "image" | "video" | "audio";

const KINDS: [RegExp, MediaKind][] = [
  // SVG is left out on purpose: it opens as text, so it can be edited.
  [/\.(png|jpe?g|gif|webp|avif|ico|bmp)$/i, "image"],
  [/\.(mp4|m4v|webm|mov)$/i, "video"],
  [/\.(mp3|wav|m4a|ogg|oga|aiff?|flac)$/i, "audio"],
];

/** The kind of a picture, video or sound file, or null for anything else. */
export function mediaKind(path: string): MediaKind | null {
  for (const [pattern, kind] of KINDS) if (pattern.test(path)) return kind;
  return null;
}

/** `media/launch.mp4` → `media/launch.jpg` when that file is in the project, else null. */
export function findPoster(path: string, files: readonly { path: string }[]): string | null {
  const dot = path.lastIndexOf(".");
  if (dot <= path.lastIndexOf("/")) return null;
  const poster = `${path.slice(0, dot)}.jpg`;
  return files.some((file) => file.path === poster) ? poster : null;
}
