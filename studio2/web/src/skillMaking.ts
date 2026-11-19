// Skills a person makes (CONTRACT.md, "Skills a person makes"): the requests the interface sends,
// the name of the project a description starts, what an import still needs, and how the approval
// card for `save_skill` reads its detail. All pure, apart from the injected calls in `startDescribedSkill`.

import type { TurnInput } from "./api";
import { nameProblem } from "./library";
import { formatBytes } from "./format";
import type { Project, SkillApprovalInfo, SkillImportPreview } from "./types";

/** The contract's wording. A test pins these sentences: change the contract first. */
export const DESCRIBE_LEAD = "Make a new skill for my library from this description (use the make-a-skill skill):";
export const FROM_WORK_LEAD =
  "Make a skill from how this was made (use the make-a-skill skill), so I can get the same kind of result again for a different subject.";
export const NOTE_LEAD = "What changes each time:";

export const DESCRIBE_EXAMPLE =
  "Every Monday, get last week’s orders from the sales database, total them by region, compare with the week before, make a one-page summary with a chart, and check every total against the database before finishing.";

/** The most characters of the description a project's name carries. */
const NAME_WORDS = 40;

/** `Skill: <the first words>`, cut at a word and without a dangling comma. */
export function skillProjectName(description: string): string {
  const flat = description.trim().replace(/\s+/g, " ");
  const letters = Array.from(flat);
  if (letters.length <= NAME_WORDS) return `Skill: ${flat.replace(/[\s,;:.–—-]+$/u, "")}`;
  const head = letters.slice(0, NAME_WORDS).join("");
  const wordEnds = letters[NAME_WORDS] === " ";
  const space = head.lastIndexOf(" ");
  const cut = wordEnds || space <= 0 ? head : head.slice(0, space);
  return `Skill: ${cut.replace(/[\s,;:.–—-]+$/u, "")}`;
}

export function describeRequest(description: string): string {
  return `${DESCRIBE_LEAD}\n\n${description.trim()}`;
}

export function fromWorkRequest(note: string): string {
  const said = note.trim();
  return said ? `${FROM_WORK_LEAD}\n\n${NOTE_LEAD} ${said}` : FROM_WORK_LEAD;
}

// ---- starting from a description -----------------------------------------------------------

export interface DescribeCalls {
  createProject: (options: { name: string }) => Promise<Project>;
  upload: (projectId: string, file: File, dir: string) => Promise<{ path: string }>;
  startTurn: (projectId: string, input: TurnInput) => Promise<unknown>;
}

/** What a failed Start already did, so that Start again carries on instead of doing it twice. */
export interface DescribeProgress {
  project: Project | null;
  uploaded: Map<File, string>;
}

export function newDescribeProgress(): DescribeProgress {
  return { project: null, uploaded: new Map() };
}

/** Makes the project, uploads the examples, sends the one request. Answers the project's id. */
export async function startDescribedSkill(
  calls: DescribeCalls,
  description: string,
  files: readonly File[],
  progress: DescribeProgress,
): Promise<string> {
  const project = progress.project ?? (await calls.createProject({ name: skillProjectName(description) }));
  progress.project = project;
  for (const file of files) {
    if (progress.uploaded.has(file)) continue;
    try {
      const { path } = await calls.upload(project.id, file, "attachments");
      progress.uploaded.set(file, path);
    } catch (cause) {
      const reason = cause instanceof Error && cause.message ? cause.message : "It could not be uploaded.";
      throw new Error(`“${file.name}” wasn’t added, so nothing was sent: ${reason}`);
    }
  }
  await calls.startTurn(project.id, {
    text: describeRequest(description),
    connection: project.connection,
    model: project.model,
    attachments: files.map((file) => progress.uploaded.get(file) as string),
  });
  return project.id;
}

// ---- importing a file ----------------------------------------------------------------------

export type NameChoice = "replace" | "rename" | null;

export interface ImportChoices {
  scriptsAccepted: boolean;
  nameChoice: NameChoice;
  newName: string;
}

export interface SkillImportOptions {
  replace?: boolean;
  allowScripts?: boolean;
  name?: string;
}

export interface ImportPlan {
  /** Which question about the name the dialog asks. */
  nameAsk: "none" | "replace" | "rename";
  /** The new-name field is showing. */
  renaming: boolean;
  /** What is wrong with the typed name, once something is typed. */
  nameIssue: string | null;
  ready: boolean;
  /** Why "Add to my library" is not available yet, in a sentence. */
  waiting: string | null;
  options: SkillImportOptions;
}

export function importPlan(preview: SkillImportPreview, choices: ImportChoices): ImportPlan {
  const nameAsk = preview.taken_by_studio ? "rename" : preview.exists ? "replace" : "none";
  const renaming = nameAsk === "rename" || (nameAsk === "replace" && choices.nameChoice === "rename");
  const typed = choices.newName.trim();
  let nameIssue: string | null = null;
  if (renaming && typed) {
    nameIssue =
      typed === preview.name
        ? preview.taken_by_studio
          ? "One of the studio’s own skills has this name."
          : "A skill in your library already has this name."
        : nameProblem(typed, 60, "skill");
  }
  const scriptsWait = preview.scripts.length > 0 && !choices.scriptsAccepted;
  const nameWait =
    nameAsk === "replace" && choices.nameChoice === null
      ? "Choose what to do about the name."
      : renaming && (!typed || nameIssue)
        ? "Give the skill another name."
        : null;
  const waiting = nameWait ?? (scriptsWait ? "Tick the box to accept its scripts." : null);
  const options: SkillImportOptions = {};
  if (preview.scripts.length > 0 && choices.scriptsAccepted) options.allowScripts = true;
  if (renaming) options.name = typed;
  else if (nameAsk === "replace" && choices.nameChoice === "replace") options.replace = true;
  return { nameAsk, renaming, nameIssue, ready: waiting === null, waiting, options };
}

export function scriptsLine(count: number): string {
  return `This skill brings ${count} ${count === 1 ? "script" : "scripts"} the agent may run`;
}

// ---- the approval card ---------------------------------------------------------------------

export function isSaveSkill(tool: string): boolean {
  return /(^|__)save_skill$/.test(tool);
}

export interface SkillApprovalParts {
  description: string;
  body: string;
  files: { path: string; size: string }[];
  /** On a replace: the sentence naming the files that stay from the earlier version. */
  kept: string | null;
}

const WHEN = "When it is used: ";
const INSTRUCTIONS = "\n\nInstructions:\n";
const FILES = "\n\nFiles:\n";
const KEPT = "\n\nFiles kept from the version it replaces: ";
const FILE_LINE = /^(\S.*) \((\d[\d.,]* ?[A-Za-z]+)\)$/;

/**
 * The server writes the detail as `When it is used: …`, `Instructions:` and the body, then `Files:`
 * with one `path (size)` a line, then the files kept on a replace. Any other shape is shown whole
 * as the instructions: nothing the person is asked to approve is ever left out.
 */
export function parseSkillApproval(detail: string): SkillApprovalParts {
  const text = detail.replace(/\r\n/g, "\n").trim();
  const whole: SkillApprovalParts = { description: "", body: text, files: [], kept: null };
  const split = text.indexOf(INSTRUCTIONS);
  if (!text.startsWith(WHEN) || split === -1) return whole;
  const description = text.slice(WHEN.length, split).trim();
  let body = text.slice(split + INSTRUCTIONS.length);
  let kept: string | null = null;
  const keptAt = body.lastIndexOf(KEPT);
  if (keptAt !== -1 && !body.slice(keptAt + 2).includes("\n")) {
    kept = body.slice(keptAt + 2);
    body = body.slice(0, keptAt);
  }
  let files: SkillApprovalParts["files"] = [];
  const filesAt = body.lastIndexOf(FILES);
  if (filesAt !== -1) {
    const lines = body.slice(filesAt + FILES.length).split("\n").map((line) => line.match(FILE_LINE));
    if (lines.every(Boolean)) {
      files = lines.map((line) => ({ path: (line as RegExpMatchArray)[1], size: (line as RegExpMatchArray)[2] }));
      body = body.slice(0, filesAt);
    }
  }
  return { description, body: body.trim(), files, kept };
}

// ---- the approval card, from the event's structured `skill` ---------------------------------

/** How many kept files are named before "and N more". */
export const KEPT_NAMED = 12;

export const REPLACES_WORDS = "replaces the one in the skill";

export interface ApprovalFileRow {
  path: string;
  size: string;
  /** "script", "replaces the one in the skill". */
  marks: string[];
}

/** Every file the call writes, its size from bytes, marked when it is a script or replaces one. */
export function approvalFileRows(files: SkillApprovalInfo["files"]): ApprovalFileRow[] {
  return (files ?? []).map((file) => {
    const marks: string[] = [];
    if (file.script) marks.push("script");
    if (file.replaces) marks.push(REPLACES_WORDS);
    return { path: file.path, size: formatBytes(file.size), marks };
  });
}

/** `Files kept from the version it replaces: a, b, … and N more.`, or null. */
export function keptSentence(kept: readonly string[] | null | undefined): string | null {
  if (!kept?.length) return null;
  const named = kept.slice(0, KEPT_NAMED).join(", ");
  const more = kept.length - KEPT_NAMED;
  return `Files kept from the version it replaces: ${named}${more > 0 ? ` and ${more} more` : ""}.`;
}

/** The kept files that are scripts, every one named, or null. */
export function keptScriptsSentence(keptScripts: readonly string[] | null | undefined): string | null {
  if (!keptScripts?.length) return null;
  const one = keptScripts.length === 1;
  return `${one ? "This script is" : "These scripts are"} already in the skill and ${one ? "stays" : "stay"} as ${one ? "it is" : "they are"}: ${keptScripts.join(", ")}.`;
}

/** "new" or "replaces the one in the skill", then the line count: `new · 12 lines`. */
export function scriptFacts(script: Pick<SkillApprovalInfo["scripts"][number], "replaces" | "lines">): string {
  const lines = `${script.lines} ${script.lines === 1 ? "line" : "lines"}`;
  return `${script.replaces ? REPLACES_WORDS : "new"} · ${lines}`;
}
