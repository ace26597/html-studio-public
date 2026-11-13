// Templates: built-in starting points and the person's saved ones. Pure helpers.

import type { FileEntry, Template } from "./types";

export function isYours(template: Template): boolean {
  return template.source === "yours";
}

/** "Start from" (built in) and "Your templates", each in the order the server sent. */
export function groupTemplates(list: readonly Template[]): { builtIn: Template[]; yours: Template[] } {
  return { builtIn: list.filter((item) => !isYours(item)), yours: list.filter(isYours) };
}

const KIND_LABEL: Record<string, string> = {
  page: "Page",
  app: "App",
  deck: "PowerPoint deck",
  workbook: "Excel workbook",
  document: "Word document",
};

/** The kind in plain words. A built-in template without a kind is an App or a Page. */
export function templateKindLabel(template: Pick<Template, "kind" | "app">): string {
  if (template.kind && KIND_LABEL[template.kind]) return KIND_LABEL[template.kind];
  return template.app ? "App" : "Page";
}

/** Where a saved template came from: "from <project>", "imported", or nothing. */
export function templateOrigin(template: Template): string | null {
  if (template.imported) return "imported";
  if (template.from_project) return `from ${template.from_project}`;
  return null;
}

/** The preview URL with a cache-busting query from `created_at`, or null. */
export function templatePreviewSrc(template: Template): string | null {
  if (!template.preview) return null;
  const join = template.preview.includes("?") ? "&" : "?";
  return template.created_at ? `${template.preview}${join}v=${encodeURIComponent(template.created_at)}` : template.preview;
}

/** What starting from this template does, in one line (saved templates only). */
export function templateStartLine(template: Template): string | null {
  if (!isYours(template)) return null;
  if (template.mode === "copy" || template.kind === "app") return "Starts as a copy of this app.";
  return "Starts with an empty page. The assistant follows this template’s look and the way it was built, with your content.";
}

// ---- saving a template ------------------------------------------------------------------

const OFFICE_FOR_TEMPLATE: [RegExp, "deck" | "workbook" | "document"][] = [
  [/\.pptx$/i, "deck"],
  [/\.xlsx$/i, "workbook"],
  [/\.docx$/i, "document"],
];

export interface SaveChoice {
  /** "whole" or a project file path. */
  value: string;
  label: string;
  kind: "page" | "app" | "deck" | "workbook" | "document";
  file: string | null;
}

/**
 * What can be saved: the whole project (an app when it has a back end, else a page),
 * then each .pptx / .xlsx / .docx in the project (not inside generated preview folders).
 */
export function saveChoices(files: readonly Pick<FileEntry, "path">[], hasBackEnd: boolean): SaveChoice[] {
  const whole: SaveChoice = hasBackEnd
    ? { value: "whole", label: "This whole app", kind: "app", file: null }
    : { value: "whole", label: "This whole page", kind: "page", file: null };
  const office: SaveChoice[] = [];
  for (const entry of files) {
    if (/\.preview\//.test(entry.path) || entry.path.startsWith("template/")) continue;
    const match = OFFICE_FOR_TEMPLATE.find(([pattern]) => pattern.test(entry.path));
    if (!match) continue;
    const kind = match[1];
    const name = entry.path.split("/").pop() ?? entry.path;
    office.push({ value: entry.path, label: `${name} · ${KIND_LABEL[kind]}`, kind, file: entry.path });
  }
  office.sort((a, b) => a.value.localeCompare(b.value));
  return [whole, ...office];
}

/** The body for `POST /projects/{id}/template`. */
export function saveTemplateBody(
  name: string,
  description: string,
  choice: SaveChoice,
): { name: string; description?: string; kind?: string; file?: string } {
  const body: { name: string; description?: string; kind?: string; file?: string } = { name: name.trim() };
  if (description.trim()) body.description = description.trim();
  if (choice.file) body.file = choice.file;
  else body.kind = choice.kind;
  return body;
}

// ---- importing a template file ------------------------------------------------------------

export type ImportState =
  | { phase: "idle" }
  | { phase: "uploading"; file: File; confirmed: boolean }
  /** The server asked: this template has code that would run on this machine. */
  | { phase: "confirm"; file: File; message: string };

export type ImportAction =
  | { type: "pick"; file: File }
  | { type: "needs-confirmation"; message: string }
  | { type: "confirm" }
  | { type: "cancel" }
  | { type: "done" };

export const idleImport: ImportState = { phase: "idle" };

export function importReducer(state: ImportState, action: ImportAction): ImportState {
  switch (action.type) {
    case "pick":
      return state.phase === "idle" ? { phase: "uploading", file: action.file, confirmed: false } : state;
    case "needs-confirmation":
      // Only an unconfirmed upload can be asked to confirm; a confirmed one that is refused again just ends.
      return state.phase === "uploading" && !state.confirmed
        ? { phase: "confirm", file: state.file, message: action.message }
        : idleImport;
    case "confirm":
      return state.phase === "confirm" ? { phase: "uploading", file: state.file, confirmed: true } : state;
    case "cancel":
    case "done":
      return idleImport;
  }
}
