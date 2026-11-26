// Small formatting helpers. All pure.

export function relativeTime(iso: string, now: number = Date.now()): string {
  const then = Date.parse(iso);
  if (!Number.isFinite(then)) return "";
  const seconds = Math.max(0, Math.round((now - then) / 1000));
  if (seconds < 45) return "just now";
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours} h ago`;
  const days = Math.round(hours / 24);
  if (days === 1) return "yesterday";
  if (days < 7) return `${days} days ago`;
  const date = new Date(then);
  const sameYear = date.getFullYear() === new Date(now).getFullYear();
  return date.toLocaleDateString(undefined, {
    day: "numeric",
    month: "short",
    ...(sameYear ? {} : { year: "numeric" }),
  });
}

export function formatDuration(ms: number): string {
  const seconds = Math.round(ms / 1000);
  if (seconds < 1) return "under 1s";
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  const rest = seconds % 60;
  if (minutes < 60) return rest ? `${minutes}m ${rest}s` : `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  return `${hours}h ${minutes % 60}m`;
}

export function formatCost(usd: number): string {
  if (usd === 0) return "$0";
  if (usd < 0.01) return "under $0.01";
  return `$${usd.toFixed(usd < 10 ? 2 : 1)}`;
}

export const ESTIMATE_TIP = "Not a charge: this ran on your Claude subscription";
export const PENDING_TIP = "The gateway writes its spend log a few seconds after a request; the price is read from it.";
export const UNPRICED_TIP = "The route reported no price for these requests. They are not counted as free.";
export const CALCULATED_TIP =
  "Worked out from token counts and a price list (by LiteLLM or the agent program). This provider does not report a cost per request.";
/** Whose rates a backup figure used, in words. */
function backupRates(backup: { rates: string; as_of: string | null }): string {
  const list = backup.as_of ? `the studio's price list of ${backup.as_of}` : "the studio's price list";
  if (backup.rates === "gateway") return "the gateway's own price for the model";
  if (backup.rates === "both") return `the gateway's own prices and ${list}`;
  return list;
}

/** Why a figure says "calculated by the studio": the backup calculation, never a bill. */
export function backupTip(backup: {
  requests: number | null;
  rates: string;
  as_of: string | null;
  unpriced_models?: string[];
  partial?: boolean;
}): string {
  if (backup.requests === null) {
    const left = backup.unpriced_models?.length ? ` Not in this figure, for want of a rate: ${backup.unpriced_models.join(", ")}.` : "";
    const cut = backup.partial ? " The request ended without saying what it used, so it cost at least this." : "";
    return `The gateway's spend log could not be read, so the studio worked this out from the request's token counts, each model at its own rate (${backupRates(backup)}). List prices, not a bill: a route that uses a dearer provider charges more.${left}${cut}`;
  }
  const many = backup.requests === 1 ? "1 request came" : `${backup.requests} requests came`;
  return `${many} back with no cost, so the studio priced ${backup.requests === 1 ? "it" : "them"} from token counts at ${backupRates(backup)}. A backup figure, not a bill.`;
}

export const RECOVERED_TIP = "Found in the gateway's spend log by session and time, not by a tag sent with the turn.";

/**
 * What a finished turn's cost line says, or null for no cost at all.
 *
 * - `cost_usd` null (a gateway, or unknown): nothing.
 * - `cost_estimated` true (subscription): "≈ $1.02 at API prices", with a tip
 *   that it is not a charge. Never a bare dollar figure.
 * - `cost_estimated` false: the plain amount, "$0.84".
 * - the flag missing (events from before Step 2): nothing, because there is no
 *   way to tell an estimate from a charge.
 */
export function costLabel(
  usage:
    | {
        cost_usd: number | null;
        cost_estimated?: boolean;
        cost_pending?: boolean;
        unpriced_requests?: number;
        cost_source?: string | null;
        cost_basis?: string | null;
        backup?: { requests: number | null; usd: number; rates: string; as_of: string | null; unpriced_models?: string[]; partial?: boolean } | null;
      }
    | null
    | undefined,
): { text: string; tip: string | null } | null {
  const usd = usage?.cost_usd;
  if (typeof usd !== "number" || !Number.isFinite(usd) || usd < 0) {
    if (usage?.cost_pending) return { text: "cost pending", tip: PENDING_TIP };
    const unpriced = usage?.unpriced_requests;
    if (typeof unpriced === "number" && unpriced > 0) {
      return { text: `${unpriced} ${unpriced === 1 ? "request" : "requests"} unpriced`, tip: UNPRICED_TIP };
    }
    return null;
  }
  if (usage?.cost_estimated === true) {
    const amount = usd > 0 && usd < 0.01 ? "under $0.01" : `≈ ${formatCost(usd)}`;
    return { text: `${amount} at API prices`, tip: ESTIMATE_TIP };
  }
  if (usage?.cost_estimated === false) {
    const unpriced = usage.unpriced_requests;
    const note = typeof unpriced === "number" && unpriced > 0 ? ` + ${unpriced} unpriced` : "";
    if (usage.cost_basis === "backup" && usage.backup) {
      const least = usage.backup.partial ? "at least " : "";
      return { text: `${least}${formatCost(usd)} calculated by the studio${note}`, tip: backupTip(usage.backup) };
    }
    const calculated = usage.cost_basis === "calculated";
    const tip = calculated ? CALCULATED_TIP : usage.cost_source === "recovered" ? RECOVERED_TIP : note ? UNPRICED_TIP : null;
    return { text: `${formatCost(usd)}${calculated ? " calculated" : ""}${note}`, tip };
  }
  return null;
}

export function formatCount(value: number): string {
  if (value < 1000) return String(value);
  if (value < 1_000_000) return `${(value / 1000).toFixed(value < 10_000 ? 1 : 0).replace(/\.0$/, "")}k`;
  return `${(value / 1_000_000).toFixed(1).replace(/\.0$/, "")}M`;
}

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(bytes < 10 * 1024 ? 1 : 0)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function truncate(text: string, max: number): string {
  const clean = text.replace(/\s+/g, " ").trim();
  return clean.length > max ? `${clean.slice(0, max - 1).trimEnd()}…` : clean;
}

/** `A` / `added` / `M` / `modified` / … → one of four stable words. */
export function fileStatus(status: string): "added" | "modified" | "deleted" | "renamed" {
  const letter = (status || "M").trim().charAt(0).toUpperCase();
  if (letter === "A" || letter === "?" || letter === "C") return "added";
  if (letter === "D") return "deleted";
  if (letter === "R") return "renamed";
  return "modified";
}

export function prettyInput(input: unknown): string {
  if (input == null) return "";
  if (typeof input === "string") return input;
  try {
    return JSON.stringify(input, null, 2);
  } catch {
    return String(input);
  }
}

/** The last segment of a workspace path. */
export function baseName(path: string): string {
  const parts = path.split("/");
  return parts[parts.length - 1] || path;
}

const IMAGE_EXT = /\.(png|jpe?g|gif|webp|avif|svg|bmp|ico)$/i;

export function isImagePath(path: string): boolean {
  return IMAGE_EXT.test(path);
}

export type AppTone = "running" | "starting" | "stopped" | "crashed" | "none";

/** The app pill's words. `crashed` names the exit code when there is one. */
export function appStateLabel(app: { state: string; exitCode: number | null }): { label: string; tone: AppTone } {
  switch (app.state) {
    case "running":
      return { label: "Running", tone: "running" };
    case "starting":
      return { label: "Starting", tone: "starting" };
    case "stopped":
      return { label: "Stopped", tone: "stopped" };
    case "crashed":
      return { label: app.exitCode === null ? "Crashed" : `Crashed · exit ${app.exitCode}`, tone: "crashed" };
    case "external":
      return { label: "Your server", tone: "running" };
    case "none":
      return { label: "No app", tone: "none" };
    default:
      return { label: app.state || "Unknown", tone: "none" };
  }
}

/**
 * Where the shell loads a workspace file: `<preview_url>__studio/files/<path>`,
 * served by the studio from disk even while the project's app owns the URL.
 * Each path segment is encoded; a leading slash is ignored.
 */
export function projectFileUrl(previewUrl: string, path: string): string {
  const base = previewUrl.endsWith("/") ? previewUrl : `${previewUrl}/`;
  const encoded = path
    .replace(/^\/+/, "")
    .split("/")
    .map(encodeURIComponent)
    .join("/");
  return new URL(`__studio/files/${encoded}`, base).href;
}

/**
 * The short words a version marker adds when large files were left out of it, and its tooltip
 * lines (CONTRACT.md, "Files too large for versions"). `null` when none were.
 */
export function leftOutNote(
  leftOut: { files: { path: string; size: number }[]; count: number } | undefined,
): { label: string; detail: string } | null {
  if (!leftOut || leftOut.count <= 0) return null;
  const label = `${leftOut.count} large ${leftOut.count === 1 ? "file" : "files"} not in versions`;
  const lines = leftOut.files.map((file) => `${file.path} (${formatBytes(file.size)})`);
  const rest = leftOut.count - leftOut.files.length;
  if (rest > 0) lines.push(`and ${rest} more`);
  const detail = `Too large to save in versions, so undo cannot bring ${leftOut.count === 1 ? "it" : "them"} back. Kept on disk as ${leftOut.count === 1 ? "it is" : "they are"}:\n${lines.join("\n")}`;
  return { label, detail };
}

const CHECKPOINT_LABEL: Record<string, string> = {
  agent: "Saved version",
  user: "Saved your edit",
  restore: "Restored a version",
  init: "Page created",
};

/** The words on a checkpoint marker in the thread. Uploads (`Upload <path>`) say what was uploaded. */
export function checkpointLabel(kind: string, message: string): string {
  const upload = /^Upload (.+)$/.exec(message ?? "");
  if (upload) return `Uploaded ${baseName(upload[1].trim())}`;
  return CHECKPOINT_LABEL[kind] ?? "Saved version";
}
