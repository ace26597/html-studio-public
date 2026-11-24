// The Files panel shows the person's files first. What the studio and the agent keep for their
// own work (saved sources, check results, research notes, what was attached to messages) is
// one step away, behind "Working files". Pure.

/** Top-level folders that hold the studio's and the agent's working files. */
export const WORKING_FOLDERS = ["sources", "checks", "research", "attachments", "notes", "state"] as const;

export function isWorkingFile(path: string): boolean {
  const top = path.split("/", 1)[0];
  return path.includes("/") && (WORKING_FOLDERS as readonly string[]).includes(top);
}

/** The files to list, and how many working files there are. */
export function splitWorking<T extends { path: string }>(files: readonly T[], showWorking: boolean): { shown: T[]; working: number } {
  let working = 0;
  const shown: T[] = [];
  for (const file of files) {
    const own = isWorkingFile(file.path);
    if (own) working++;
    if (!own || showWorking) shown.push(file);
  }
  return { shown, working };
}
