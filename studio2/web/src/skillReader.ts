// Reading any skill, explaining it, and starting from one (CONTRACT.md, the section of that name):
// what the reader shows and offers, and the "Make my own copy" form. All pure.

import { ApiError } from "./api";
import { formatBytes, relativeTime } from "./format";
import { nameProblem } from "./library";
import { plural } from "./libraryPick";
import type { SkillFile, SkillFull } from "./types";

/** The instructions file: listed first, as "Instructions". */
export const INSTRUCTIONS_PATH = "SKILL.md";

/** The longest name a skill may have, and the longest question for "explain". */
export const MAX_SKILL_NAME = 60;
export const MAX_ASK_TEXT = 2_000;

// ---- what the reader shows -----------------------------------------------------------------

const WHERE: Record<string, string> = {
  studio: "Comes with the studio",
  yours: "Yours, kept in this studio",
  machine: "From this computer’s skills folder",
  plugin: "From a plugin",
};

/** Where a skill comes from, in a few words: its `origin` when the index has one. */
export function skillWhere(skill: Pick<SkillFull, "source" | "origin">): string {
  if (skill.origin) return skill.origin;
  return WHERE[skill.source] ?? "In the library";
}

/**
 * The facts line: files, scripts, size, and how often it was picked and used.
 * `["4 files", "1 script", "12 KB", "picked 5 times, used 3 · last used 2 days ago"]`, joined by " · ".
 */
export function skillFactsLine(
  skill: Pick<SkillFull, "files" | "scripts" | "size" | "picked" | "used" | "last_used">,
  now: number = Date.now(),
): string {
  const parts = [
    plural(skill.files.length, "file"),
    skill.scripts.length ? plural(skill.scripts.length, "script") : "no scripts",
    formatBytes(skill.size),
  ];
  if (!skill.picked) {
    parts.push("never picked yet");
  } else {
    parts.push(`picked ${plural(skill.picked, "time")}, used ${skill.used}`);
    if (skill.last_used) {
      const when = relativeTime(skill.last_used, now);
      if (when) parts.push(`last used ${when}`);
    }
  }
  return parts.join(" · ");
}

export interface SkillFileRow {
  path: string;
  /** "Instructions" for SKILL.md, else the path. */
  label: string;
  size: number;
  kind: string;
  script: boolean;
  instructions: boolean;
}

/** The file list: the instructions first, then every other file by path. */
export function skillFileRows(files: readonly SkillFile[]): SkillFileRow[] {
  const rows = files.map((file) => ({
    path: file.path,
    label: file.path === INSTRUCTIONS_PATH ? "Instructions" : file.path,
    size: file.size,
    kind: file.kind,
    script: Boolean(file.script),
    instructions: file.path === INSTRUCTIONS_PATH,
  }));
  return rows.sort((a, b) =>
    a.instructions !== b.instructions ? (a.instructions ? -1 : 1) : a.path < b.path ? -1 : a.path > b.path ? 1 : 0,
  );
}

/** The front matter's other keys, as `[key, value]`, in the order written; empty values left out. */
export function frontEntries(front: Record<string, string> | null | undefined): [string, string][] {
  return Object.entries(front ?? {}).filter(([key, value]) => key && String(value ?? "").trim() !== "").map(([key, value]) => [key, String(value)]);
}

export const CUT_NOTE = "Only the first 200,000 characters are shown here. Export the skill to read all of it.";

export function otherFileNote(path: string): string {
  return `“${path}” is a kind of file the studio does not show here. Export the skill to open it.`;
}

// ---- what the dialog offers ----------------------------------------------------------------

export interface SkillOffers {
  explain: boolean;
  export: boolean;
  copy: boolean;
  edit: boolean;
  askChange: boolean;
  delete: boolean;
}

/** Explain and Export always; a copy for someone else's; Edit, Ask for a change and Delete for one's own. None of the changes on a shared studio. */
export function skillOffers(skill: Pick<SkillFull, "yours">, shared: boolean): SkillOffers {
  const mine = Boolean(skill.yours);
  return {
    explain: true,
    export: true,
    copy: !mine && !shared,
    edit: mine && !shared,
    askChange: mine && !shared,
    delete: mine && !shared,
  };
}

/** What a row in the Library's list offers: Open and Export for any skill in the index, Delete for one's own (not on a shared studio). */
export function skillRowOffers(
  row: { exportId?: string; action?: { name: string } },
  shared: boolean,
): { open: boolean; export: boolean; delete: boolean } {
  const inIndex = Boolean(row.exportId);
  return { open: inIndex, export: inIndex, delete: Boolean(row.action) && !shared };
}

/** A skill's name in the person's library: `yours:<name>` → `<name>`. */
export function ownName(skill: Pick<SkillFull, "id" | "name">): string {
  if (skill.name) return skill.name;
  const colon = skill.id.indexOf(":");
  return colon === -1 ? skill.id : skill.id.slice(colon + 1);
}

// ---- asking the agent ----------------------------------------------------------------------

/** What is wrong with the text of an ask, or null. `change` needs words; `explain` may be empty. */
export function askProblem(about: "explain" | "change", text: string): string | null {
  const said = text.trim();
  if (about === "change" && !said) return "Say what should change.";
  if (said.length > MAX_ASK_TEXT) {
    const what = about === "explain" ? "The question" : "What should change";
    return `${what} can be at most ${MAX_ASK_TEXT.toLocaleString("en-US")} characters.`;
  }
  return null;
}

/** The body of `POST /api/library/skills/ask`: `text` only when something was written. */
export function askBody(id: string, about: "explain" | "change", text: string): { id: string; about: "explain" | "change"; text?: string } {
  const said = text.trim();
  return said ? { id, about, text: said } : { id, about };
}

// ---- making one's own copy -----------------------------------------------------------------

/** `<name>-mine`, shortened so that it stays within the 60 characters a name may have. */
export function defaultCopyName(name: string): string {
  const suffix = "-mine";
  return `${name.slice(0, MAX_SKILL_NAME - suffix.length)}${suffix}`;
}

export type CopyRefusal =
  /** A skill of the person's has the name: replace it, or change the name. */
  | { code: "name_taken"; name: string; message: string }
  /** One of the studio's own skills has the name: change it. */
  | { code: "name_taken_by_studio"; name: string; message: string }
  /** The skill holds scripts and the box was not ticked. */
  | { code: "scripts_need_confirmation"; message: string };

export interface CopyState {
  name: string;
  scriptsAccepted: boolean;
  /** "Replace it" was chosen for the name that is taken. */
  replace: boolean;
  /** Scripts the server named on a 409 that the skill's own list did not have. */
  extraScripts: string[];
  refusal: CopyRefusal | null;
  /** Any other refusal, as a sentence. */
  error: string | null;
  busy: boolean;
}

export type CopyAction =
  | { type: "name"; name: string }
  | { type: "tick"; on: boolean }
  | { type: "replace" }
  | { type: "submit" }
  | { type: "refused"; error: unknown }
  | { type: "done" }
  /** Start again for a skill (the form is opened). */
  | { type: "reset"; skillName: string };

export function newCopyState(skillName: string): CopyState {
  return { name: defaultCopyName(skillName), scriptsAccepted: false, replace: false, extraScripts: [], refusal: null, error: null, busy: false };
}

function scriptsFrom(details: unknown): string[] {
  const scripts = (details as { scripts?: unknown } | null)?.scripts;
  return Array.isArray(scripts) ? scripts.filter((item): item is string => typeof item === "string") : [];
}

/** Before the form is first opened. */
export const emptyCopyState: CopyState = newCopyState("");

export function copyReducer(state: CopyState, action: CopyAction): CopyState {
  switch (action.type) {
    case "reset":
      return newCopyState(action.skillName);
    case "name": {
      // A new name forgets "Replace it" and the refusal about the old name.
      const nameRefusal = state.refusal && state.refusal.code !== "scripts_need_confirmation";
      return {
        ...state,
        name: action.name,
        replace: false,
        refusal: nameRefusal ? null : state.refusal,
        error: null,
      };
    }
    case "tick":
      return {
        ...state,
        scriptsAccepted: action.on,
        refusal: state.refusal?.code === "scripts_need_confirmation" ? null : state.refusal,
        error: null,
      };
    case "replace":
      return { ...state, replace: true, refusal: null, error: null };
    case "submit":
      return { ...state, busy: true, error: null };
    case "done":
      return { ...state, busy: false };
    case "refused": {
      const error = action.error;
      const base = { ...state, busy: false };
      if (error instanceof ApiError) {
        const name = state.name.trim();
        if (error.code === "name_taken") return { ...base, replace: false, refusal: { code: "name_taken", name, message: error.message } };
        if (error.code === "name_taken_by_studio") {
          return { ...base, replace: false, refusal: { code: "name_taken_by_studio", name, message: error.message } };
        }
        if (error.code === "scripts_need_confirmation") {
          return {
            ...base,
            scriptsAccepted: false,
            extraScripts: scriptsFrom(error.details),
            refusal: { code: "scripts_need_confirmation", message: error.message },
          };
        }
        return { ...base, error: error.message };
      }
      return { ...base, error: error instanceof Error && error.message ? error.message : "Something went wrong." };
    }
  }
}

export interface CopyPlan {
  /** Every script the copy would bring, the skill's and any the server named. */
  scripts: string[];
  nameIssue: string | null;
  /** The name is one of the person's: offer "Replace it" or a new name. */
  askReplace: boolean;
  ready: boolean;
  /** Why the button does not work yet, in a sentence. */
  waiting: string | null;
  /** The button's words. */
  label: string;
  request: { from: string; name: string; allow_scripts?: boolean; replace?: boolean };
}

export function copyPlan(skill: Pick<SkillFull, "id" | "scripts">, state: CopyState): CopyPlan {
  const name = state.name.trim();
  const scripts = [...new Set([...skill.scripts, ...state.extraScripts])];
  const refusal = state.refusal;
  let nameIssue = nameProblem(name, MAX_SKILL_NAME, "skill");
  if (!nameIssue && refusal?.code === "name_taken_by_studio" && refusal.name === name) {
    nameIssue = "One of the studio’s own skills has this name. Give your copy another name.";
  }
  const askReplace = !nameIssue && refusal?.code === "name_taken" && refusal.name === name && !state.replace;
  const waiting = nameIssue
    ? "Give the copy a name that can be used."
    : askReplace
      ? "You already have a skill with this name: replace it, or change the name."
      : scripts.length > 0 && !state.scriptsAccepted
        ? "Tick the box to accept its scripts."
        : null;
  const request: CopyPlan["request"] = { from: skill.id, name };
  if (scripts.length > 0 && state.scriptsAccepted) request.allow_scripts = true;
  if (state.replace) request.replace = true;
  return {
    scripts,
    nameIssue,
    askReplace,
    ready: waiting === null && !state.busy,
    waiting,
    label: state.replace ? "Replace my skill with this copy" : "Make my own copy",
    request,
  };
}

/** The id the new copy has in the library. */
export function copiedId(name: string): string {
  return `yours:${name}`;
}
