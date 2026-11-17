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
