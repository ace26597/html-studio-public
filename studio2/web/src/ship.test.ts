import { describe, expect, it } from "vitest";
import { formRecipe, progressSteps, recipeForm, shipButton } from "./ship";
import type { ShipRecipe, ShipRecord, ShipView } from "./types";

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
    const card = { ship_id: "s_1" } as ShipView["card"];
    expect(shipButton(view({ running: "s_1", card })).state).toBe("waiting");
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
