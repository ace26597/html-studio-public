// Files a request made: which of the files it added or changed get a card in the chat, what
// each card calls the file, and what the card does once the file is gone (CONTRACT.md,
// "Files in the chat, and files shown as what they are").

import type { FileChange } from "./types";

/** At most this many cards per request; the rest are counted ("and 3 more in Files"). */
export const DELIVERABLE_CAP = 8;

export type DeliverableKind =
  | "deck"
  | "workbook"
  | "document"
  | "pdf"
  | "picture"
  | "video"
  | "sound"
  | "table"
  | "text"
  | "file";

const KINDS: [RegExp, DeliverableKind][] = [
  [/\.(pptx|ppt|odp|key)$/i, "deck"],
  [/\.(xlsx|xlsm|xls|ods)$/i, "workbook"],
  [/\.(docx|doc|odt|rtf)$/i, "document"],
  [/\.pdf$/i, "pdf"],
  [/\.(png|jpe?g|gif|webp|avif|bmp|svg)$/i, "picture"],
  [/\.(mp4|m4v|webm|mov)$/i, "video"],
  [/\.(mp3|wav|m4a|ogg|oga|aiff?|flac)$/i, "sound"],
  [/\.(csv|tsv)$/i, "table"],
  [/\.(md|markdown|txt)$/i, "text"],
];

/** What kind of file a path names, by its extension. */
export function deliverableKind(path: string): DeliverableKind {
  for (const [pattern, kind] of KINDS) if (pattern.test(path)) return kind;
  return "file";
}

/** The card's plain name for the kind ("PowerPoint deck", "Table (CSV)"). */
export function kindLabel(path: string): string {
  switch (deliverableKind(path)) {
    case "deck":
      return "PowerPoint deck";
    case "workbook":
      return "Excel workbook";
    case "document":
      return "Word document";
    case "pdf":
      return "PDF";
    case "picture":
      return "Picture";
    case "video":
      return "Video";
    case "sound":
      return "Sound";
    case "table":
      return /\.tsv$/i.test(path) ? "Table (TSV)" : "Table (CSV)";
    case "text":
      return "Text";
    default:
      return "File";
  }
}

/** The studio's own and working folders at the top of a project: never a deliverable. */
const WORKING_TOP = new Set(["sources", "checks", "attachments", "tools", "research", "state", "template", "notes"]);

/** The page's own code (and scripts that build things): never a deliverable, wherever it is. */
const CODE = /\.(html?|css|scss|sass|less|js|mjs|cjs|jsx|ts|tsx|map|py|sh|lock)$/i;

/** Kinds that count outside `files/` when the request added them. SVG does not: there it is
 * nearly always one of the page's own icons. */
const ELSEWHERE: ReadonlySet<DeliverableKind> = new Set(["deck", "workbook", "document", "pdf", "video", "sound", "table"]);
const RASTER = /\.(png|jpe?g|gif|webp|avif|bmp)$/i;

function inWorkingPlace(path: string): boolean {
  const parts = path.split("/");
  if (parts.length > 1 && WORKING_TOP.has(parts[0])) return true;
  // The pictures a deck or document is built from (by convention), not something it made.
  // files/img holds the pictures a deck is built from; files/diagrams the drawings render_diagram saves for a page.
  if (parts.length > 2 && parts[0] === "files" && (parts[1] === "img" || parts[1] === "diagrams")) return true;
  for (let i = 0; i < parts.length; i++) {
    const part = parts[i];
    if (!part || part.startsWith(".") || part === "node_modules" || part === "__pycache__") return true;
    // A folder `render_document` writes beside a file: `files/deck.pptx.preview/…`.
    if (i < parts.length - 1 && /\.preview$/i.test(part)) return true;
  }
  return false;
}

/** Whether one changed file earns a card (the rule, without the cap). */
export function isDeliverable(change: FileChange): boolean {
  const { path, status } = change;
  if (status !== "added" && status !== "modified") return false;
  if (!path || inWorkingPlace(path) || CODE.test(path)) return false;
  if (path.startsWith("files/")) return true;
  if (status !== "added") return false;
  const kind = deliverableKind(path);
  return ELSEWHERE.has(kind) || (kind === "picture" && RASTER.test(path));
}

/** `media/launch.poster.jpg`, or `media/launch.jpg` beside `media/launch.mp4`: a video's poster. */
function isPoster(path: string, all: readonly FileChange[]): boolean {
  if (/\.poster\.jpe?g$/i.test(path)) return true;
  if (!/\.jpe?g$/i.test(path)) return false;
  const stem = path.replace(/\.jpe?g$/i, "");
  return all.some((other) => other.path !== path && deliverableKind(other.path) === "video" && other.path.replace(/\.[^./]+$/, "") === stem);
}

export interface Deliverables {
  /** The files that get a card, in order: `files/` first, then the rest, each in the checkpoint's order. */
  shown: FileChange[];
  /** How many more earned a card than are shown. */
  more: number;
}

/**
 * The cards for one request: the files its own checkpoints (kind `agent`) added or changed that
 * pass the rule, minus the files attached to the message itself, capped.
 */
export function deliverablesOf(
  checkpoints: readonly { kind: string; files: readonly FileChange[] }[],
  attached: readonly string[] = [],
  cap: number = DELIVERABLE_CAP,
): Deliverables {
  // The last word on each path wins (a request has one checkpoint, but replays may hold more).
  const latest = new Map<string, FileChange>();
  for (const checkpoint of checkpoints) {
    if (checkpoint.kind !== "agent") continue;
    for (const change of checkpoint.files) {
      if (!change || typeof change.path !== "string") continue;
      latest.delete(change.path);
      latest.set(change.path, change);
    }
  }
  const all = [...latest.values()];
  const skip = new Set(attached);
  const passing = all.filter((change) => isDeliverable(change) && !skip.has(change.path) && !isPoster(change.path, all));
  const ordered = [
    ...passing.filter((change) => change.path.startsWith("files/")),
    ...passing.filter((change) => !change.path.startsWith("files/")),
  ];
  return { shown: ordered.slice(0, cap), more: Math.max(0, ordered.length - cap) };
}

/** What the studio knows of a file now: its size, gone, or not known (the listing has not come). */
export type FileNow = { state: "here"; size: number } | { state: "gone" } | { state: "unknown" };

/**
 * Look a path up in the project's file listing. A listing that hit its cap cannot say a file is
 * gone, only that it was not among the first ones.
 */
export function fileNow(listing: ReadonlyMap<string, number> | null, path: string, complete: boolean): FileNow {
  if (!listing) return { state: "unknown" };
  const size = listing.get(path);
  if (size !== undefined) return { state: "here", size };
  return complete ? { state: "gone" } : { state: "unknown" };
}

/** "and 3 more in Files" — null when nothing was left out. */
export function moreLine(more: number): string | null {
  return more > 0 ? `and ${more} more in Files` : null;
}

/** The card's second line: the kind, then the size now (or that it is gone), then new or changed. */
export function cardMeta(change: FileChange, now: FileNow, formatSize: (bytes: number) => string): string {
  const parts = [kindLabel(change.path)];
  if (now.state === "here") parts.push(formatSize(now.size));
  if (now.state === "gone") parts.push("no longer in the project");
  else parts.push(change.status === "added" ? "new" : "changed");
  return parts.join(" · ");
}
