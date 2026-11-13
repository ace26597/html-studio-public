// Make it live (CONTRACT.md, "Ship: make it live"): pure helpers for the ship panel.

import type { ShipCommandResult, ShipMethod, ShipRecipe, ShipRecord, ShipResult, ShipStepEvent, ShipView } from "./types";

export const METHOD_LABEL: Record<ShipMethod, string> = {
  branch: "Leave a branch in the original's repository",
  merge: "Merge into the original's branch (fast-forward only)",
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
  back_failed: { label: "Going back failed", tone: "err" },
  failed: { label: "Failed", tone: "err" },
  interrupted: { label: "Interrupted", tone: "warn" },
};

/** The result's label: a branch left in the original is not live yet. */
export function resultText(result: ShipResult, method: ShipMethod) {
  if (result === "shipped" && method === "branch") return { label: "Branch left", tone: "ok" as const };
  return RESULT_TEXT[result];
}

/** Who confirmed the recipe, in words. */
export function confirmedBy(by: string | null): string {
  if (!by) return "Not confirmed yet.";
  if (by === "person") return "Saved by you";
  if (by.startsWith("agent")) return "Proposed by the assistant and confirmed by you";
  return `Confirmed (${by})`;
}

/** What the top-bar button says. */
export function shipButton(view: ShipView | null | undefined): {
  label: string;
  state: "setup" | "idle" | "running" | "waiting";
} {
  if (!view || !view.confirmed) return { label: "Make it live", state: "setup" };
  if (view.running && view.card) return { label: "Allow to make it live", state: "waiting" };
  if (view.running) return { label: "Making it live…", state: "running" };
  return { label: "Make it live", state: "idle" };
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
