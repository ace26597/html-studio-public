import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { changesPage } from "./reload";
import { initialThread, planPlacement, reduceEvents, sendsQueued, type NoticeItem, type ThreadState } from "./thread";
import { ThreadView, type ThreadActions } from "./ThreadView";
import type { StudioEvent } from "./types";

// The project's plan (`set_plan`): only the newest is drawn, once (CONTRACT.md, "Big projects").

type Loose = { type: StudioEvent["type"]; turn_id?: string | null } & Record<string, unknown>;
let seq = 0;
const ev = (event: Loose): StudioEvent =>
  ({ seq: ++seq, ts: new Date(Date.UTC(2026, 10, 20, 9, 0, seq)).toISOString(), turn_id: null, ...event }) as unknown as StudioEvent;
const start = (turn: string, text: string) => ev({ type: "turn_started", turn_id: turn, text, selection: null, connection: "fake", model: null });
const finish = (turn: string) => ev({ type: "turn_finished", turn_id: turn, status: "completed", error: null, usage: null });
const plan = (turn: string, ...entries: [string, string][]) =>
  ev({ type: "plan", turn_id: turn, source: "set_plan", entries: entries.map(([content, status]) => ({ content, status })) });
const tasks = (turn: string, ...entries: [string, string][]) =>
  ev({ type: "plan", turn_id: turn, entries: entries.map(([content, status]) => ({ content, status })) });
const build = (...events: StudioEvent[]): ThreadState => reduceEvents(initialThread, events);

const actions = {
  onRetry: () => undefined,
  onOpenVersion: () => undefined,
  canRetry: true,
  onDecide: () => undefined,
  decisions: {},
  fileHref: () => null,
  onPutBack: () => undefined,
} as unknown as ThreadActions;
const html = (state: ThreadState) => renderToStaticMarkup(<ThreadView entries={state.entries} actions={actions} />);

const SITE = "Build a three-page site about the garden club with one shared style";

describe("where the plan is drawn", () => {
  it("is drawn pinned in the request that is changing it", () => {
    const state = build(start("t1", SITE), plan("t1", ["Shared style", "completed"], ["Home page", "in_progress"], ["Events", "pending"]));
    expect(planPlacement(state.entries)).toMatchObject({ turnId: "t1", changedIn: "t1", changedBy: null });
    const page = html(state);
    expect(page).toContain('aria-label="Plan"');
    expect(page).toContain("1 of 3");
  });

  it("after that request it is folded there, done items struck through", () => {
    const state = build(start("t1", SITE), plan("t1", ["Shared style", "completed"], ["Home page", "pending"]), finish("t1"));
    const page = html(state);
    expect(page).not.toContain('aria-label="Plan"');
    expect(page).toContain("Plan · 1 of 2 done");
    expect(page).not.toContain("last changed by");
  });

  it("an unfinished plan moves to the newest request and names the one that changed it", () => {
    const state = build(
      start("t1", SITE),
      plan("t1", ["Shared style", "completed"], ["Home page", "pending"]),
      finish("t1"),
      start("t2", "Carry on"),
    );
    const placed = planPlacement(state.entries);
    expect(placed).toMatchObject({ turnId: "t2", changedIn: "t1" });
    expect(placed?.changedBy).toBe("Build a three-page site about the garden club…");
    const page = html(state);
    expect(page.match(/Plan · 1 of 2 done/g)).toHaveLength(1);
    expect(page).toContain("last changed by “Build a three-page site about the garden club…”");
    expect(page).not.toContain('aria-label="Plan"');     // not pinned: this request has not touched it
  });

  it("is pinned again once the newer request changes it, and drawn only there", () => {
    const state = build(
      start("t1", SITE),
      plan("t1", ["Shared style", "completed"], ["Home page", "pending"]),
      finish("t1"),
      start("t2", "Carry on"),
      plan("t2", ["Shared style", "completed"], ["Home page", "in_progress"]),
    );
    expect(planPlacement(state.entries)).toMatchObject({ turnId: "t2", changedBy: null });
    const page = html(state);
    expect(page.match(/aria-label="Plan"/g)).toHaveLength(1);
    expect(page).not.toContain("Plan · 1 of 2 done");
  });

  it("a finished plan stays with the request that finished it", () => {
    const state = build(start("t1", SITE), plan("t1", ["Shared style", "completed"]), finish("t1"), start("t2", "Change a colour"));
    expect(planPlacement(state.entries)).toMatchObject({ turnId: "t1", changedBy: null });
  });

  it("an empty plan clears it", () => {
    const state = build(start("t1", SITE), plan("t1", ["Shared style", "pending"]), finish("t1"), start("t2", "Drop the plan"), plan("t2"));
    expect(planPlacement(state.entries)).toBeNull();
    expect(html(state)).not.toContain("Plan ·");
  });
});

describe("a change under notes/ does not reload the page", () => {
  it("reloads for page files and keeps still for notes only", () => {
    expect(changesPage(["notes/project.md"])).toBe(false);
    expect(changesPage(["notes/project.md", "index.html"])).toBe(true);
    expect(changesPage(["notes"])).toBe(true);       // a top-level file of that name is a page file
    expect(changesPage(["src/notes/card.html"])).toBe(true);
    expect(changesPage([])).toBe(true);
    expect(changesPage(undefined)).toBe(true);
  });
});

describe("the agent program's own task list never hides the project's plan", () => {
  it("a later task list leaves the unfinished set_plan plan in place", () => {
    const state = build(
      start("t1", SITE),
      plan("t1", ["Shared style", "completed"], ["Home page", "completed"], ["Events", "pending"], ["Join", "pending"], ["Gallery", "pending"], ["Check", "pending"]),
      finish("t1"),
      start("t2", "Fix the footer"),
      tasks("t2", ["Read", "completed"], ["Edit", "completed"], ["Check", "completed"]),
      finish("t2"),
    );
    expect(planPlacement(state.entries)).toMatchObject({ turnId: "t2", changedIn: "t1" });
    const page = html(state);
    expect(page).toContain("Plan · 2 of 6 done");
    expect(page).not.toContain("3 of 3");
  });

  it("a task list in the same request as set_plan does not replace it", () => {
    const state = build(start("t1", SITE), plan("t1", ["Home page", "in_progress"], ["Events", "pending"]), tasks("t1", ["Read", "completed"]));
    expect(planPlacement(state.entries)?.entries.map((entry) => entry.content)).toEqual(["Home page", "Events"]);
  });

  it("a thread with no set_plan plan still draws a task list", () => {
    const state = build(start("t1", SITE), tasks("t1", ["Read", "completed"], ["Edit", "in_progress"]));
    expect(planPlacement(state.entries)).toMatchObject({ turnId: "t1", changedBy: null });
  });
});

describe("a message to a project with messages waiting joins the queue", () => {
  it("queues while a request runs or messages wait, and only then", () => {
    expect(sendsQueued({ runningTurnId: null, queue: [] })).toBe(false);
    expect(sendsQueued({ runningTurnId: "t1", queue: [] })).toBe(true);
    const waiting = build(ev({ type: "turn_queued", queued_id: "q_1", text: "a2", selection: null, attachments: [] }));
    expect(waiting.runningTurnId).toBeNull();
    expect(sendsQueued(waiting)).toBe(true);
  });

  it("a restart that drops a queued message offers its text back", () => {
    const state = build(
      ev({ type: "turn_queued", queued_id: "q_1", text: "make the heading green", selection: null, attachments: [] }),
      ev({ type: "turn_unqueued", queued_id: "q_1", reason: "restart", text: "make the heading green" }),
    );
    const notice = state.entries[state.entries.length - 1] as NoticeItem;
    expect(notice.message).toContain("because the studio restarted: “make the heading green”");
    expect(notice.restoreText).toBe("make the heading green");
    expect(state.queue).toEqual([]);
  });
});
