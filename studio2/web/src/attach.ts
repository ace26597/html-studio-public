// Step 3k: helpers for bringing an app by its folder (CONTRACT.md, "Bring an app by its folder").
// Pure functions, so the dialog and the project's marker say the same thing and can be tested.

import type { AttachMode, AttachRecord, AttachRequest, Inspection } from "./types";

/** `/Users/sam/dev/site` → `~/dev/site` (also `/home/<name>/…`); anything else unchanged. */
export function shortPath(path: string): string {
  return path.replace(/^\/(?:Users|home)\/[^/]+(?=\/|$)/, "~");
}

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  const units = ["KB", "MB", "GB"];
  let value = bytes / 1024;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value >= 10 ? Math.round(value) : value.toFixed(1)} ${units[unit]}`;
}

/** What the project's marker says: where it came from and which way in. */
export function attachLabel(attach: AttachRecord): string {
  const where = shortPath(attach.path);
  return attach.mode === "in_place" ? `Editing ${where} in place` : `Copy of ${where}`;
}

/** The longer line under the marker: the branch, the commit and what was left behind. */
export function attachDetail(attach: AttachRecord): string {
  const parts: string[] = [];
  if (attach.mode === "copy") {
    if (attach.kind === "git" && attach.started_from) {
      parts.push(`From ${attach.branch ?? "a detached commit"} at ${attach.started_from.slice(0, 7)}`);
      if (attach.studio_branch) parts.push(`changes go on ${attach.studio_branch}`);
    } else {
      parts.push("A copy of the folder's files");
    }
    const left = attach.uncommitted.length;
    if (left && !attach.included_uncommitted) parts.push(`${left} uncommitted change${left === 1 ? "" : "s"} not copied`);
    const skipped = attach.env_skipped ?? [];
    if (skipped.length) {
      parts.push(`${skipped.length} key file${skipped.length === 1 ? "" : "s"} not copied (${skipped.map((s) => s.name).join(", ")})`);
    }
    parts.push("the original is not changed");
  } else {
    parts.push("Changes are written to that folder; the studio keeps its own versions");
    if (attach.address) parts.push(`shows ${attach.address}`);
  }
  return parts.join(" · ");
}

/**
 * The key files the person ticked that the copy did not take (`env_skipped`), in plain words:
 * each one, why, and what to do about it. Null when every ticked file was copied.
 */
export function envSkippedNote(attach: AttachRecord): string | null {
  const skipped = attach.env_skipped ?? [];
  if (!skipped.length) return null;
  const where = shortPath(attach.path);
  const head =
    skipped.length === 1
      ? `The key file ${skipped[0].name} was not copied: ${skipped[0].reason}.`
      : `${skipped.length} key files were not copied: ${skipped.map((s) => `${s.name} (${s.reason})`).join(", ")}.`;
  const advice: string[] = [];
  if (skipped.some((s) => s.reason.includes("link"))) advice.push(`replace the link in ${where} with a real file`);
  if (skipped.some((s) => s.reason.includes("not found"))) advice.push(`check that it is still in ${where}`);
  const fix = advice.length ? `${advice.join(", or ")}, then make the copy again` : "make the copy again";
  return `${head} The app may not start without ${skipped.length === 1 ? "it" : "them"}. If it needs ${skipped.length === 1 ? "it" : "them"}, ${fix}.`;
}

/** The default project name for a folder and a way in. */
export function defaultAttachName(inspection: Pick<Inspection, "name">, mode: AttachMode): string {
  return `${inspection.name} (${mode === "copy" ? "copy" : "in place"})`;
}

/** Mirrors the server's address rule: http on this computer, with a port. → error sentence or null. */
export function addressProblem(text: string): string | null {
  const value = text.trim();
  if (!value) return null;
  let url: URL;
  try {
    url = new URL(value.includes("://") ? value : `http://${value}`);
  } catch {
    return "That is not an address.";
  }
  const host = url.hostname.replace(/^\[|\]$/g, "").toLowerCase();
  if (url.protocol !== "http:" || !["127.0.0.1", "localhost", "::1"].includes(host) || !url.port) {
    return "Only a server on this computer can be shown: http://localhost:<port> or http://127.0.0.1:<port>.";
  }
  return null;
}

export interface AttachChoices {
  mode: AttachMode;
  name: string;
  run: string;
  address: string;
  envFiles: string[];
  includeUncommitted: boolean;
}

/** Why Create is not possible yet, or null. */
export function attachBlocker(inspection: Inspection | null, choices: AttachChoices): string | null {
  if (!inspection) return "Check the folder first.";
  const refused = choices.mode === "copy" ? inspection.refused.copy : inspection.refused.in_place;
  if (refused) return refused;
  if (choices.mode === "in_place") return addressProblem(choices.address);
  return null;
}

/** The request body for the choices made in the dialog. */
export function attachRequest(inspection: Inspection, choices: AttachChoices): AttachRequest {
  const body: AttachRequest = { path: inspection.path, mode: choices.mode, run: choices.run.trim() };
  const name = choices.name.trim();
  if (name) body.name = name;
  if (choices.mode === "in_place" && choices.address.trim()) body.address = choices.address.trim();
  if (choices.mode === "copy") {
    body.env_files = choices.envFiles.filter((file) => inspection.env_files.includes(file));
    if (inspection.git && choices.includeUncommitted) body.include_uncommitted = true;
  }
  return body;
}

/** One line on the size of what a copy takes. */
export function sizeLine(inspection: Inspection): string {
  const { files, bytes, history_bytes, partial } = inspection.size;
  const count = `${partial ? "more than " : ""}${files.toLocaleString("en")} file${files === 1 ? "" : "s"}`;
  const history = inspection.git && history_bytes ? `, history ${formatBytes(history_bytes)}` : "";
  return `${count}, ${formatBytes(bytes)}${history}`;
}
