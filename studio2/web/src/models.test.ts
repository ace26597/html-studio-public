import { describe, expect, it } from "vitest";
import {
  checkChip,
  checkFacts,
  checkedWhen,
  costWords,
  hasOtherModels,
  isRunning,
  showsLitellm,
  type CheckResult,
  type ModelsInfo,
} from "./models";
import type { LitellmStatus } from "./setup";

const part = (ok: boolean, label = "A part") => ({ id: label, label, ok, detail: ok ? "" : "It did not." });

const result = (extra: Partial<CheckResult> = {}): CheckResult => ({
  connection: "openai",
  model: "gpt-x",
  status: "passed",
  reason: null,
  parts: [part(true), part(true), part(true), part(true), part(true)],
  seconds: 48,
  steps: 10,
  input_tokens: 159_827,
  output_tokens: 757,
  cost_usd: 0.100841,
  cost_estimated: false,
  notes: [],
  trace: [],
  said: "DONE",
  at: "2026-11-12T17:39:06.000Z",
  ...extra,
});

const litellm = (extra: Partial<LitellmStatus> = {}): LitellmStatus => ({
  installed: false,
  version: "1.102.1",
  pinned: "1.102.1",
  needed: false,
  connections: [],
  state: "not_installed",
  detail: "",
  port: null,
  last_exit: null,
  log: "",
  install_command: "studio litellm install",
  size: "about 430 MB on disk",
  installing: false,
  install_error: null,
  install_log: [],
  ...extra,
});

const info = (extra: Partial<ModelsInfo> = {}): ModelsInfo => ({
  connections: [
    {
      id: "subscription",
      label: "Claude plan",
      kind: "claude-subscription",
      available: true,
      reason: null,
      default_model: "opus",
      provider: null,
      small_model: null,
      models: [{ id: "opus", label: "Opus", cost: "plan", warning: "It uses a little of the plan.", check: null }],
    },
  ],
  running: null,
  parts: [],
  check_version: 1,
  note: { headline: "Claude models work best with the studio; other models are experimental.", limits: [] },
  litellm: litellm(),
  ...extra,
});

describe("the chip beside a model", () => {
  it("says passed, how many parts passed, failed, stopped or not checked", () => {
    expect(checkChip(null)).toEqual({ tone: "neutral", text: "Not checked" });
    expect(checkChip(result())).toEqual({ tone: "ok", text: "Passed" });
    expect(checkChip(result({ status: "failed", parts: [part(true), part(false), part(true), part(true), part(true)] }))).toEqual({
      tone: "off",
      text: "4 of 5 passed",
    });
    expect(checkChip(result({ status: "failed", parts: [part(false), part(false)] }))).toEqual({ tone: "off", text: "Failed" });
    expect(checkChip(result({ status: "stopped" }))).toEqual({ tone: "neutral", text: "Stopped" });
  });
});

describe("the facts of a check", () => {
  it("gives the time, the steps, the charge, the tokens and when", () => {
    const facts = checkFacts(result(), "en-GB");
    expect(facts.startsWith("48s · 10 steps · $0.10 · 161k tokens · ")).toBe(true);
    expect(facts).toContain("12 Nov");
  });

  it("marks a plan figure as not a charge and leaves out what is not known", () => {
    expect(checkFacts(result({ cost_usd: 0.0117, cost_estimated: true, steps: 1 }), "en-GB")).toContain("1 step · ≈ $0.01 at API prices");
    const bare = checkFacts(result({ cost_usd: null, input_tokens: null, output_tokens: null, at: "not a date" }));
    expect(bare).toBe("48s · 10 steps");
    expect(checkedWhen("nonsense")).toBe("");
  });
});

describe("what a model costs, in words", () => {
  it("covers the four cases", () => {
    expect(costWords("free")).toBe("Free");
    expect(costWords("plan")).toBe("On your Claude plan");
    expect(costWords("paid")).toBe("Billed per use");
    expect(costWords("unknown")).toBe("May cost money");
  });
});

describe("what the Models block shows", () => {
  it("knows which model is being checked", () => {
    const running = info({ running: { connection: "subscription", model: "opus", started_at: "", phase: "First message" } });
    expect(isRunning(running, "subscription", "opus")).toBe(true);
    expect(isRunning(running, "subscription", "sonnet")).toBe(false);
    expect(isRunning(info(), "subscription", "opus")).toBe(false);
    expect(isRunning(null, "subscription", "opus")).toBe(false);
  });

  it("shows the note about other models only when one is listed", () => {
    expect(hasOtherModels(info())).toBe(false);
    const other = info();
    other.connections[0].models.push({ id: "gpt-x", label: "gpt-x", experimental: true, cost: "paid", warning: "Billed.", check: null });
    expect(hasOtherModels(other)).toBe(true);
  });

  it("shows the studio's LiteLLM when something needs it, it is there, or an install is under way or failed", () => {
    expect(showsLitellm(litellm())).toBe(false);
    expect(showsLitellm(litellm({ needed: true }))).toBe(true);
    expect(showsLitellm(litellm({ installed: true, state: "idle" }))).toBe(true);
    expect(showsLitellm(litellm({ installing: true }))).toBe(true);
    expect(showsLitellm(litellm({ install_error: "No uv." }))).toBe(true);
  });
});

describe("pickingLine", () => {
  it("names the connection's own model, or says none is set", async () => {
    const { pickingLine } = await import("./models");
    expect(pickingLine({ kind: "anthropic-gateway", picking_model: "gpt-5.6-luna" })).toBe(
      "Skills are picked by gpt-5.6-luna, on this connection.",
    );
    expect(pickingLine({ kind: "anthropic-gateway", picking_model: null })).toContain("Add small_model");
    expect(pickingLine({ kind: "fake" })).toBe("");
  });
});
