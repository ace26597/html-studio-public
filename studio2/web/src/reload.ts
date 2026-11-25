// When the shell reloads the preview iframe. Pure, so the rule is testable.

export type ReloadReason = "files_changed" | "app_running" | "app_stopped" | "inline_edit" | "restore";

/**
 * Pages that update themselves (`<meta name="studio-reload" content="off">`,
 * reported by the bridge as `ready.auto_reload: false`) are not reloaded when
 * project files change. Everything else still reloads them: the app coming up
 * (or going away, so its status page shows), an inline edit, a restore.
 * A page that never said (`autoReload` undefined) counts as `true`.
 */
export function shouldReloadPreview(reason: ReloadReason, autoReload: boolean | undefined): boolean {
  if (reason === "files_changed") return autoReload !== false;
  return true;
}

/**
 * Top-level folders whose files are never page content: a change there does not reload the preview.
 * `notes/` (the project notes) and `research/` (research notes, the saved check_site result) are the
 * agent's working files. `sources/` holds the saved text of sources, which no page shows; its
 * `index.json` is the one exception (a page may read it for its source list). Not here, on purpose:
 * `checks/` (the checked-numbers appendix shows the saved check), `attachments/` (a page may show a
 * picture the person attached) and `state/` (an app's own data, which the studio never reports).
 */
export const NOT_PAGE_FOLDERS = ["notes", "research", "sources"] as const;

/** Files inside NOT_PAGE_FOLDERS that a page may still read. */
export const PAGE_READS = ["sources/index.json"] as const;

/** Whether one changed path could change what the page shows. */
export function isPageFile(path: string): boolean {
  if (!path.includes("/") || (PAGE_READS as readonly string[]).includes(path)) return true;
  return !(NOT_PAGE_FOLDERS as readonly string[]).includes(path.split("/", 1)[0]);
}

/** Whether a `files_changed` event touched anything the page could show (an empty list counts as yes). */
export function changesPage(paths: readonly string[] | undefined): boolean {
  if (!paths || !paths.length) return true;
  return paths.some(isPageFile);
}

/**
 * A reload drops every mark on the page (the bridge draws them in the frame), and positions on
 * a changed page would not mean the same thing. So while the person is drawing, or holds a
 * drawing that was not sent, reloads wait; the caller runs one once the drawing is gone.
 */
export function drawingHoldsReload(drawing: boolean, marks: number): boolean {
  return drawing || marks > 0;
}

/** The note shown while a reload waits for a drawing, since the page shown is no longer the newest. */
export function heldReloadNote(drawing: boolean, marks: number): string {
  return marks > 0 || !drawing
    ? "The page has changed. It refreshes once your drawing is sent or removed."
    : "The page has changed. It refreshes when you press Done.";
}

/** Reads `auto_reload` from a bridge `ready` message; anything but a literal `false` means true. */
export function readAutoReload(data: Record<string, unknown>): boolean {
  return data.auto_reload !== false;
}
