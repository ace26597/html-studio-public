// Context from this computer (CONTRACT.md, "Context from this computer"): the pure parts of the
// Add dialog, the chips above the message box and the Context tab, kept here so they can be tested
// without a browser.

import { formatBytes } from "./attach";
import type { IconName } from "./icons";
import type {
  ContextAddressFound,
  ContextFileFound,
  ContextFolderFound,
  ContextFound,
  ContextInspection,
  ContextItem,
  ContextKind,
} from "./types";

export const CONTEXT_LIMIT = 12;
export const LABEL_MAX = 60;
export const NOTE_MAX = 500;
/** Chips shown above the message box before "+N". */
export const CHIPS_SHOWN = 3;

export const FIELD_LABEL = "A folder, a file or an address";
export const FIELD_EXAMPLE = "~/dev/my-app or http://localhost:3000";

export function kindIcon(kind: ContextKind): IconName {
  return kind === "folder" ? "folder" : kind === "file" ? "file" : "globe";
}

export function kindName(kind: ContextKind): string {
  return kind === "folder" ? "Folder" : kind === "file" ? "File" : "Address";
}

/** The full path or address: what a chip's tip and the panel show. */
export function contextWhere(item: Pick<ContextItem, "path" | "address">): string {
  return item.path ?? item.address ?? "";
}

/** The tip on a chip: the full path or address (the label is already on the chip). */
export function chipTip(item: Pick<ContextItem, "kind" | "path" | "address">): string {
  return `${kindName(item.kind)}: ${contextWhere(item)}`;
}

function count(value: number, one: string, many: string): string {
  return `${value.toLocaleString("en")} ${value === 1 ? one : many}`;
}

function folderSummary(found: ContextFolderFound): string {
  const parts: string[] = [];
  if (found.framework) parts.push(found.framework.label);
  if (found.git) parts.push(found.git.branch ? `git branch ${found.git.branch}` : "git, no branch checked out");
  if (found.files === null) {
    parts.push("not counted (it took too long)");
  } else {
    parts.push(`${found.partial ? "more than " : ""}${count(found.files, "file", "files")}`);
    if (found.bytes !== null) parts.push(formatBytes(found.bytes));
  }
  const line = parts.join(" · ");
  return line.charAt(0).toUpperCase() + line.slice(1);
}

function addressSummary(found: ContextAddressFound): string {
  if (!found.reachable) return "Did not answer";
  const status = found.status !== null ? `Answers (${found.status})` : "Answers";
  return found.title ? `${status} · “${found.title}”` : status;
}

/** One line for what the inspection found, e.g. "Next.js · git branch main · 2,310 files · 46.0 MB". */
export function foundSummary(kind: ContextKind, found: ContextFound | null | undefined): string {
  if (!found) return "";
  if (kind === "folder") return folderSummary(found as ContextFolderFound);
  if (kind === "file") {
    const bytes = (found as ContextFileFound).bytes;
    return typeof bytes === "number" ? formatBytes(bytes) : "";
  }
  return addressSummary(found as ContextAddressFound);
}

/** The names at the top of a folder, as one line, with how many more there are. */
export function topLine(found: ContextFound | null | undefined): string {
  const folder = found as ContextFolderFound | null | undefined;
  if (!folder?.top?.length) return "";
  const more = folder.top_more ?? 0;
  return `${folder.top.join("  ")}${more > 0 ? `  and ${more.toLocaleString("en")} more` : ""}`;
}

/** The first three items, and how many are left for "+N". */
export function chipList<T>(items: readonly T[], shown = CHIPS_SHOWN): { shown: T[]; more: number } {
  return { shown: items.slice(0, shown), more: Math.max(0, items.length - shown) };
}

/** The tab's name: "Context", or "Context · 3". */
export function contextTabLabel(count: number): string {
  return count > 0 ? `Context · ${count}` : "Context";
}

/** What is wrong with a label and note before they are sent, or null. */
export function labelNoteProblem(label: string, note: string, { labelRequired }: { labelRequired: boolean }): string | null {
  const name = label.trim();
  if (labelRequired && !name) return "Give it a name.";
  if (name.length > LABEL_MAX) return `The name can be at most ${LABEL_MAX} characters.`;
  if (note.trim().length > NOTE_MAX) return `“What is it?” can be at most ${NOTE_MAX} characters.`;
  return null;
}

/** The list with this item put in (in its place when it is already there, else at the end). */
export function putContextItem(list: readonly ContextItem[], item: ContextItem): ContextItem[] {
  const strip = ({ id, kind, path, address, label, note, added_at, found }: ContextItem): ContextItem => ({
    id,
    kind,
    path,
    address,
    label,
    note,
    added_at,
    found,
  });
  const clean = strip(item);
  return list.some((entry) => entry.id === item.id)
    ? list.map((entry) => (entry.id === item.id ? clean : entry))
    : [...list, clean];
}

export function dropContextItem(list: readonly ContextItem[], itemId: string): ContextItem[] {
  return list.filter((entry) => entry.id !== itemId);
}

/** Changes when items are added or removed (not when one is renamed): when the panel reads states again. */
export function contextIdsKey(list: readonly Pick<ContextItem, "id">[] | null | undefined): string {
  return (list ?? []).map((item) => item.id).join(",");
}

// ---- the Add dialog -----------------------------------------------------------------------

export interface AddContextState {
  value: string;
  /** The value the inspection describes (trimmed), or null. */
  checkedValue: string | null;
  inspection: ContextInspection | null;
  checking: boolean;
  adding: boolean;
  /** The server's message for a failed check or add. */
  error: string | null;
  label: string;
  note: string;
}

export type AddContextAction =
  | { type: "value"; value: string }
  | { type: "check-start" }
  | { type: "check-done"; value: string; inspection: ContextInspection }
  | { type: "check-failed"; message: string }
  | { type: "label"; value: string }
  | { type: "note"; value: string }
  | { type: "add-start" }
  | { type: "add-failed"; message: string };

export const emptyAddContext: AddContextState = {
  value: "",
  checkedValue: null,
  inspection: null,
  checking: false,
  adding: false,
  error: null,
  label: "",
  note: "",
};

export function addContextReducer(state: AddContextState, action: AddContextAction): AddContextState {
  switch (action.type) {
    case "value": {
      // The result always describes what is in the field: a different value clears it.
      const stale = state.checkedValue !== null && action.value.trim() !== state.checkedValue;
      return stale
        ? { ...state, value: action.value, checkedValue: null, inspection: null, label: "", error: null }
        : { ...state, value: action.value, error: null };
    }
    case "check-start":
      return { ...state, checking: true, error: null };
    case "check-done": {
      // An answer for a value that is no longer in the field is dropped.
      if (action.value !== state.value.trim()) return { ...state, checking: false };
      return {
        ...state,
        checking: false,
        checkedValue: action.value,
        inspection: action.inspection,
        label: action.inspection.label,
        error: null,
      };
    }
    case "check-failed":
      return { ...state, checking: false, checkedValue: null, inspection: null, label: "", error: action.message };
    case "label":
      return { ...state, label: action.value, error: null };
    case "note":
      return { ...state, note: action.value, error: null };
    case "add-start":
      return { ...state, adding: true, error: null };
    case "add-failed":
      return { ...state, adding: false, error: action.message };
  }
}

/** True when the result shown describes the value in the field. */
export function isChecked(state: AddContextState): boolean {
  return state.inspection !== null && state.checkedValue === state.value.trim();
}

/** Why Add is not possible now, or null. */
export function addBlocker(state: AddContextState): string | null {
  if (!state.value.trim()) return "Type a folder’s full path or an address.";
  if (!isChecked(state)) return "Check it first.";
  if (state.inspection?.refused) return state.inspection.refused;
  return labelNoteProblem(state.label, state.note, { labelRequired: true });
}

export function canCheck(state: AddContextState): boolean {
  return Boolean(state.value.trim()) && !state.checking && !state.adding;
}

export function canAdd(state: AddContextState): boolean {
  return !state.checking && !state.adding && addBlocker(state) === null;
}

/** What Enter in the field does: check what is typed, or add what was checked. */
export function enterAction(state: AddContextState): "check" | "add" | null {
  if (isChecked(state)) return canAdd(state) ? "add" : null;
  return canCheck(state) ? "check" : null;
}

/** The body of `POST /api/projects/{id}/context`: the label only when it differs from the default. */
export function addContextRequest(state: AddContextState): { value: string; label?: string; note?: string } {
  const body: { value: string; label?: string; note?: string } = { value: state.checkedValue ?? state.value.trim() };
  const label = state.label.trim();
  if (label && label !== state.inspection?.label) body.label = label;
  const note = state.note.trim();
  if (note) body.note = note;
  return body;
}

/** The body of a PATCH: only what changed (empty when nothing did). */
export function editContextPatch(item: ContextItem, label: string, note: string): { label?: string; note?: string } {
  const patch: { label?: string; note?: string } = {};
  if (label.trim() !== item.label) patch.label = label.trim();
  if (note.trim() !== item.note) patch.note = note.trim();
  return patch;
}

/** The library button in the top bar: its count is data sources plus context items. */
export function libraryButtonText(dataSources: number, context: number): { count: number; label: string; tip: string } {
  const parts: string[] = [];
  if (context) parts.push(`${context} context ${context === 1 ? "item" : "items"}`);
  if (dataSources) parts.push(`${dataSources} data ${dataSources === 1 ? "source" : "sources"} attached`);
  const count = dataSources + context;
  return {
    count,
    label: count ? `Context, library and data sources (${parts.join(", ")})` : "Context, library and data sources",
    tip: count ? `Library · ${parts.join(", ")}` : "Context, library and data sources",
  };
}

/** Whether two lists hold the same items with the same names and notes (no new state needed). */
export function sameContext(a: readonly ContextItem[] | undefined, b: readonly ContextItem[]): boolean {
  if (!a || a.length !== b.length) return false;
  return a.every(
    (item, index) =>
      item.id === b[index].id && item.label === b[index].label && item.note === b[index].note,
  );
}
