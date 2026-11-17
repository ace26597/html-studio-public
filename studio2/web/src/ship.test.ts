import { describe, expect, it } from "vitest";
import {
  METHOD_LABEL,
  allowLabel,
  cardTitle,
  codeParts,
  formRecipe,
  offerUpdate,
  progressSteps,
  recipeForm,
  shipButton,
  shipResultText,
  startNotice,
  updateNotice,
} from "./ship";
import type { ShipCard, ShipRecipe, ShipRecord, ShipView } from "./types";

const recipe: ShipRecipe = {
  checks: ["npm test"],
  method: "merge",
  after: ["npm run build", "pm2 restart site"],
  address: "http://127.0.0.1:3020/",
  protected: [".env*", "migrations/"],
  back: null,
  timeout: 600,
  notes: "",
};

const view = (patch: Partial<ShipView>): ShipView => ({
  available: true,
  methods: ["branch", "merge"],
  recipe,
  confirmed: true,
  confirmed_by: "person",
  confirmed_at: "2026-11-12T00:00:00Z",
  last: null,
  running: null,
  card: null,
  ...patch,
});

const record = (patch: Partial<ShipRecord>): ShipRecord => ({
  id: "s_1",
  project: "p_1",
  by: "person",
  turn_id: null,
  started_at: "",
  finished_at: null,
  phase: "checks",
  result: null,
  message: null,
  recipe,
  attach: { mode: "copy", path: "/x", kind: "git", branch: "main", studio_branch: "studio/x" },
  plan: { method: "merge", base: "a", head: "b", where: "/x", writes: [] },
  files: [],
  files_more: 0,
  checks: [],
  decision: null,
  applied: [],
  after: [],
  address: null,
  back: [],
  back_after: [],
  ...patch,
});

describe("recipe form", () => {
  it("round-trips and drops empty lines", () => {
    const form = recipeForm(recipe);
    expect(form.after).toBe("npm run build\npm2 restart site");
    const { recipe: back } = formRecipe({ ...form, checks: "npm test\n\n  ", protected: ".env*, migrations/" });
    expect(back).toEqual(recipe);
  });
  it("refuses a bad address and a bad time limit", () => {
    const form = recipeForm(recipe);
    expect(formRecipe({ ...form, address: "ftp://x" }).error).toMatch(/http/);
    expect(formRecipe({ ...form, timeout: "5" }).error).toMatch(/seconds/);
    expect(formRecipe({ ...form, address: "" }).recipe?.address).toBeNull();
  });
  it("keeps go-back commands only when there are some", () => {
    expect(formRecipe({ ...recipeForm(recipe), back: "pm2 restart site" }).recipe?.back).toEqual(["pm2 restart site"]);
  });
});

describe("button", () => {
  it("follows the ship's state", () => {
    expect(shipButton(null).state).toBe("setup");
    expect(shipButton(view({ confirmed: false })).state).toBe("setup");
    expect(shipButton(view({})).state).toBe("idle");
    expect(shipButton(view({ running: "s_1" })).state).toBe("running");
    const card = { ship_id: "s_1", method: "merge", commands: [], address: null } as unknown as ShipView["card"];
    expect(shipButton(view({ running: "s_1", card })).state).toBe("waiting");
  });
  it("uses the card's words for each method, never 'live' for a branch", () => {
    const branch = { ...recipe, method: "branch" as const };
    const branchCard = { ship_id: "s_1", method: "branch", commands: [], address: null } as unknown as ShipCard;
    const waiting = shipButton(view({ recipe: branch, running: "s_1", card: branchCard }));
    expect(waiting.label).toBe("Allow and leave the branch");
    expect(waiting.short).toBe("Allow");
    const running = shipButton(view({ recipe: branch, running: "s_1" }));
    expect(running.label).toBe("Leaving the branch…");
    expect(`${running.label} ${running.short}`).not.toMatch(/live/i);

    const liveCard = { ship_id: "s_1", method: "merge", commands: ["npm run build"], address: null } as unknown as ShipCard;
    expect(shipButton(view({ running: "s_1", card: liveCard })).label).toBe("Allow and make it live");
    expect(shipButton(view({ running: "s_1" })).label).toBe("Making it live…");
    const quiet = { ...recipe, after: [], address: null };
    expect(shipButton(view({ recipe: quiet, running: "s_1" })).label).toBe("Merging…");
    expect(shipButton(view({ recipe: { ...quiet, method: "files" }, running: "s_1" })).label).toBe("Copying the files…");
    const quietCard = { ship_id: "s_1", method: "merge", commands: [], address: null } as unknown as ShipCard;
    expect(shipButton(view({ recipe: quiet, running: "s_1", card: quietCard })).label).toBe("Allow and merge");
  });
});

describe("progress", () => {
  it("lists checks, the decision, the change, after-commands and the address", () => {
    const steps = progressSteps(
      record({
        phase: "done",
        result: "shipped",
        checks: [{ command: "npm test", exit_code: 0, output: "ok", duration_ms: 1200, timed_out: false }],
        decision: { decision: "allow", at: "", by: "person", protected: [] },
        applied: ["main fast-forwarded"],
        after: [{ command: "pm2 restart site", exit_code: 0, output: "", duration_ms: 300, timed_out: false }],
        address: { ok: true, address: "http://127.0.0.1:3020/", status: 200, attempts: ["try 1: HTTP 200"] },
      }),
      null,
    );
    expect(steps.map((step) => [step.label, step.state])).toEqual([
      ["Check: npm test", "ok"],
      ["You allowed it", "ok"],
      ["Change made in the original", "ok"],
      ["After: pm2 restart site", "ok"],
      ["Address http://127.0.0.1:3020/", "ok"],
    ]);
  });
  it("shows the step running now and a waiting card", () => {
    const live = { type: "ship_step", ship_id: "s_1", step: "check", state: "running", index: 0, command: "npm test", seq: 1, ts: "", turn_id: null } as const;
    expect(progressSteps(record({}), live).at(-1)).toMatchObject({ label: "Check: npm test", state: "running" });
    expect(progressSteps(record({ phase: "waiting" }), null).at(-1)).toMatchObject({ state: "waiting" });
  });
  it("shows a failed address and going back", () => {
    const steps = progressSteps(
      record({
        phase: "done",
        result: "rolled_back",
        decision: { decision: "allow", at: "", by: "person", protected: [] },
        applied: ["x"],
        address: { ok: false, address: "http://a/", status: null, attempts: ["try 1: ConnectError"] },
        back: ["main is back at abc1234."],
      }),
      null,
    );
    expect(steps.map((step) => step.state)).toEqual(["ok", "ok", "failed", "ok"]);
  });
});

describe("what the panel says", () => {
  const shipped = (method: "branch" | "merge" | "files" | "none", after: string[], address: string | null = null) =>
    record({ result: "shipped", recipe: { ...recipe, method, after, address }, plan: { method, base: "a", head: "b", where: "/x", writes: [] } });

  it("calls a ship live only when something ran or answered", () => {
    expect(shipResultText(shipped("merge", [])).label).toBe("Merged");
    expect(shipResultText(shipped("files", [])).label).toBe("Files copied");
    expect(shipResultText(shipped("none", [])).label).toBe("Done");
    expect(shipResultText(shipped("branch", ["npm run build"])).label).toBe("Branch left");
    expect(shipResultText(shipped("merge", ["pm2 restart site"])).label).toBe("Live");
    expect(shipResultText(shipped("files", [], "http://127.0.0.1:3020/")).label).toBe("Live");
    expect(shipResultText(record({ result: "back_failed" })).label).toBe("Going back did not finish");
  });

  it("words the allow button by what it does", () => {
    const card = (method: ShipCard["method"], commands: ShipCard["commands"], address: string | null = null) =>
      ({ method, commands, address }) as ShipCard;
    expect(allowLabel(card("branch", []))).toBe("Allow and leave the branch");
    expect(allowLabel(card("merge", []))).toBe("Allow and merge");
    expect(allowLabel(card("files", []))).toBe("Allow and copy the files");
    expect(allowLabel(card("none", []))).toBe("Allow");
    expect(allowLabel(card("merge", [{ command: "pm2 restart site", where: "/x" }]))).toBe("Allow and make it live");
    expect(allowLabel(card("none", [], "http://127.0.0.1:3020/"))).toBe("Allow and make it live");
    expect(cardTitle(card("branch", []))).toBe("Allow leaving this branch in the original?");
    expect(cardTitle(card("merge", []))).toBe("Allow this change into the original?");
    expect(cardTitle(card("merge", [{ command: "pm2 restart site", where: "/x" }]))).toBe("Allow this change to go live?");
  });

  it("names the merge method as the card does", () => {
    expect(METHOD_LABEL.merge).toBe("Merge into the original's checked-out branch (fast-forward only)");
    expect(METHOD_LABEL.none).toBe("Nothing to move: the folder is edited in place");
  });

  it("shows commands between backticks as code", () => {
    expect(codeParts("then `git reset --keep abc1234` in /x.")).toEqual([
      { text: "then ", code: false },
      { text: "git reset --keep abc1234", code: true },
      { text: " in /x.", code: false },
    ]);
    expect(codeParts("a lone ` stays")).toEqual([{ text: "a lone ` stays", code: false }]);
  });

  it("keeps one current message, and offers the update after a moved-on refusal or an update", () => {
    const movedOn = startNotice("The original's main has moved on since the copy last matched it.");
    expect(movedOn).toEqual({ kind: "start", ok: false, text: "The original's main has moved on since the copy last matched it." });
    expect(offerUpdate(true, movedOn)).toBe(true);
    expect(offerUpdate(false, movedOn)).toBe(false);
    expect(offerUpdate(true, startNotice("The original has uncommitted changes (index.html)."))).toBe(false);
    const conflict = updateNotice(false, "The original's new commits conflict with the copy's changes in index.html.");
    expect(conflict.kind).toBe("update");
    expect(offerUpdate(true, conflict)).toBe(true);
    expect(offerUpdate(true, null)).toBe(false);
  });
});
