/** The name the studio offers for a page nobody has named yet, or null. It is shown, never applied. */
export function offeredName(project: { name: string; name_suggestion?: string | null } | null): string | null {
  if (!project || project.name !== "Untitled") return null;
  const offered = (project.name_suggestion ?? "").trim();
  return offered && offered !== "Untitled" ? offered : null;
}
