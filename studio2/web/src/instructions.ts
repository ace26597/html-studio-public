// Instructions for a project (CONTRACT.md, "Instructions for a project"): what the person always
// wants here, in their own words. Pure helpers; the dialog is InstructionsDialog.tsx.

import type { Project } from "./types";
import type { MenuEntry } from "./ui";

/** The server's limit, counted on the tidied text. */
export const INSTRUCTIONS_MAX = 4000;

export const INSTRUCTIONS_TITLE = "Instructions";

/** The two texts the dialog holds: for this project, and for every project of the person's. */
export type InstructionsPart = "project" | "everywhere";
export const PART_LABEL: Record<InstructionsPart, string> = { project: "This project", everywhere: "Every project" };
export const PART_QUESTION: Record<InstructionsPart, string> = {
  project: "What should the agent always do here? Write it as you would say it.",
  everywhere: "What holds for all your work? Where it is, the currency, how you like things written.",
};
export const EVERYWHERE_EXAMPLES = [
  "My solar businesses are in Maharashtra, India.",
  "Use rupees, with lakh and crore.",
  "Write plainly. I read on a laptop.",
].join("\n");

export const INSTRUCTIONS_EXAMPLES = [
  "Always use Indian number format (1,20,000) and rupees.",
  "Every report ends with a list of the sources used.",
  "Keep the tone plain. No exclamation marks.",
  "I read these on a laptop: wide tables are fine.",
].join("\n");

export const DISCARD_INSTRUCTIONS = "Discard the changes to the instructions?";

/** The text as the server stores it: one kind of line end, no spaces at line ends, trimmed. */
export function tidyInstructions(text: string): string {
  return text
    .replace(/\r\n?/g, "\n")
    .split("\n")
    .map((line) => line.replace(/[ \t]+$/, ""))
    .join("\n")
    .trim();
}

/** Characters as the server counts them (an emoji is one, not two). */
export function instructionsLength(text: string): number {
  return Array.from(tidyInstructions(text)).length;
}

export function instructionsProblem(text: string): string | null {
  const length = instructionsLength(text);
  if (length <= INSTRUCTIONS_MAX) return null;
  return `Instructions can be ${INSTRUCTIONS_MAX.toLocaleString("en-US")} characters at most; this is ${length.toLocaleString("en-US")}.`;
}

export function instructionsCount(text: string): { text: string; over: boolean } {
  const length = instructionsLength(text);
  return {
    text: `${length.toLocaleString("en-US")} / ${INSTRUCTIONS_MAX.toLocaleString("en-US")}`,
    over: length > INSTRUCTIONS_MAX,
  };
}

export function hasInstructions(project: Pick<Project, "instructions"> | null | undefined): boolean {
  return Boolean(project?.instructions?.trim());
}

/** Something to save: the tidied text differs from what the project has. */
export function instructionsChanged(saved: string | undefined, text: string): boolean {
  return tidyInstructions(text) !== tidyInstructions(saved ?? "");
}

/**
 * The studio's server keeps instructions. One started before they existed leaves the field out of
 * the project (the interface is rebuilt without a restart): there the dialog is not offered,
 * because a Save would look kept and be dropped.
 */
export function instructionsKept(project: Pick<Project, "instructions"> | null | undefined): boolean {
  return typeof project?.instructions === "string";
}

/** The same entry in both of the project's menus; none on a server that does not keep them. */
export function instructionsEntries(project: Pick<Project, "instructions"> | null, onSelect: () => void): MenuEntry[] {
  if (!instructionsKept(project)) return [];
  return [{ label: "Instructions…", icon: "note", hint: hasInstructions(project) ? "On" : undefined, onSelect }];
}

/** The button beside "Add context" under the message box. `everywhere`: the text for every project, if any. */
export function instructionsButton(
  project: Pick<Project, "instructions"> | null,
  everywhere?: string | null,
): { label: string; tip: string; on: boolean } {
  const here = hasInstructions(project);
  const all = !!everywhere && everywhere.trim() !== "";
  const on = here || all;
  return {
    label: on ? "Instructions (on)" : "Instructions",
    tip: here && all
      ? "The agent follows your instructions for every project and for this one"
      : here
        ? "This project has instructions the agent follows every time"
        : all
          ? "The agent follows your instructions for every project"
          : "Instructions (what you always want, here or in every project)",
    on,
  };
}

/** Under the box: when the text applies. */
export function instructionsNote(running: boolean, part: InstructionsPart = "project"): string {
  const always =
    part === "everywhere"
      ? "The agent is given this with every request in every project. A project's own instructions come first where they differ."
      : "The agent is given this with every request in this project, and only in this project.";
  return running
    ? `${always} The request running now keeps the earlier text; a change applies from the next one.`
    : `${always} A change applies from the next request.`;
}

export function instructionsSavedMessage(text: string, part: InstructionsPart = "project"): string {
  const which = part === "everywhere" ? "Instructions for every project" : "Instructions";
  return tidyInstructions(text)
    ? `${which} saved. The agent follows them from the next request.`
    : `${which} cleared. The agent is told they no longer apply.`;
}
