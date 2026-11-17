// Make it live (CONTRACT.md, "Ship: make it live"): pure helpers for the ship panel.

import type { ShipCard, ShipCommandResult, ShipMethod, ShipRecipe, ShipRecord, ShipResult, ShipStepEvent, ShipView } from "./types";

export const METHOD_LABEL: Record<ShipMethod, string> = {
  branch: "Leave a branch in the original's repository",
  // The same names the server puts on the card (ship.py METHODS).
  merge: "Merge into the original's checked-out branch (fast-forward only)",
  files: "Copy the changed files into the original folder",
  none: "Nothing to move: the folder is edited in place",
};

export const METHOD_SHORT: Record<ShipMethod, string> = {
  branch: "Leave a branch",
  merge: "Merge",
  files: "Copy files",
  none: "In place",
};

/** The recipe as the editor holds it: one command or pattern per line. */
export interface RecipeForm {
  checks: string;
  method: ShipMethod;
  after: string;
  address: string;
  protected: string;
  back: string;
  timeout: string;
  notes: string;
}

const lines = (text: string): string[] =>
  text
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);

export function recipeForm(recipe: ShipRecipe): RecipeForm {
  return {
    checks: recipe.checks.join("\n"),
    method: recipe.method,
    after: recipe.after.join("\n"),
    address: recipe.address ?? "",
    protected: recipe.protected.join("\n"),
    back: (recipe.back ?? []).join("\n"),
    timeout: String(recipe.timeout),
    notes: recipe.notes,
  };
}

/** → the recipe to save, or the sentence that says what is wrong. */
export function formRecipe(form: RecipeForm): { recipe: ShipRecipe | null; error: string | null } {
  const address = form.address.trim();
  if (address && !/^https?:\/\/\S+$/.test(address)) {
    return { recipe: null, error: "The address to check must start with http:// or https://." };
  }
  const timeout = Number(form.timeout.trim() || "600");
  if (!Number.isFinite(timeout) || timeout < 10 || timeout > 3600) {
    return { recipe: null, error: "The time limit is a number of seconds between 10 and 3600." };
  }
  const lists = { checks: lines(form.checks), after: lines(form.after), back: lines(form.back) };
  if (Object.values(lists).some((list) => list.length > 10)) {
    return { recipe: null, error: "At most 10 commands in each list." };
  }
  const back = lists.back;
  return {
    recipe: {
      checks: lists.checks,
      method: form.method,
      after: lists.after,
      address: address || null,
      protected: lines(form.protected.replace(/,/g, "\n")),
      back: back.length ? back : null,
      timeout: Math.round(timeout),
      notes: form.notes.trim(),
    },
    error: null,
  };
}

export const RESULT_TEXT: Record<ShipResult, { label: string; tone: "ok" | "warn" | "err" | "muted" }> = {
  shipped: { label: "Live", tone: "ok" },
  denied: { label: "Not allowed", tone: "muted" },
  checks_failed: { label: "A check failed", tone: "warn" },
  refused: { label: "Stopped before writing", tone: "warn" },
  rolled_back: { label: "Went back by itself", tone: "warn" },
  back_failed: { label: "Going back did not finish", tone: "err" },
  failed: { label: "Failed", tone: "err" },
  interrupted: { label: "Interrupted", tone: "warn" },
};

const SHIPPED_QUIETLY: Record<ShipMethod, string> = {
  branch: "Branch left",
  merge: "Merged",
  files: "Files copied",
  none: "Done",
};

/** The result's label. "Live" only when something ran or answered afterwards: a branch left in
 * the original, or a merge with nothing run after it, started and checked nothing. */
export function shipResultText(record: Pick<ShipRecord, "result" | "plan" | "recipe">) {
  const method = record.plan.method;
  if (record.result === "shipped") {
    const ran = method !== "branch" && (record.recipe.after.length > 0 || Boolean(record.recipe.address));
    return ran ? RESULT_TEXT.shipped : { label: SHIPPED_QUIETLY[method], tone: "ok" as const };
  }
  return record.result ? RESULT_TEXT[record.result] : { label: "Running", tone: "muted" as const };
}

/** The card's allow button, by what allowing does. */
export function allowLabel(card: Pick<ShipCard, "method" | "commands" | "address">): string {
  if (card.method === "branch") return "Allow and leave the branch";
  if (card.commands.length || card.address) return "Allow and make it live";
  return { merge: "Allow and merge", files: "Allow and copy the files", none: "Allow" }[card.method];
}

/** The card's question, by what allowing does. */
export function cardTitle(card: Pick<ShipCard, "method" | "commands" | "address">): string {
  if (card.method === "branch") return "Allow leaving this branch in the original?";
  if (card.commands.length || card.address) return "Allow this change to go live?";
  return card.method === "none" ? "Allow this change?" : "Allow this change into the original?";
}

/** A message with commands between backticks → its parts, the commands marked as code. An odd
 * number of backticks is not a command: the text stays as it is. */
export function codeParts(text: string): { text: string; code: boolean }[] {
  const parts = text.split("`");
  if (parts.length % 2 === 0) return [{ text, code: false }];
  return parts.map((part, index) => ({ text: part, code: index % 2 === 1 })).filter((part) => part.text);
}

/** The one message shown above the panel's buttons: the last thing that happened there. */
export interface ShipNotice {
  kind: "start" | "update";
  ok: boolean;
  text: string;
}

export const startNotice = (text: string): ShipNotice => ({ kind: "start", ok: false, text });
export const updateNotice = (ok: boolean, text: string): ShipNotice => ({ kind: "update", ok, text });

/** "Bring the copy up to date" is offered after the original moved on, and stays after a try. */
export function offerUpdate(gitCopy: boolean, notice: ShipNotice | null): boolean {
  if (!gitCopy || !notice) return false;
  return notice.kind === "update" || /moved on/.test(notice.text);
}

/** Who confirmed the recipe, in words. */
export function confirmedBy(by: string | null): string {
  if (!by) return "Not confirmed yet.";
  if (by === "person") return "Saved by you";
  if (by.startsWith("agent")) return "Proposed by the assistant and confirmed by you";
  return `Confirmed (${by})`;
}

/** What a running ship is doing, in the card's words for its method: "live" only when something
 * runs or answers afterwards, never for a branch left in the original. */
function runningLabel(recipe: ShipRecipe | null): { label: string; short: string } {
  const method = recipe?.method;
  if (method === "branch") return { label: "Leaving the branch…", short: "Branch…" };
  if (recipe && (recipe.after.length || recipe.address)) return { label: "Making it live…", short: "Live…" };
  if (method === "merge") return { label: "Merging…", short: "Merging…" };
  if (method === "files") return { label: "Copying the files…", short: "Copying…" };
  return { label: "Working…", short: "Working…" };
}

/** What the top-bar button says (`short` is the compact button's text, on a phone). */
export function shipButton(view: ShipView | null | undefined): {
  label: string;
  short: string;
  state: "setup" | "idle" | "running" | "waiting";
} {
  if (!view || !view.confirmed) return { label: "Make it live", short: "Make live", state: "setup" };
  if (view.running && view.card) return { label: allowLabel(view.card), short: "Allow", state: "waiting" };
  if (view.running) return { ...runningLabel(view.recipe), state: "running" };
  return { label: "Make it live", short: "Make live", state: "idle" };
}

export interface ProgressStep {
  key: string;
  label: string;
  state: "running" | "ok" | "failed" | "waiting" | "skipped";
  /** The label ends with a command (shown in a code font). */
  command?: boolean;
  detail?: string;
  output?: string;
}

function commandSteps(prefix: string, label: string, results: ShipCommandResult[]): ProgressStep[] {
  return results.map((result, index) => ({
    key: `${prefix}-${index}`,
    label: `${label}: ${result.command}`,
    command: true,
    state: result.exit_code === 0 ? "ok" : "failed",
    detail: result.timed_out
      ? "stopped at the time limit"
      : `exit ${result.exit_code ?? "?"} · ${(result.duration_ms / 1000).toFixed(1)} s`,
    output: result.output,
  }));
}

/** The steps of a ship so far, from its record plus the step running right now (a live event). */
export function progressSteps(record: ShipRecord | null, live: ShipStepEvent | null): ProgressStep[] {
  if (!record) return [];
  const steps: ProgressStep[] = [...commandSteps("check", "Check", record.checks)];
  if (record.phase === "waiting") {
    steps.push({ key: "card", label: "Waiting for you to allow it", state: "waiting" });
  }
  if (record.decision) {
    steps.push({
      key: "decision",
      label: record.decision.decision === "allow" ? "You allowed it" : "You did not allow it",
      state: record.decision.decision === "allow" ? "ok" : "skipped",
    });
  }
  if (record.applied.length) {
    const failed = record.applied.some((line) => line.startsWith("Failed"));
    steps.push({ key: "apply", label: "Change made in the original", state: failed ? "failed" : "ok", detail: record.applied.join("\n") });
  }
  steps.push(...commandSteps("after", "After", record.after));
  if (record.address) {
    steps.push({
      key: "address",
      label: `Address ${record.address.address}`,
      state: record.address.ok ? "ok" : "failed",
      detail: record.address.attempts.join(" · "),
    });
  }
  if (record.back.length) steps.push({ key: "back", label: "Went back", state: "ok", detail: record.back.join("\n") });
  steps.push(...commandSteps("back_after", "After going back", record.back_after));
  if (live && live.ship_id === record.id && live.state === "running" && !record.result) {
    const names: Record<string, string> = {
      check: "Check",
      after: "After",
      back_after: "After going back",
      apply: "Making the change in the original",
      address: `Checking ${live.address ?? "the address"}`,
      back: "Going back",
    };
    const label = live.command ? `${names[live.step]}: ${live.command}` : names[live.step];
    if (!steps.some((step) => step.label === label && step.state !== "running")) {
      steps.push({ key: `live-${live.step}-${live.index ?? 0}`, label, state: "running", command: Boolean(live.command) });
    }
  }
  return steps;
}
