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

/** Reads `auto_reload` from a bridge `ready` message; anything but a literal `false` means true. */
export function readAutoReload(data: Record<string, unknown>): boolean {
  return data.auto_reload !== false;
}
