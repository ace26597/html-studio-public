import { describe, expect, it } from "vitest";
import {
  allTools,
  initialThread,
  lastTurn,
  latestActivity,
  reduceEvent,
  reduceEvents,
  runningChangedPaths,
  runningTurn,
  seedApp,
  summarizeTools,
  turnViewItems,
  waitingApprovals,
  type ActivityGroup,
  type ApprovalItem,
  type MessageItem,
  type ThreadState,
  type ToolItem,
  type Turn,
} from "./thread";
import type { StudioEvent } from "./types";

// ---- a tiny event-log builder --------------------------------------------------

type Loose = { type: StudioEvent["type"]; turn_id?: string | null } & Record<string, unknown>;

function log(...events: Loose[]): StudioEvent[] {
  return events.map(
    (event, index) =>
      ({
        seq: index + 1,
        ts: new Date(Date.UTC(2026, 10, 10, 9, 0, index)).toISOString(),
        turn_id: null,
        ...event,
      }) as unknown as StudioEvent,
  );
}

const T = "turn_a";
const started = (text = "Build a page", extra: Record<string, unknown> = {}): Loose => ({
  type: "turn_started",
  turn_id: T,
  text,
  selection: null,
  connection: "fake",
  model: null,
  ...extra,
});
const chunk = (message_id: string, text: string, turn_id = T): Loose => ({
  type: "message_chunk",
  turn_id,
  message_id,
  text,
});
const thought = (message_id: string, text: string): Loose => ({ type: "thought_chunk", turn_id: T, message_id, text });
const call = (tool_id: string, kind: string, title: string, extra: Record<string, unknown> = {}): Loose => ({
  type: "tool_call",
  turn_id: T,
  tool_id,
  name: kind,
  kind,
  title,
  input: {},
  parent_tool_id: null,
  ...extra,
});
const result = (tool_id: string, status = "ok", summary: string | null = "done"): Loose => ({
  type: "tool_result",
  turn_id: T,
  tool_id,
  status,
  summary,
});
const finished = (status = "completed", extra: Record<string, unknown> = {}): Loose => ({
  type: "turn_finished",
  turn_id: T,
  status,
  error: null,
  usage: { input_tokens: 100, output_tokens: 20, cost_usd: null, duration_ms: 4200 },
  session_id: "s1",
  ...extra,
});

function build(...events: Loose[]): ThreadState {
  return reduceEvents(initialThread, log(...events));
}

function onlyTurn(state: ThreadState): Turn {
  const turns = state.entries.filter((entry): entry is Turn => entry.type === "turn");
  expect(turns).toHaveLength(1);
  return turns[0];
}

// ---- tests ---------------------------------------------------------------------------

describe("turn lifecycle", () => {
  it("starts empty", () => {
    expect(initialThread.entries).toEqual([]);
    expect(runningTurn(initialThread)).toBeNull();
    expect(lastTurn(initialThread)).toBeNull();
  });

  it("records the user message, selection and running state", () => {
    const selection = { path: "/", selector: "h2", tag: "h2", text: "Share", html: "<h2>Share</h2>" };
    const state = build(started("Make this bigger", { selection, model: "opus" }));
    const turn = onlyTurn(state);
    expect(turn.text).toBe("Make this bigger");
    expect(turn.selection).toEqual(selection);
    expect(turn.model).toBe("opus");
    expect(turn.status).toBe("running");
    expect(state.runningTurnId).toBe(T);
    expect(runningTurn(state)?.id).toBe(T);
  });

  it("completes with usage and clears the running turn", () => {
    const state = build(started(), chunk("m1", "Hi"), finished());
    const turn = onlyTurn(state);
    expect(turn.status).toBe("completed");
    expect(turn.usage).toEqual({ input_tokens: 100, output_tokens: 20, cost_usd: null, duration_ms: 4200 });
    expect(turn.finishedAt).not.toBeNull();
    expect(state.runningTurnId).toBeNull();
  });

  it("keeps the error of a failed turn", () => {
    const state = build(
      started(),
      call("t1", "read", "Read index.html"),
      finished("failed", { error: "The model timed out." }),
    );
    const turn = onlyTurn(state);
    expect(turn.status).toBe("failed");
    expect(turn.error).toBe("The model timed out.");
    expect(state.runningTurnId).toBeNull();
  });

  it("marks a stopped turn and cancels tools that never reported back", () => {
    const state = build(
      started(),
      call("t1", "read", "Read index.html"),
      result("t1"),
      call("agent", "agent", "Research"),
      call("t2", "search", "Search", { parent_tool_id: "agent" }),
      finished("stopped", { usage: null }),
    );
    const turn = onlyTurn(state);
    expect(turn.status).toBe("stopped");
    expect(turn.usage).toBeNull();
    const tools = allTools(turn.items);
    expect(tools.map((tool) => [tool.toolId, tool.status])).toEqual([
      ["t1", "ok"],
      ["agent", "cancelled"],
      ["t2", "cancelled"],
    ]);
  });

  it("treats an unknown finish status as failed and tolerates missing usage fields", () => {
    const state = build(started(), finished("exploded", { usage: { duration_ms: 10 } }));
    const turn = onlyTurn(state);
    expect(turn.status).toBe("failed");
    expect(turn.usage).toEqual({ input_tokens: null, output_tokens: null, cost_usd: null, duration_ms: 10 });
  });

  it("keeps several turns in order with independent state", () => {
    const state = reduceEvents(
      initialThread,
      log(
        started("first"),
        chunk("m1", "one"),
        finished(),
        { type: "turn_started", turn_id: "turn_b", text: "second", selection: null, connection: "fake", model: null },
        chunk("m2", "two", "turn_b"),
      ),
    );
    const turns = state.entries.filter((entry): entry is Turn => entry.type === "turn");
    expect(turns.map((turn) => [turn.text, turn.status])).toEqual([
      ["first", "completed"],
      ["second", "running"],
    ]);
    expect(state.runningTurnId).toBe("turn_b");
    expect(lastTurn(state)?.id).toBe("turn_b");
  });
});

describe("message chunks", () => {
  it("appends chunks with the same message_id into one message", () => {
    const turn = onlyTurn(build(started(), chunk("m1", "Hello"), chunk("m1", ", "), chunk("m1", "world")));
    expect(turn.items).toHaveLength(1);
    expect(turn.items[0]).toMatchObject({ type: "message", messageId: "m1", text: "Hello, world" });
  });

  it("starts a new message for a new message_id", () => {
    const turn = onlyTurn(build(started(), chunk("m1", "First."), chunk("m2", "Second.")));
    expect(turn.items.map((item) => (item as MessageItem).text)).toEqual(["First.", "Second."]);
  });

  it("keeps chronological order when a tool call interrupts one message_id", () => {
    const turn = onlyTurn(
      build(
        started(),
        chunk("m1", "Before. "),
        call("t1", "edit", "Edit index.html"),
        result("t1"),
        chunk("m1", "After."),
      ),
    );
    expect(turn.items.map((item) => item.type)).toEqual(["message", "tool", "message"]);
    expect((turn.items[0] as MessageItem).text).toBe("Before. ");
    expect((turn.items[2] as MessageItem).text).toBe("After.");
    // Distinct React keys for the two segments of the same message.
    expect(new Set(turn.items.map((item) => item.key)).size).toBe(3);
  });

  it("keeps thoughts and messages apart even with the same message_id", () => {
    const turn = onlyTurn(build(started(), thought("m1", "Let me "), thought("m1", "think."), chunk("m1", "Answer.")));
    expect(turn.items.map((item) => item.type)).toEqual(["thought", "message"]);
    expect(turn.items[0]).toMatchObject({ text: "Let me think." });
  });

  it("ignores empty chunks", () => {
    const turn = onlyTurn(build(started(), chunk("m1", ""), chunk("m1", "x")));
    expect(turn.items).toHaveLength(1);
    expect((turn.items[0] as MessageItem).text).toBe("x");
  });
});

describe("tool calls", () => {
  it("pairs a result with its call", () => {
    const running = build(
      started(),
      call("t1", "execute", "Run python build.py", { input: { command: "python build.py" } }),
    );
    expect(allTools(onlyTurn(running).items)[0]).toMatchObject({ status: "running", summary: null });

    const done = reduceEvents(
      running,
      log(started(), call("t1", "execute", "x"), result("t1", "ok", "exit 0")).slice(2),
    );
    const tool = allTools(onlyTurn(done).items)[0];
    expect(tool).toMatchObject({ toolId: "t1", status: "ok", summary: "exit 0", title: "Run python build.py" });
    expect(tool.input).toEqual({ command: "python build.py" });
  });

  it("records an error result", () => {
    const turn = onlyTurn(build(started(), call("t1", "execute", "Run tests"), result("t1", "error", "exit 1")));
    expect(allTools(turn.items)[0]).toMatchObject({ status: "error", summary: "exit 1" });
  });

  it("pairs interleaved results with the right calls", () => {
    const turn = onlyTurn(
      build(
        started(),
        call("a", "read", "Read a"),
        call("b", "read", "Read b"),
        result("b", "ok", "B"),
        result("a", "error", "A"),
      ),
    );
    expect(allTools(turn.items).map((tool) => [tool.toolId, tool.status, tool.summary])).toEqual([
      ["a", "error", "A"],
      ["b", "ok", "B"],
    ]);
  });

  it("ignores a result for an unknown tool", () => {
    const before = build(started(), call("t1", "read", "Read"));
    const after = reduceEvents(before, log(started(), started(), result("ghost")).slice(2));
    expect(onlyTurn(after).items).toEqual(onlyTurn(before).items);
  });

  it("nests sub-agent calls under their parent, to any depth", () => {
    const turn = onlyTurn(
      build(
        started(),
        call("agent", "agent", "Research competitors"),
        call("s1", "search", "Search", { parent_tool_id: "agent" }),
        call("inner", "agent", "Dig deeper", { parent_tool_id: "agent" }),
        call("f1", "fetch", "Fetch page", { parent_tool_id: "inner" }),
        result("f1"),
        result("s1"),
        call("top", "edit", "Edit index.html"),
      ),
    );
    // Only two rows at the top level; the rest hang off the agent.
    expect(turn.items.map((item) => (item as ToolItem).tool.toolId)).toEqual(["agent", "top"]);
    const agent = (turn.items[0] as ToolItem).tool;
    expect(agent.children.map((child) => child.toolId)).toEqual(["s1", "inner"]);
    expect(agent.children[1].children.map((child) => child.toolId)).toEqual(["f1"]);
    expect(agent.children[1].children[0].status).toBe("ok");
    expect(agent.children[0].status).toBe("ok");
    expect(agent.status).toBe("running");
  });

  it("shows a child whose parent is unknown at the top level instead of dropping it", () => {
    const turn = onlyTurn(build(started(), call("orphan", "read", "Read", { parent_tool_id: "missing" })));
    expect(turn.items).toHaveLength(1);
    expect((turn.items[0] as ToolItem).tool.toolId).toBe("orphan");
  });

  it("falls back to the tool name when the title is empty", () => {
    const turn = onlyTurn(build(started(), call("t1", "other", "", { name: "mcp__db__query" })));
    expect(allTools(turn.items)[0].title).toBe("mcp__db__query");
  });
});

describe("plan", () => {
  it("replaces the previous plan", () => {
    const state = build(
      started(),
      {
        type: "plan",
        turn_id: T,
        entries: [
          { content: "A", status: "in_progress" },
          { content: "B", status: "pending" },
        ],
      },
      {
        type: "plan",
        turn_id: T,
        entries: [
          { content: "A", status: "completed" },
          { content: "B", status: "in_progress" },
          { content: "C", status: "pending" },
        ],
      },
    );
    expect(onlyTurn(state).plan).toEqual([
      { content: "A", status: "completed" },
      { content: "B", status: "in_progress" },
      { content: "C", status: "pending" },
    ]);
  });

  it("is not a thread item (it is pinned, not interleaved)", () => {
    const state = build(started(), { type: "plan", turn_id: T, entries: [{ content: "A", status: "pending" }] });
    expect(onlyTurn(state).items).toEqual([]);
  });
});

describe("de-duplication by seq", () => {
  it("ignores an event it has already applied", () => {
    const events = log(started(), chunk("m1", "Hello"), chunk("m1", " again"));
    const once = reduceEvents(initialThread, events);
    const twice = reduceEvents(once, events);
    expect(twice).toBe(once);
    expect((onlyTurn(twice).items[0] as MessageItem).text).toBe("Hello again");
  });

  it("ignores a reconnect that replays an overlapping tail", () => {
    const events = log(started(), chunk("m1", "a"), chunk("m1", "b"), chunk("m1", "c"), finished());
    const state = reduceEvents(reduceEvents(initialThread, events.slice(0, 3)), events.slice(1));
    expect((onlyTurn(state).items[0] as MessageItem).text).toBe("abc");
    expect(state.lastSeq).toBe(5);
  });

  it("advances lastSeq even for event types it does not know", () => {
    const state = reduceEvent(initialThread, {
      seq: 1,
      ts: "2026-11-10T09:00:00Z",
      turn_id: null,
      type: "future_thing",
    } as unknown as StudioEvent);
    expect(state.lastSeq).toBe(1);
    expect(state.entries).toEqual([]);
  });

  it("ignores malformed events", () => {
    expect(reduceEvent(initialThread, null as unknown as StudioEvent)).toBe(initialThread);
    expect(reduceEvent(initialThread, { type: "notice" } as unknown as StudioEvent)).toBe(initialThread);
  });
});

describe("replay equals live", () => {
  const events = log(
    started("Build it", { selection: { path: "/", selector: "h1", tag: "h1", text: "Hi", html: "<h1>Hi</h1>" } }),
    { type: "plan", turn_id: T, entries: [{ content: "Read", status: "in_progress" }] },
    thought("m0", "Thinking "),
    thought("m0", "hard."),
    chunk("m1", "I will "),
    chunk("m1", "start."),
    call("t1", "read", "Read index.html"),
    result("t1"),
    call("agent", "agent", "Research"),
    call("t2", "search", "Search", { parent_tool_id: "agent" }),
    result("t2"),
    result("agent"),
    call("t3", "edit", "Edit index.html", { input: { file_path: "index.html" } }),
    result("t3"),
    { type: "files_changed", turn_id: T, paths: ["index.html"] },
    { type: "notice", turn_id: T, level: "warning", message: "Slow network" },
    { type: "plan", turn_id: T, entries: [{ content: "Read", status: "completed" }] },
    chunk("m2", "Done."),
    {
      type: "checkpoint",
      turn_id: T,
      sha: "abc",
      kind: "agent",
      message: "Build",
      files: [{ path: "index.html", status: "M" }],
    },
    finished(),
    { type: "files_changed", paths: ["notes.txt"] },
    {
      type: "checkpoint",
      sha: "def",
      kind: "user",
      message: "Edit notes.txt",
      files: [{ path: "notes.txt", status: "A" }],
    },
    { type: "notice", level: "info", message: "Studio updated" },
  );

  it("gives the same model one event at a time, all at once, or in arbitrary batches", () => {
    const allAtOnce = reduceEvents(initialThread, events);

    let oneByOne = initialThread;
    for (const event of events) oneByOne = reduceEvent(oneByOne, event);
    expect(oneByOne).toEqual(allAtOnce);

    for (const size of [2, 3, 7]) {
      let batched = initialThread;
      for (let i = 0; i < events.length; i += size) batched = reduceEvents(batched, events.slice(i, i + size));
      expect(batched).toEqual(allAtOnce);
    }
  });

  it("gives the same model when a reload happens mid-turn and the rest arrives live", () => {
    const full = reduceEvents(initialThread, events);
    for (const cut of [1, 5, 9, 14, 19]) {
      // First page load saw `cut` events live; then the browser reloads and replays from 0.
      const replayed = reduceEvents(initialThread, events.slice(0, cut));
      const resumed = reduceEvents(replayed, events.slice(cut));
      expect(resumed).toEqual(full);
    }
  });

  it("does not mutate earlier states", () => {
    const first = reduceEvents(initialThread, events.slice(0, 6));
    const snapshot = JSON.stringify(first);
    reduceEvents(first, events.slice(6));
    expect(JSON.stringify(first)).toBe(snapshot);
    expect(initialThread.entries).toEqual([]);
  });

  it("builds the expected final thread", () => {
    const state = reduceEvents(initialThread, events);
    expect(state.entries.map((entry) => entry.type)).toEqual(["turn", "checkpoint", "notice"]);
    const turn = state.entries[0] as Turn;
    expect(turn.items.map((item) => item.type)).toEqual([
      "thought",
      "message",
      "tool",
      "tool",
      "tool",
      "notice",
      "message",
      "checkpoint",
    ]);
    expect(turn.plan).toEqual([{ content: "Read", status: "completed" }]);
    expect(turn.changedPaths).toEqual(["index.html"]);
    expect(state.filesRev).toBe(4); // two files_changed + two checkpoints
    expect(state.versionsRev).toBe(2);
    expect(state.lastSeq).toBe(events.length);
  });
});

describe("checkpoints with files too large for versions", () => {
  it("keeps the left out files on the item only when there are any", () => {
    const state = build(
      { type: "checkpoint", sha: "a1", kind: "agent", message: "Plain", files: [] },
      {
        type: "checkpoint",
        sha: "b2",
        kind: "agent",
        message: "With data",
        files: [{ path: "data.csv", status: "deleted" }],
        left_out: [{ path: "data.csv", size: 300 }],
        left_out_count: 1,
      },
    );
    const [plain, withData] = state.entries as { leftOut?: unknown }[];
    expect(plain.leftOut).toBeUndefined();
    expect(withData.leftOut).toEqual({ files: [{ path: "data.csv", size: 300 }], count: 1 });
  });
});

describe("files, checkpoints and notices", () => {
  it("collects changed paths per turn without duplicates", () => {
    const state = build(
      started(),
      { type: "files_changed", turn_id: T, paths: ["index.html"] },
      { type: "files_changed", turn_id: T, paths: ["index.html", "styles.css"] },
    );
    expect(runningChangedPaths(state)).toEqual(["index.html", "styles.css"]);
    expect(state.filesRev).toBe(2);
  });

  it("forgets the running turn's changed paths once it has finished", () => {
    const state = build(started(), { type: "files_changed", turn_id: T, paths: ["index.html"] }, finished());
    expect(runningChangedPaths(state)).toEqual([]);
  });

  it("puts checkpoints and notices without a turn at the top level", () => {
    const state = build(
      { type: "checkpoint", sha: "s1", kind: "user", message: "Edit text", files: [] },
      { type: "notice", level: "error", message: "Disk is full" },
    );
    expect(state.entries).toMatchObject([
      { type: "checkpoint", sha: "s1", kind: "user" },
      { type: "notice", level: "error", message: "Disk is full" },
    ]);
  });

  it("creates a placeholder turn for events whose turn_started was never seen", () => {
    const state = build(chunk("m1", "orphan text"));
    const turn = onlyTurn(state);
    expect(turn.text).toBe("");
    expect(turn.status).toBe("running");
    expect((turn.items[0] as MessageItem).text).toBe("orphan text");
  });
});

describe("activity view", () => {
  it("shows every row while the turn runs", () => {
    const turn = onlyTurn(build(started(), call("a", "edit", "Edit a"), result("a"), call("b", "edit", "Edit b")));
    expect(turnViewItems(turn).map((item) => item.type)).toEqual(["tool", "tool"]);
  });

  it("folds consecutive tool rows into a group once the turn has finished", () => {
    const turn = onlyTurn(
      build(
        started(),
        chunk("m1", "Starting."),
        call("r", "read", "Read index.html"),
        result("r"),
        thought("th", "hmm"),
        call("e1", "edit", "Edit index.html", { input: { file_path: "index.html" } }),
        result("e1"),
        call("e2", "edit", "Edit index.html", { input: { file_path: "index.html" } }),
        result("e2"),
        call("e3", "edit", "Write styles.css", { input: { file_path: "styles.css" } }),
        result("e3"),
        call("x", "execute", "Run build"),
        result("x"),
        chunk("m2", "Done."),
        call("solo", "read", "Read notes"),
        result("solo"),
        finished(),
      ),
    );
    const view = turnViewItems(turn);
    expect(view.map((item) => item.type)).toEqual(["message", "group", "message", "tool"]);
    const group = view[1] as ActivityGroup;
    expect(group.summary).toBe("Edited 2 files, read 1 file, ran 1 command");
    expect(group.items).toHaveLength(6); // five tools and the thinking between them
    expect(group.hasError).toBe(false);
  });

  it("flags a group that contains a failed call, including nested ones", () => {
    const turn = onlyTurn(
      build(
        started(),
        call("agent", "agent", "Research"),
        call("s", "search", "Search", { parent_tool_id: "agent" }),
        result("s", "error", "no network"),
        result("agent"),
        call("e", "edit", "Edit"),
        result("e"),
        finished(),
      ),
    );
    const group = turnViewItems(turn)[0] as ActivityGroup;
    expect(group.type).toBe("group");
    expect(group.hasError).toBe(true);
    expect(group.summary).toBe("Edited 1 file, searched 1 time, used 1 sub\u2011agent");
  });

  it("leaves a thought that precedes the first tool, or trails the last, outside the group", () => {
    const turn = onlyTurn(
      build(
        started(),
        thought("a", "first"),
        call("x", "read", "Read"),
        result("x"),
        call("y", "read", "Read"),
        result("y"),
        thought("b", "last"),
        finished(),
      ),
    );
    expect(turnViewItems(turn).map((item) => item.type)).toEqual(["thought", "group", "thought"]);
  });

  it("summarises kinds in plain words", () => {
    const turn = onlyTurn(
      build(
        started(),
        call("1", "fetch", "Fetch a"),
        call("2", "fetch", "Fetch b"),
        call("3", "think", "Think"),
        call("4", "other", "Custom tool"),
        call("5", "execute", "Run a"),
        call("6", "execute", "Run b"),
      ),
    );
    expect(summarizeTools(allTools(turn.items))).toBe("Ran 2 commands, fetched 2 pages, 2 other steps");
    expect(summarizeTools([])).toBe("No tool activity");
  });
});

describe("latest activity (collapsed chat ticker)", () => {
  it("is null when nothing runs", () => {
    expect(latestActivity(initialThread)).toBeNull();
    expect(latestActivity(build(started(), finished()))).toBeNull();
  });

  it("prefers the most recent tool that is still running, even a nested one", () => {
    const state = build(
      started(),
      call("agent", "agent", "Research competitors"),
      call("s", "search", "Search “acme pricing”", { parent_tool_id: "agent" }),
    );
    expect(latestActivity(state)).toBe("Search “acme pricing”");
  });

  it("describes writing, thinking and starting", () => {
    expect(latestActivity(build(started()))).toBe("Starting");
    expect(latestActivity(build(started(), thought("t", "hmm")))).toBe("Thinking");
    expect(latestActivity(build(started(), call("a", "read", "Read"), result("a"), chunk("m", "So")))).toBe(
      "Writing a reply",
    );
    expect(latestActivity(build(started(), call("a", "read", "Read index.html"), result("a")))).toBe("Read index.html");
  });
});

describe("a tool call announced early", () => {
  it("is filled in by the repeated call instead of adding a second row", () => {
    const state = reduceEvents(
      initialThread,
      log(
        started(),
        { type: "tool_call", turn_id: T, tool_id: "tu_1", name: "Write", kind: "edit", title: "Write index.html", input: { file_path: "index.html" } },
        { type: "thought_chunk", turn_id: T, message_id: "m:0", text: "still writing" },
        { type: "tool_call", turn_id: T, tool_id: "tu_1", name: "Write", kind: "edit", title: "Write index.html", input: { file_path: "index.html", content: "<h1>x</h1>" } },
        { type: "tool_result", turn_id: T, tool_id: "tu_1", status: "ok", summary: "done" },
      ),
    );
    const tools = allTools(lastTurn(state)!.items);
    expect(tools).toHaveLength(1);
    expect(tools[0].input).toEqual({ file_path: "index.html", content: "<h1>x</h1>" });
    expect(tools[0].status).toBe("ok");
    expect(lastTurn(state)!.items[0].type).toBe("tool");
  });
});

// ---- Step 2 ---------------------------------------------------------------------------

const queued = (queued_id: string, text = "Then this", extra: Record<string, unknown> = {}): Loose => ({
  type: "turn_queued",
  turn_id: null,
  queued_id,
  text,
  selection: null,
  attachments: [],
  connection: "fake",
  model: null,
  ...extra,
});
const unqueued = (queued_id: string, reason = "cancelled"): Loose => ({
  type: "turn_unqueued",
  turn_id: null,
  queued_id,
  reason,
});
const ask = (approval_id: string, extra: Record<string, unknown> = {}, turn_id = T): Loose => ({
  type: "approval_request",
  turn_id,
  approval_id,
  tool: "Bash",
  title: "Run a shell command",
  detail: "sudo rm -rf /tmp/x",
  reason: "Uses sudo",
  ...extra,
});
const resolved = (approval_id: string, decision = "allow", by = "user", turn_id: string | null = T): Loose => ({
  type: "approval_resolved",
  turn_id,
  approval_id,
  decision,
  by,
});
const appStatus = (state: string, exit_code: number | null = null, turn_id: string | null = null): Loose => ({
  type: "app_status",
  turn_id,
  state,
  exit_code,
  run: "uvicorn server:app",
});

function approvals(turn: Turn): ApprovalItem[] {
  return turn.items.filter((item): item is ApprovalItem => item.type === "approval");
}

describe("turn_started additions", () => {
  it("records attachments and the queued id", () => {
    const turn = onlyTurn(build(started("Use this", { attachments: ["attachments/a.png", "data.csv"], queued_id: "q1" })));
    expect(turn.attachments).toEqual(["attachments/a.png", "data.csv"]);
    expect(turn.queuedId).toBe("q1");
  });

  it("defaults to no attachments for Step 1 events", () => {
    const turn = onlyTurn(build(started()));
    expect(turn.attachments).toEqual([]);
    expect(turn.queuedId).toBeNull();
  });

  it("drops malformed attachment entries", () => {
    const turn = onlyTurn(build(started("x", { attachments: ["ok.txt", 4, null, ""] })));
    expect(turn.attachments).toEqual(["ok.txt"]);
  });
});

describe("queue", () => {
  it("keeps queued messages in the order they were queued, with their fields", () => {
    const selection = { path: "/", selector: "h1", tag: "h1", text: "T", html: "<h1>T</h1>" };
    const state = build(
      started(),
      queued("q1", "first", { attachments: ["attachments/x.png"] }),
      queued("q2", "second", { selection }),
    );
    expect(state.queue.map((item) => item.queuedId)).toEqual(["q1", "q2"]);
    expect(state.queue[0].attachments).toEqual(["attachments/x.png"]);
    expect(state.queue[1].selection).toEqual(selection);
    expect(state.queue[0].text).toBe("first");
  });

  it("removes a queued message when a turn starts with its queued_id, leaving the rest in order", () => {
    const state = build(
      started(),
      queued("q1", "first"),
      queued("q2", "second"),
      finished(),
      { ...started("first", { queued_id: "q1" }), turn_id: "turn_b" },
    );
    expect(state.queue.map((item) => item.queuedId)).toEqual(["q2"]);
    expect(state.runningTurnId).toBe("turn_b");
    const turns = state.entries.filter((entry): entry is Turn => entry.type === "turn");
    expect(turns.map((turn) => turn.text)).toEqual(["Build a page", "first"]);
    expect(turns[1].queuedId).toBe("q1");
  });

  it("does not consume the queue for a turn without a queued_id", () => {
    const state = build(started(), queued("q1"), finished(), { ...started("direct"), turn_id: "turn_b" });
    expect(state.queue.map((item) => item.queuedId)).toEqual(["q1"]);
  });

  it("ignores a duplicate turn_queued", () => {
    const state = build(started(), queued("q1", "a"), queued("q1", "b"));
    expect(state.queue).toHaveLength(1);
    expect(state.queue[0].text).toBe("a");
  });

  it("removes a cancelled message without a notice", () => {
    const state = build(started(), queued("q1"), queued("q2"), unqueued("q1"));
    expect(state.queue.map((item) => item.queuedId)).toEqual(["q2"]);
    expect(state.entries.map((entry) => entry.type)).toEqual(["turn"]);
  });

  it("says so when a queued message was dropped by a restart", () => {
    const state = build(started(), queued("q1", "make it blue"), finished("failed"), unqueued("q1", "restart"));
    expect(state.queue).toEqual([]);
    const notice = state.entries[state.entries.length - 1];
    expect(notice.type).toBe("notice");
    expect(notice.type === "notice" && notice.message).toContain("make it blue");
    expect(notice.type === "notice" && notice.level).toBe("warning");
  });

  it("says what Stop removed and keeps its text to put back", () => {
    const state = build(started(), queued("q1", "make it blue"), unqueued("q1", "stopped"), finished("stopped"));
    expect(state.queue).toEqual([]);
    const notice = state.entries.find((entry) => entry.type === "notice");
    expect(notice?.type === "notice" && notice.message).toContain("make it blue");
    expect(notice?.type === "notice" && notice.message).toContain("Stop");
    expect(notice?.type === "notice" && notice.restoreText).toBe("make it blue");
    expect(notice?.type === "notice" && notice.level).toBe("info");
  });

  it("says when what Stop removed had files that are not put back", () => {
    const state = build(started(), queued("q1", "use this", { attachments: ["attachments/a.png"] }), unqueued("q1", "stopped"));
    const notice = state.entries.find((entry) => entry.type === "notice");
    expect(notice?.type === "notice" && notice.message).toContain("attached file");
  });

  it("names exactly what is not put back: the selected element and each attached file", () => {
    const selection = { path: "index.html", selector: "h1", tag: "h1", text: "Welcome", html: "<h1>Welcome</h1>" };
    const state = build(
      started(),
      queued("q1", "make the heading red", { selection }),
      queued("q2", "use these notes", { attachments: ["attachments/notes.txt", "index.html"] }),
      unqueued("q1", "stopped"),
      unqueued("q2", "stopped"),
    );
    const notices = state.entries.filter((entry) => entry.type === "notice");
    const [heading, notes] = notices.map((entry) => (entry.type === "notice" ? entry.message : ""));
    expect(heading).toContain("selected element");
    expect(heading).toContain("<h1>");
    expect(heading).toContain("Welcome");
    expect(notes).toContain("notes.txt and index.html");
    expect(notes).not.toContain("attachments/");
  });

  it("stops offering a note's text once it was sent again, also after a reload", () => {
    const state = build(
      started(),
      queued("q1", "make it blue"),
      unqueued("q1", "stopped"),
      finished("stopped"),
      { ...started("make it blue"), turn_id: "turn_b" },
    );
    const notice = state.entries.find((entry) => entry.type === "notice");
    expect(notice?.type === "notice" && notice.restoreText).toBe("make it blue");
    expect(notice?.type === "notice" && notice.resent).toBe(true);
  });

  it("says when a queued message could not start, and keeps its text to put back", () => {
    const state = build(started(), queued("q1", "make it blue"), finished(), unqueued("q1", "not_started"));
    expect(state.queue).toEqual([]);
    const notice = state.entries.find((entry) => entry.type === "notice");
    expect(notice?.type === "notice" && notice.message).toContain("could not start");
    expect(notice?.type === "notice" && notice.message).toContain("make it blue");
    expect(notice?.type === "notice" && notice.restoreText).toBe("make it blue");
    expect(notice?.type === "notice" && notice.level).toBe("warning");
  });

  it("ignores turn_unqueued for an unknown id", () => {
    const before = build(started(), queued("q1"));
    const after = reduceEvent(before, { ...log(unqueued("nope"))[0], seq: before.lastSeq + 1 });
    expect(after.queue).toEqual(before.queue);
    expect(after.entries).toBe(before.entries);
  });

  it("is the same replayed all at once or one event at a time", () => {
    const events = log(
      started(),
      queued("q1"),
      queued("q2"),
      unqueued("q2"),
      queued("q3"),
      finished(),
      { ...started("Then this", { queued_id: "q1" }), turn_id: "turn_b" },
    );
    const all = reduceEvents(initialThread, events);
    let one = initialThread;
    for (const event of events) one = reduceEvent(one, event);
    expect(one).toEqual(all);
    expect(all.queue.map((item) => item.queuedId)).toEqual(["q3"]);
  });
});

describe("approvals", () => {
  it("carries save_skill's structured skill to the card, and none for older events", () => {
    const skill = { description: "d", body: "b", files: [], scripts: [{ path: "s.sh", text: "echo", lines: 1, replaces: false }], removed: [], kept: [], kept_scripts: [] };
    const [withSkill] = approvals(onlyTurn(build(started(), ask("a1", { tool: "mcp__studio__save_skill", skill }))));
    expect(withSkill.skill).toEqual(skill);
    const [older] = approvals(onlyTurn(build(started(), ask("a1"))));
    expect(older.skill).toBeNull();
  });

  it("adds a waiting approval to the turn's activity, in order", () => {
    const state = build(started(), call("t1", "read", "Read index.html"), result("t1"), ask("a1"));
    const turn = onlyTurn(state);
    expect(turn.items.map((item) => item.type)).toEqual(["tool", "approval"]);
    const [item] = approvals(turn);
    expect(item).toMatchObject({
      approvalId: "a1",
      turnId: T,
      title: "Run a shell command",
      detail: "sudo rm -rf /tmp/x",
      reason: "Uses sudo",
      status: "waiting",
      by: null,
    });
    expect(waitingApprovals(state).map((a) => a.approvalId)).toEqual(["a1"]);
    expect(latestActivity(state)).toBe("Waiting for your approval");
  });

  it("records an allow by the user", () => {
    const state = build(started(), ask("a1"), resolved("a1", "allow"));
    expect(approvals(onlyTurn(state))[0]).toMatchObject({ status: "allowed", by: "user" });
    expect(waitingApprovals(state)).toEqual([]);
  });

  it("records a deny by the user", () => {
    const state = build(started(), ask("a1"), resolved("a1", "deny"));
    expect(approvals(onlyTurn(state))[0]).toMatchObject({ status: "denied", by: "user" });
  });

  it("records a deny because the turn ended first", () => {
    const state = build(started(), ask("a1"), resolved("a1", "deny", "turn_end"), finished("stopped"));
    expect(approvals(onlyTurn(state))[0]).toMatchObject({ status: "denied", by: "turn_end" });
  });

  it("closes an approval the turn left unanswered at turn_finished", () => {
    const state = build(started(), ask("a1"), finished("failed", { error: "boom" }));
    expect(approvals(onlyTurn(state))[0]).toMatchObject({ status: "denied", by: "turn_end" });
    expect(waitingApprovals(state)).toEqual([]);
  });

  it("does not change an approval already resolved when the turn ends", () => {
    const state = build(started(), ask("a1"), resolved("a1", "allow"), finished());
    expect(approvals(onlyTurn(state))[0]).toMatchObject({ status: "allowed", by: "user" });
  });

  it("finds the approval even when approval_resolved has no turn_id", () => {
    const state = build(started(), ask("a1"), resolved("a1", "allow", "user", null));
    expect(approvals(onlyTurn(state))[0].status).toBe("allowed");
  });

  it("ignores a resolution for an approval it never saw", () => {
    const state = build(started(), resolved("zzz"));
    expect(onlyTurn(state).items).toEqual([]);
  });

  it("does not add a second card for a repeated request", () => {
    const state = build(started(), ask("a1"), ask("a1", { detail: "sudo ls" }));
    const list = approvals(onlyTurn(state));
    expect(list).toHaveLength(1);
    expect(list[0].detail).toBe("sudo ls");
  });

  it("keeps an approval outside folded tool groups after the turn", () => {
    const state = build(
      started(),
      call("t1", "read", "Read a"),
      result("t1"),
      call("t2", "read", "Read b"),
      result("t2"),
      ask("a1"),
      resolved("a1"),
      call("t3", "execute", "Run x"),
      result("t3"),
      finished(),
    );
    const view = turnViewItems(onlyTurn(state));
    expect(view.map((item) => item.type)).toEqual(["group", "approval", "tool"]);
  });

  it("handles several approvals in one turn independently", () => {
    const state = build(started(), ask("a1"), resolved("a1", "deny"), ask("a2"));
    const list = approvals(onlyTurn(state));
    expect(list.map((item) => item.status)).toEqual(["denied", "waiting"]);
    expect(waitingApprovals(state).map((a) => a.approvalId)).toEqual(["a2"]);
  });
});

describe("app status", () => {
  it("starts unknown", () => {
    expect(initialThread.app).toBeNull();
  });

  it("follows app_status events, last one wins", () => {
    const state = build(appStatus("starting"), appStatus("running"));
    expect(state.app).toMatchObject({ configured: true, state: "running", exitCode: null, run: "uvicorn server:app" });
  });

  it("keeps the exit code of a crash", () => {
    const state = build(appStatus("starting"), appStatus("crashed", 1));
    expect(state.app).toMatchObject({ state: "crashed", exitCode: 1 });
  });

  it("is not configured when the state is none", () => {
    const state = build(appStatus("running"), appStatus("none"));
    expect(state.app?.configured).toBe(false);
  });

  it("does not create a turn for an app_status with a turn_id", () => {
    const state = build(started(), appStatus("running", null, T));
    expect(onlyTurn(state).items).toEqual([]);
    expect(state.app?.state).toBe("running");
  });

  it("takes a seed when there is no newer event", () => {
    const seeded = seedApp(initialThread, { configured: true, state: "stopped", exit_code: null, run: "x" }, 0);
    expect(seeded.app).toMatchObject({ configured: true, state: "stopped", seq: 0 });
  });

  it("lets a later event replace a seed", () => {
    const seeded = seedApp(initialThread, { configured: true, state: "stopped", exit_code: null, run: "x" }, 0);
    const state = reduceEvents(seeded, log(appStatus("running")));
    expect(state.app?.state).toBe("running");
  });

  it("keeps an event that arrived after the seed was requested", () => {
    const state = build(appStatus("starting"), appStatus("running"));
    const seeded = seedApp(state, { configured: true, state: "starting", exit_code: null, run: "x" }, 1);
    expect(seeded.app?.state).toBe("running");
    expect(seeded).toBe(state);
  });

  it("lets a seed requested after the last event replace it, and ignores events up to that seq", () => {
    const state = build(appStatus("starting"));
    const seeded = seedApp(state, { configured: true, state: "running", exit_code: null, run: "x" }, 1);
    expect(seeded.app?.state).toBe("running");
  });

  it("ignores an older event that is applied after a newer seed", () => {
    // The GET answered with what was current at seq 5; event 3 is reduced afterwards (it was batched).
    const seeded = seedApp(initialThread, { configured: true, state: "running", exit_code: null, run: "x" }, 5);
    const state = reduceEvents(seeded, log(started(), chunk("m", "x"), appStatus("starting")));
    expect(state.app?.state).toBe("running");
  });
});

describe("tool calls still work alongside Step 2 events", () => {
  it("fills an early tool_call in place even with an approval in between", () => {
    const state = build(
      started(),
      { type: "tool_call", turn_id: T, tool_id: "tu_1", name: "Bash", kind: "execute", title: "Run", input: {} },
      ask("a1"),
      resolved("a1"),
      { type: "tool_call", turn_id: T, tool_id: "tu_1", name: "Bash", kind: "execute", title: "Run sudo ls", input: { command: "sudo ls" } },
      { type: "tool_call", turn_id: T, tool_id: "tu_1", name: "Bash", kind: "execute", title: "Run sudo ls", input: { command: "sudo ls" } },
      result("tu_1"),
    );
    const tools = allTools(onlyTurn(state).items);
    expect(tools).toHaveLength(1);
    expect(tools[0]).toMatchObject({ title: "Run sudo ls", status: "ok" });
    expect(onlyTurn(state).items.map((item) => item.type)).toEqual(["tool", "approval"]);
  });

  it("keeps cost_estimated in usage only when the event carries it", () => {
    const withFlag = onlyTurn(
      build(started(), finished("completed", { usage: { input_tokens: 1, output_tokens: 2, cost_usd: 1.02, duration_ms: 5, cost_estimated: true } })),
    );
    expect(withFlag.usage?.cost_estimated).toBe(true);
    const without = onlyTurn(build(started(), finished()));
    expect("cost_estimated" in (without.usage ?? {})).toBe(false);
  });
});

describe("studio tools in summaries", () => {
  it("names the page check instead of counting it as a read", () => {
    const state = build(
      started(),
      call("t1", "read", "Read index.html"),
      result("t1"),
      { ...call("t2", "read", "Check citations"), name: "mcp__studio__check_page" },
      result("t2"),
      finished(),
    );
    const group = turnViewItems(onlyTurn(state))[0] as ActivityGroup;
    expect(group.summary).toBe("Read 1 file, checked the page");
  });
});

describe("office rendering in summaries", () => {
  it("names render_document by what it does", () => {
    const state = build(
      started(),
      { ...call("t1", "other", "Render files/deck.pptx for viewing"), name: "mcp__studio__render_document" },
      result("t1"),
      { ...call("t2", "edit", "Write tools/build.py") },
      result("t2"),
      finished(),
    );
    const group = turnViewItems(onlyTurn(state))[0] as ActivityGroup;
    expect(group.summary).toBe("Edited 1 file, rendered 1 file for viewing");
    expect(allTools(onlyTurn(state).items)[0].title).toBe("Render files/deck.pptx for viewing");
  });
});

describe("saving a template in summaries", () => {
  it("names save_template by what it does and keeps its title", () => {
    const state = build(
      started(),
      { ...call("t1", "other", "Save the template “Report look”"), name: "mcp__studio__save_template" },
      result("t1"),
      call("t2", "read", "Read index.html"),
      result("t2"),
      finished(),
    );
    const group = turnViewItems(onlyTurn(state))[0] as ActivityGroup;
    expect(group.summary).toBe("Read 1 file, saved a template");
    expect(allTools(onlyTurn(state).items)[0].title).toBe("Save the template “Report look”");
  });
});

describe("turn_cost", () => {
  const started: Loose = { type: "turn_started", turn_id: "turn_g", text: "hi", selection: null, connection: "mine", model: "gpt-x" };
  const finished: Loose = {
    type: "turn_finished",
    turn_id: "turn_g",
    status: "completed",
    error: null,
    session_id: "s",
    usage: { input_tokens: 1200, output_tokens: 80, cost_usd: null, duration_ms: 900, cost_estimated: false, cost_pending: true },
  };

  it("keeps a gateway turn pending until its price is read back", () => {
    const state = build(started, finished);
    expect(lastTurn(state)?.usage).toMatchObject({ cost_usd: null, cost_pending: true });
  });

  it("replaces the pending figure with what the gateway reported", () => {
    const usage = lastTurn(
      build(started, finished, {
        type: "turn_cost",
        turn_id: "turn_g",
        cost_usd: 4.446269,
        requests: 193,
        unpriced: 1,
        source: "recovered",
        reason: null,
      }),
    )?.usage;
    expect(usage).toMatchObject({ cost_usd: 4.446269, cost_estimated: false, unpriced_requests: 1, cost_source: "recovered" });
    expect(usage?.cost_pending).toBeUndefined();
    expect(usage?.input_tokens).toBe(1200);
  });

  it("keeps no figure, and the reason, when the gateway could not say", () => {
    const state = build(started, finished, {
      type: "turn_cost",
      turn_id: "turn_g",
      cost_usd: null,
      requests: 0,
      unpriced: 0,
      source: "unavailable",
      reason: "The gateway has no spend log.",
    });
    expect(lastTurn(state)?.usage).toMatchObject({
      cost_usd: null,
      cost_reason: "The gateway has no spend log.",
    });
  });

  it("ignores a price for a turn it has never seen", () => {
    const state = build({ type: "turn_cost", turn_id: "turn_x", cost_usd: 1, requests: 1, unpriced: 0, source: "gateway", reason: null });
    expect(state.entries).toHaveLength(0);
    expect(state.runningTurnId).toBeNull();
  });
});

describe("a conversation's running cost total", () => {
  const turn = (id: string, cost: number, extra: Record<string, unknown> = {}, session = "s1"): Loose[] => [
    { type: "turn_started", turn_id: id, text: "x", selection: null, connection: "subscription", model: "opus" },
    {
      type: "turn_finished",
      turn_id: id,
      status: "completed",
      error: null,
      session_id: session,
      usage: { input_tokens: 1, output_tokens: 1, cost_usd: cost, duration_ms: 1, cost_estimated: true, ...extra },
    },
  ];
  const costs = (state: ThreadState) => state.entries.map((entry) => (entry as Turn).usage?.cost_usd);

  it("shows each older turn its own part, not the conversation's total so far", () => {
    expect(costs(build(...turn("a", 10.6), ...turn("b", 10.99), ...turn("c", 11.65)))).toEqual([10.6, 0.39, 0.66]);
  });

  it("starts again with a new conversation", () => {
    expect(costs(build(...turn("a", 10.6), ...turn("b", 0.5, {}, "s2"), ...turn("c", 0.7, {}, "s2")))).toEqual([10.6, 0.5, 0.2]);
  });

  it("takes a newer turn's figure as it is, and carries on from its total", () => {
    const state = build(...turn("a", 10.6), ...turn("b", 0.39, { session_cost_usd: 10.99 }), ...turn("c", 0.66, { session_cost_usd: 11.65 }));
    expect(costs(state)).toEqual([10.6, 0.39, 0.66]);
  });

  it("carries on from a gateway turn written before Step 2, whose figure was the running total", () => {
    // Before Step 2 no turn said `cost_estimated`, and a gateway turn's cost_usd was the program's total.
    const old = (id: string, cost: number): Loose[] => {
      const [started, finished] = turn(id, cost);
      const usage = { ...(finished.usage as Record<string, unknown>) };
      delete usage.cost_estimated;
      return [{ ...started, connection: "gateway" }, { ...finished, usage }];
    };
    const state = build(...turn("a", 1.0), ...old("g0", 0), ...old("g", 2.5), ...turn("b", 3.0));
    expect(costs(state)[3]).toBeCloseTo(0.5);
  });
});

