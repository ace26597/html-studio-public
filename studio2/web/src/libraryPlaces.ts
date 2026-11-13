/** More places to look for skills and connectors (CONTRACT.md). */

export interface LibraryPlacesInfo {
  always: string[];
  skill_dirs: { path: string; found: boolean; skills: number }[];
  connector_files: { path: string; found: boolean; connectors: number }[];
  from_env: { skill_dirs: boolean; connector_files: boolean };
}

/** What one place holds, in words. */
export function placeNote(found: boolean, count: number, one: string, many: string, missing: string): string {
  if (!found) return missing;
  return `${count} ${count === 1 ? one : many} found`;
}

/** The list with a path added (once) or removed. */
export function withPath(list: string[], path: string, add: boolean): string[] {
  const clean = path.trim();
  if (!clean) return list;
  return add ? (list.includes(clean) ? list : [...list, clean]) : list.filter((item) => item !== clean);
}
