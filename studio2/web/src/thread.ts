// The heart of the UI: a pure reducer from the project's event log to the chat
// thread model. Replaying the stored log and receiving the same events live
// must produce an identical model, so nothing here reads the clock, the DOM
// or anything other than its arguments.

import { recheckFrom, verificationItem, type VerificationItem } from "./effort";
import { stepsFrom, type NextStep } from "./nextSteps";
import { asPick } from "./libraryPick";
import type { FileChange, LibraryPick, PlanEntry, Selection, StudioEvent, Usage } from "./types";

export type TurnStatus = "running" | "completed" | "stopped" | "failed";
export type ToolStatus = "running" | "ok" | "error" | "cancelled";

export interface ToolNode {
  toolId: string;
  name: string;
  kind: string;
  title: string;
  input: unknown;
  parentToolId: string | null;
  status: ToolStatus;
  summary: string | null;
  seq: number;
  /** Calls made inside this call when it is a sub-agent. */
  children: ToolNode[];
}

export interface MessageItem {
  type: "message";
  key: string;
  messageId: string;
  text: string;
}
export interface ThoughtItem {
  type: "thought";
  key: string;
  messageId: string;
  text: string;
}
export interface ToolItem {
  type: "tool";
  key: string;
  tool: ToolNode;
}
export interface CheckpointItem {
  type: "checkpoint";
  key: string;
  sha: string;
  kind: string;
  message: string;
  files: FileChange[];
  ts: string;
}
export interface NoticeItem {
  type: "notice";
  key: string;
  level: string;
  message: string;
  ts: string;
}

export type ApprovalStatus = "waiting" | "allowed" | "denied";

/** The agent asked before doing something risky (`approval_request` … `approval_resolved`). */
export interface ApprovalItem {
  type: "approval";
  key: string;
  approvalId: string;
  turnId: string;
  tool: string;
  title: string;
  detail: string;
  reason: string;
  status: ApprovalStatus;
  /** Who decided: `user`, or `turn_end` when the turn ended first. Null while waiting. */
  by: "user" | "turn_end" | null;
  seq: number;
}

export type TurnItem = MessageItem | ThoughtItem | ToolItem | CheckpointItem | NoticeItem | ApprovalItem | VerificationItem;

/** A message waiting for the running turn to end (`turn_queued`). */
export interface QueuedMessage {
  queuedId: string;
  seq: number;
  ts: string;
  text: string;
  selection: Selection | null;
  attachments: string[];
  connection: string | null;
  model: string | null;
}

/** The project's app as last reported: an `app_status` event, or a `GET …/app` seed. */
export interface AppStatus {
  configured: boolean;
  state: string;
  exitCode: number | null;
  run: string | null;
  /** The event sequence this status is current as of (a seed: the last seq seen when it was requested). */
  seq: number;
}

export interface Turn {
  type: "turn";
  key: string;
  id: string;
  seq: number;
  startedAt: string;
  text: string;
  selection: Selection | null;
  connection: string | null;
  model: string | null;
  /** Workspace paths attached to the user's message. */
  attachments: string[];
  /** Set when the turn started from a queued message. */
  queuedId: string | null;
  status: TurnStatus;
  error: string | null;
  usage: Usage | null;
  finishedAt: string | null;
  /** The latest plan; each `plan` event replaces the previous one. */
  plan: PlanEntry[] | null;
  items: TurnItem[];
  /** Workspace paths reported by `files_changed` during this turn. */
  changedPaths: string[];
  /** Step 3o: what the studio picked from the library for this request. */
  pick?: LibraryPick | null;
  /** Suggested next steps (`next_steps`), shown as chips while this is the last request. */
  nextSteps?: NextStep[];
}

/** Top-level thread entries: turns, plus checkpoints and notices that belong to no turn. */
export type ThreadEntry = Turn | CheckpointItem | NoticeItem;

export interface ThreadState {
  /** Highest sequence number applied. Events at or below it are duplicates. */
  lastSeq: number;
  entries: ThreadEntry[];
  runningTurnId: string | null;
  /** Bumps whenever the workspace changed on disk (`files_changed`, `checkpoint`). */
  filesRev: number;
  /** Bumps whenever a checkpoint was created. */
  versionsRev: number;
  /** Messages waiting behind the running turn, oldest first (they run in this order). */
  queue: QueuedMessage[];
  /** The project's app, or null until an `app_status` event or a seed arrives. */
  app: AppStatus | null;
  /** The running cost total the last finished turn reported for its conversation (see `turn_finished`). */
  sessionCost?: { id: string; total: number } | null;
}

export const initialThread: ThreadState = {
  lastSeq: 0,
  entries: [],
  runningTurnId: null,
  filesRev: 0,
  versionsRev: 0,
  queue: [],
  app: null,
};

// ---- helpers ---------------------------------------------------------------

function placeholderTurn(id: string, seq: number, ts: string): Turn {
  return {
    type: "turn",
    key: `turn:${id}`,
    id,
    seq,
    startedAt: ts,
    text: "",
    selection: null,
    connection: null,
    model: null,
    attachments: [],
    queuedId: null,
    status: "running",
    error: null,
    usage: null,
    finishedAt: null,
    plan: null,
    items: [],
    changedPaths: [],
    pick: null,
  };
}

function findTurnIndex(entries: ThreadEntry[], turnId: string): number {
  for (let i = entries.length - 1; i >= 0; i--) {
    const entry = entries[i];
    if (entry.type === "turn" && entry.id === turnId) return i;
  }
  return -1;
}

/**
 * Apply `fn` to the turn with `turnId`. An event for a turn we never saw start
 * (only possible if the log was read from the middle) creates a placeholder so
 * nothing is silently dropped.
 */
function withTurn(state: ThreadState, turnId: string, seq: number, ts: string, fn: (turn: Turn) => Turn): ThreadState {
  const entries = state.entries.slice();
  let index = findTurnIndex(entries, turnId);
  let runningTurnId = state.runningTurnId;
  if (index === -1) {
    entries.push(placeholderTurn(turnId, seq, ts));
    index = entries.length - 1;
    runningTurnId = turnId;
  }
  entries[index] = fn(entries[index] as Turn);
  return { ...state, entries, runningTurnId };
}

function updateToolNode(node: ToolNode, toolId: string, fn: (tool: ToolNode) => ToolNode): ToolNode | null {
  if (node.toolId === toolId) return fn(node);
  for (let i = node.children.length - 1; i >= 0; i--) {
    const next = updateToolNode(node.children[i], toolId, fn);
    if (next) {
      const children = node.children.slice();
      children[i] = next;
      return { ...node, children };
    }
  }
  return null;
}

/** Returns new items with the tool updated, or null when no such tool exists. */
function updateTool(items: TurnItem[], toolId: string, fn: (tool: ToolNode) => ToolNode): TurnItem[] | null {
  for (let i = items.length - 1; i >= 0; i--) {
    const item = items[i];
    if (item.type !== "tool") continue;
    const next = updateToolNode(item.tool, toolId, fn);
    if (next) {
      const copy = items.slice();
      copy[i] = { ...item, tool: next };
      return copy;
    }
  }
  return null;
}

function cancelRunning(node: ToolNode): ToolNode {
  const children = node.children.map(cancelRunning);
  const changed = children.some((child, i) => child !== node.children[i]);
  if (node.status !== "running" && !changed) return node;
  return { ...node, status: node.status === "running" ? "cancelled" : node.status, children };
}

function appendChunk(
  items: TurnItem[],
  type: "message" | "thought",
  messageId: string,
  text: string,
  seq: number,
): TurnItem[] {
  const last = items[items.length - 1];
  // Chunks extend the open segment of their message. If something else (a tool
  // call, a different message) arrived in between, the text continues in a new
  // segment so the thread stays in the order things happened.
  if (last && last.type === type && last.messageId === messageId) {
    const copy = items.slice();
    copy[copy.length - 1] = { ...last, text: last.text + text };
    return copy;
  }
  const prefix = type === "message" ? "msg" : "thought";
  return [...items, { type, key: `${prefix}:${messageId}:${seq}`, messageId, text }];
}

function normalizeUsage(usage: Partial<Usage> | null | undefined): Usage | null {
  if (!usage) return null;
  const num = (value: unknown) => (typeof value === "number" && Number.isFinite(value) ? value : null);
  const normalized: Usage = {
    input_tokens: num(usage.input_tokens),
    output_tokens: num(usage.output_tokens),
    cost_usd: num(usage.cost_usd),
    duration_ms: num(usage.duration_ms),
  };
  // Only a real boolean counts; a missing flag stays missing (see `costLabel`).
  if (typeof usage.cost_estimated === "boolean") normalized.cost_estimated = usage.cost_estimated;
  if (usage.cost_pending === true) normalized.cost_pending = true;
  if (typeof usage.cost_basis === "string") normalized.cost_basis = usage.cost_basis;
  if (usage.backup && typeof usage.backup === "object") normalized.backup = usage.backup;
  const unpriced = num(usage.unpriced_requests);
  if (unpriced !== null) normalized.unpriced_requests = unpriced;
  return normalized;
}

function strings(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string" && item !== "") : [];
}

function updateApproval(
  items: TurnItem[],
  approvalId: string,
  fn: (item: ApprovalItem) => ApprovalItem,
): TurnItem[] | null {
  const index = items.findIndex((item) => item.type === "approval" && item.approvalId === approvalId);
  if (index === -1) return null;
  const copy = items.slice();
  copy[index] = fn(items[index] as ApprovalItem);
  return copy;
}

function normalizeStatus(status: string): TurnStatus {
  return status === "completed" || status === "stopped" || status === "failed" ? status : "failed";
}

// ---- reducer ---------------------------------------------------------------

export function reduceEvent(state: ThreadState, event: StudioEvent): ThreadState {
  if (!event || typeof event.seq !== "number" || event.seq <= state.lastSeq) return state;
  const next = applyEvent(state, event);
  return next.lastSeq === event.seq ? next : { ...next, lastSeq: event.seq };
}

export function reduceEvents(state: ThreadState, events: readonly StudioEvent[]): ThreadState {
  let next = state;
  for (const event of events) next = reduceEvent(next, event);
  return next;
}

function applyEvent(state: ThreadState, event: StudioEvent): ThreadState {
  const { seq, ts } = event;
  switch (event.type) {
    case "turn_started": {
      const id = event.turn_id ?? `seq-${seq}`;
      const queuedId = typeof event.queued_id === "string" && event.queued_id ? event.queued_id : null;
      const turn: Turn = {
        ...placeholderTurn(id, seq, ts),
        text: event.text ?? "",
        selection: event.selection ?? null,
        connection: event.connection ?? null,
        model: event.model ?? null,
        attachments: strings(event.attachments),
        queuedId,
      };
      // A queued message leaves the queue when its turn starts.
      const queue = queuedId ? state.queue.filter((item) => item.queuedId !== queuedId) : state.queue;
      const existing = findTurnIndex(state.entries, id);
      if (existing !== -1) {
        // A placeholder was created by an earlier stray event: fill it in.
        const entries = state.entries.slice();
        const prior = entries[existing] as Turn;
        entries[existing] = {
          ...prior,
          text: turn.text,
          selection: turn.selection,
          connection: turn.connection,
          model: turn.model,
          attachments: turn.attachments,
          queuedId: turn.queuedId,
        };
        return { ...state, entries, queue, runningTurnId: prior.status === "running" ? id : state.runningTurnId };
      }
      return { ...state, entries: [...state.entries, turn], queue, runningTurnId: id };
    }

    case "library_picked": {
      if (!event.turn_id) return state;
      const pick = asPick(event);
      return withTurn(state, event.turn_id, seq, ts, (turn) => ({ ...turn, pick }));
    }

    case "turn_queued": {
      const queuedId = typeof event.queued_id === "string" ? event.queued_id : "";
      if (!queuedId || state.queue.some((item) => item.queuedId === queuedId)) return state;
      // A turn that already started from this message (it can only come later) means it is not waiting.
      if (state.entries.some((entry) => entry.type === "turn" && entry.queuedId === queuedId)) return state;
      const item: QueuedMessage = {
        queuedId,
        seq,
        ts,
        text: event.text ?? "",
        selection: event.selection ?? null,
        attachments: strings(event.attachments),
        connection: event.connection ?? null,
        model: event.model ?? null,
      };
      return { ...state, queue: [...state.queue, item] };
    }

    case "turn_unqueued": {
      const removed = state.queue.find((item) => item.queuedId === event.queued_id);
      if (!removed) return state;
      const queue = state.queue.filter((item) => item !== removed);
      if (event.reason !== "restart") return { ...state, queue };
      // Removed by a restart, not by the user: say so, or the message silently vanishes.
      const notice: NoticeItem = {
        type: "notice",
        key: `unqueued:${seq}`,
        level: "warning",
        message: `A queued message was not sent because the studio restarted: “${removed.text}”`,
        ts,
      };
      return { ...state, queue, entries: [...state.entries, notice] };
    }

    case "approval_request": {
      if (!event.turn_id || !event.approval_id) return state;
      const turnId = event.turn_id;
      const fields = {
        tool: event.tool ?? "",
        title: event.title || "Approve an action",
        detail: event.detail ?? "",
        reason: event.reason ?? "",
      };
      return withTurn(state, turnId, seq, ts, (turn) => {
        const repeated = updateApproval(turn.items, event.approval_id, (item) => ({ ...item, ...fields }));
        if (repeated) return { ...turn, items: repeated };
        const item: ApprovalItem = {
          type: "approval",
          key: `approval:${event.approval_id}`,
          approvalId: event.approval_id,
          turnId,
          ...fields,
          status: "waiting",
          by: null,
          seq,
        };
        return { ...turn, items: [...turn.items, item] };
      });
    }

    case "approval_resolved": {
      if (!event.approval_id) return state;
      const resolve = (item: ApprovalItem): ApprovalItem => ({
        ...item,
        status: event.decision === "allow" ? "allowed" : "denied",
        by: event.by === "turn_end" ? "turn_end" : "user",
      });
      // Look in the named turn first, then anywhere (a resolution is never dropped for a missing turn_id).
      const order: number[] = [];
      if (event.turn_id) {
        const index = findTurnIndex(state.entries, event.turn_id);
        if (index !== -1) order.push(index);
      }
      for (let i = state.entries.length - 1; i >= 0; i--) if (!order.includes(i)) order.push(i);
      for (const index of order) {
        const entry = state.entries[index];
        if (entry.type !== "turn") continue;
        const items = updateApproval(entry.items, event.approval_id, resolve);
        if (!items) continue;
        const entries = state.entries.slice();
        entries[index] = { ...entry, items };
        return { ...state, entries };
      }
      return state;
    }

    case "app_status": {
      if (state.app && state.app.seq >= seq) return state;
      const appState = typeof event.state === "string" ? event.state : "none";
      return {
        ...state,
        app: {
          configured: appState !== "none",
          state: appState,
          exitCode: typeof event.exit_code === "number" ? event.exit_code : null,
          run: typeof event.run === "string" ? event.run : null,
          seq,
        },
      };
    }

    case "message_chunk":
    case "thought_chunk": {
      if (!event.turn_id || !event.text) return state;
      const type = event.type === "message_chunk" ? "message" : "thought";
      return withTurn(state, event.turn_id, seq, ts, (turn) => ({
        ...turn,
        items: appendChunk(turn.items, type, event.message_id, event.text, seq),
      }));
    }

    case "tool_call": {
      if (!event.turn_id) return state;
      const node: ToolNode = {
        toolId: event.tool_id,
        name: event.name ?? "",
        kind: event.kind ?? "other",
        title: event.title || event.name || "Tool",
        input: event.input ?? null,
        parentToolId: event.parent_tool_id ?? null,
        status: "running",
        summary: null,
        seq,
        children: [],
      };
      return withTurn(state, event.turn_id, seq, ts, (turn) => {
        // A harness may announce a tool early and repeat the call once its full input is
        // known. The later event fills in the same row and keeps its place in the thread.
        const repeated = updateTool(turn.items, node.toolId, (tool) => ({
          ...tool,
          name: node.name || tool.name,
          kind: node.kind,
          title: node.title,
          input: node.input,
        }));
        if (repeated) return { ...turn, items: repeated };
        if (node.parentToolId) {
          const nested = updateTool(turn.items, node.parentToolId, (parent) => ({
            ...parent,
            children: [...parent.children, node],
          }));
          if (nested) return { ...turn, items: nested };
        }
        return { ...turn, items: [...turn.items, { type: "tool", key: `tool:${node.toolId}`, tool: node }] };
      });
    }

    case "tool_result": {
      if (!event.turn_id) return state;
      return withTurn(state, event.turn_id, seq, ts, (turn) => {
        const items = updateTool(turn.items, event.tool_id, (tool) => ({
          ...tool,
          status: event.status === "error" ? "error" : "ok",
          summary: event.summary ?? null,
        }));
        return items ? { ...turn, items } : turn;
      });
    }

    case "plan": {
      if (!event.turn_id) return state;
      const entries = Array.isArray(event.entries) ? event.entries : [];
      return withTurn(state, event.turn_id, seq, ts, (turn) => ({ ...turn, plan: entries }));
    }

    case "files_changed": {
      const paths = Array.isArray(event.paths) ? event.paths : [];
      const bumped = { ...state, filesRev: state.filesRev + 1 };
      if (!event.turn_id) return bumped;
      return withTurn(bumped, event.turn_id, seq, ts, (turn) => {
        const fresh = paths.filter((path) => !turn.changedPaths.includes(path));
        return fresh.length ? { ...turn, changedPaths: [...turn.changedPaths, ...fresh] } : turn;
      });
    }

    case "checkpoint": {
      const item: CheckpointItem = {
        type: "checkpoint",
        key: `checkpoint:${seq}`,
        sha: event.sha,
        kind: event.kind,
        message: event.message ?? "",
        files: Array.isArray(event.files) ? event.files : [],
        ts,
      };
      const bumped = { ...state, filesRev: state.filesRev + 1, versionsRev: state.versionsRev + 1 };
      if (!event.turn_id) return { ...bumped, entries: [...bumped.entries, item] };
      return withTurn(bumped, event.turn_id, seq, ts, (turn) => ({ ...turn, items: [...turn.items, item] }));
    }

    case "verification": {
      const item = verificationItem(event as unknown as Record<string, unknown>, `verification:${seq}`, ts);
      if (!event.turn_id) return state;
      return withTurn(state, event.turn_id, seq, ts, (turn) => ({ ...turn, items: [...turn.items, item] }));
    }

    case "verification_recheck": {
      // Belongs to the request's last card, shown at its top; without a card there is nothing to correct.
      if (!event.turn_id || findTurnIndex(state.entries, event.turn_id) === -1) return state;
      const recheck = recheckFrom(event as unknown as Record<string, unknown>);
      return withTurn(state, event.turn_id, seq, ts, (turn) => {
        const at = turn.items.map((i) => i.type).lastIndexOf("verification");
        if (at === -1) return turn;
        const items = turn.items.slice();
        items[at] = { ...(items[at] as VerificationItem), recheck };
        return { ...turn, items };
      });
    }

    case "next_steps": {
      if (!event.turn_id || findTurnIndex(state.entries, event.turn_id) === -1) return state;
      const nextSteps = stepsFrom(event as unknown as Record<string, unknown>);
      return withTurn(state, event.turn_id, seq, ts, (turn) => ({ ...turn, nextSteps }));
    }

    case "notice": {
      const item: NoticeItem = {
        type: "notice",
        key: `notice:${seq}`,
        level: event.level ?? "info",
        message: event.message ?? "",
        ts,
      };
      if (!event.turn_id) return { ...state, entries: [...state.entries, item] };
      return withTurn(state, event.turn_id, seq, ts, (turn) => ({ ...turn, items: [...turn.items, item] }));
    }

    case "turn_cost": {
      // What the gateway's spend log says the turn cost; it replaces the turn's own (empty) figure.
      if (!event.turn_id || findTurnIndex(state.entries, event.turn_id) === -1) return state;
      const num = (value: unknown) => (typeof value === "number" && Number.isFinite(value) ? value : null);
      return withTurn(state, event.turn_id, seq, ts, (turn) => ({
        ...turn,
        usage: {
          input_tokens: turn.usage?.input_tokens ?? null,
          output_tokens: turn.usage?.output_tokens ?? null,
          duration_ms: turn.usage?.duration_ms ?? null,
          cost_usd: num(event.cost_usd),
          cost_estimated: false,
          unpriced_requests: num(event.unpriced) ?? 0,
          cost_source: event.source ?? null,
          cost_basis: event.basis ?? null,
          backup: event.backup ?? null,
          cost_reason: event.reason ?? null,
        },
      }));
    }

    case "turn_finished": {
      if (!event.turn_id) return state;
      // The agent program reports a conversation's running total. Newer turns carry their own part
      // and the total beside it; for older ones on the plan the part is worked out here.
      let usage = normalizeUsage(event.usage);
      let sessionCost = state.sessionCost ?? null;
      const session = event.session_id;
      const reported = event.usage?.session_cost_usd;
      if (usage && session && typeof reported === "number" && Number.isFinite(reported)) {
        sessionCost = { id: session, total: reported };
      } else if (usage && session && typeof usage.cost_usd === "number" && usage.cost_estimated === true) {
        const total = usage.cost_usd;
        const before = sessionCost && sessionCost.id === session && total >= sessionCost.total ? sessionCost.total : 0;
        sessionCost = { id: session, total };
        usage = { ...usage, cost_usd: Math.round((total - before) * 1e6) / 1e6 };
      }
      state = sessionCost === (state.sessionCost ?? null) ? state : { ...state, sessionCost };
      const finished = withTurn(state, event.turn_id, seq, ts, (turn) => ({
        ...turn,
        status: normalizeStatus(event.status),
        error: event.error ?? null,
        usage,
        finishedAt: ts,
        items: turn.items.map((item) => {
          // Clients close what the turn left open: running tools, and approvals nobody answered.
          if (item.type === "approval") {
            return item.status === "waiting" ? { ...item, status: "denied" as const, by: "turn_end" as const } : item;
          }
          if (item.type !== "tool") return item;
          const tool = cancelRunning(item.tool);
          return tool === item.tool ? item : { ...item, tool };
        }),
      }));
      return {
        ...finished,
        runningTurnId: finished.runningTurnId === event.turn_id ? null : finished.runningTurnId,
      };
    }

    default:
      // Unknown event types are ignored (but still advance `lastSeq`).
      return state;
  }
}

/**
 * Fold a `GET …/app` answer into the thread. `requestedAtSeq` is the thread's
 * `lastSeq` when the request was sent: an `app_status` event applied after that
 * is at least as new as the answer, so it wins; the answer replaces anything
 * older, and any later event replaces the answer.
 */
export function seedApp(
  state: ThreadState,
  app: { configured: boolean; state: string; exit_code: number | null; run: string | null },
  requestedAtSeq: number,
): ThreadState {
  if (state.app && state.app.seq > requestedAtSeq) return state;
  const next: AppStatus = {
    configured: Boolean(app.configured),
    state: typeof app.state === "string" ? app.state : "none",
    exitCode: typeof app.exit_code === "number" ? app.exit_code : null,
    run: typeof app.run === "string" ? app.run : null,
    seq: requestedAtSeq,
  };
  const prior = state.app;
  if (
    prior &&
    prior.seq === next.seq &&
    prior.configured === next.configured &&
    prior.state === next.state &&
    prior.exitCode === next.exitCode &&
    prior.run === next.run
  ) {
    return state;
  }
  return { ...state, app: next };
}

// ---- selectors (pure views over the model) -----------------------------------

export function runningTurn(state: ThreadState): Turn | null {
  if (!state.runningTurnId) return null;
  const index = findTurnIndex(state.entries, state.runningTurnId);
  return index === -1 ? null : (state.entries[index] as Turn);
}

export function lastTurn(state: ThreadState): Turn | null {
  for (let i = state.entries.length - 1; i >= 0; i--) {
    const entry = state.entries[i];
    if (entry.type === "turn") return entry;
  }
  return null;
}

function flattenTools(nodes: ToolNode[], out: ToolNode[] = []): ToolNode[] {
  for (const node of nodes) {
    out.push(node);
    flattenTools(node.children, out);
  }
  return out;
}

export function allTools(items: TurnItem[]): ToolNode[] {
  const top: ToolNode[] = [];
  for (const item of items) if (item.type === "tool") top.push(item.tool);
  return flattenTools(top);
}

/** One line describing what the running turn is doing right now, for the collapsed chat button. */
export function latestActivity(state: ThreadState): string | null {
  const turn = runningTurn(state);
  if (!turn) return null;
  if (turn.items.some((item) => item.type === "approval" && item.status === "waiting")) {
    return "Waiting for your approval";
  }
  const tools = allTools(turn.items);
  let running: ToolNode | null = null;
  let latest: ToolNode | null = null;
  for (const tool of tools) {
    if (!latest || tool.seq > latest.seq) latest = tool;
    if (tool.status === "running" && (!running || tool.seq > running.seq)) running = tool;
  }
  if (running) return running.title;
  const last = turn.items[turn.items.length - 1];
  if (last?.type === "message") return "Writing a reply";
  if (last?.type === "thought") return "Thinking";
  if (latest) return latest.title;
  return "Starting";
}

export interface ActivityGroup {
  type: "group";
  key: string;
  summary: string;
  items: (ToolItem | ThoughtItem)[];
  hasError: boolean;
}

export type TurnViewItem = TurnItem | ActivityGroup;

function plural(count: number, one: string, many: string): string {
  return `${count} ${count === 1 ? one : many}`;
}

function editTarget(tool: ToolNode): string {
  const input = tool.input as Record<string, unknown> | null;
  if (input && typeof input === "object") {
    for (const key of ["file_path", "path", "notebook_path"]) {
      const value = input[key];
      if (typeof value === "string" && value) return value;
    }
  }
  return tool.title;
}

/** "Edited 4 files, ran 2 commands" for a set of tool calls (nested calls included). */
const CHECK_PAGE = "mcp__studio__check_page";
const RENDER_DOCUMENT = "mcp__studio__render_document";
const SAVE_TEMPLATE = "mcp__studio__save_template";

export function summarizeTools(top: ToolNode[]): string {
  const all = flattenTools(top);
  // The studio's page check is its own step, whatever kind the harness gave it.
  const checks = all.filter((tool) => tool.name === CHECK_PAGE).length;
  const renders = all.filter((tool) => tool.name === RENDER_DOCUMENT).length;
  const saves = all.filter((tool) => tool.name === SAVE_TEMPLATE).length;
  const named = new Set([CHECK_PAGE, RENDER_DOCUMENT, SAVE_TEMPLATE]);
  const tools = all.filter((tool) => !named.has(tool.name));
  const count = (kind: string) => tools.filter((tool) => tool.kind === kind).length;
  const edited = new Set(tools.filter((tool) => tool.kind === "edit").map(editTarget)).size;
  const read = count("read");
  const ran = count("execute");
  const searched = count("search");
  const fetched = count("fetch");
  const agents = count("agent");
  const known = new Set(["edit", "read", "execute", "search", "fetch", "agent"]);
  const other = tools.filter((tool) => !known.has(tool.kind)).length;

  const parts: string[] = [];
  if (edited) parts.push(`edited ${plural(edited, "file", "files")}`);
  if (read) parts.push(`read ${plural(read, "file", "files")}`);
  if (ran) parts.push(`ran ${plural(ran, "command", "commands")}`);
  if (searched) parts.push(`searched ${plural(searched, "time", "times")}`);
  if (fetched) parts.push(`fetched ${plural(fetched, "page", "pages")}`);
  // U+2011, a non-breaking hyphen: "sub-agent" must not wrap in the middle.
  if (agents) parts.push(`used ${plural(agents, "sub\u2011agent", "sub\u2011agents")}`);
  if (checks) parts.push(checks === 1 ? "checked the page" : `checked pages ${checks} times`);
  if (renders) parts.push(`rendered ${plural(renders, "file", "files")} for viewing`);
  if (saves) parts.push(saves === 1 ? "saved a template" : `saved ${saves} templates`);
  if (other) parts.push(plural(other, "other step", "other steps"));
  if (!parts.length) return "No tool activity";
  const text = parts.join(", ");
  return text.charAt(0).toUpperCase() + text.slice(1);
}

function hasToolError(node: ToolNode): boolean {
  return node.status === "error" || node.children.some(hasToolError);
}

/**
 * The items of a turn as the thread shows them. While a turn runs every row is
 * shown as it happens; once it has finished, runs of two or more consecutive
 * tool calls (with any thinking between them) fold into one expandable group.
 */
export function turnViewItems(turn: Turn): TurnViewItem[] {
  if (turn.status === "running") return turn.items;
  const out: TurnViewItem[] = [];
  let run: (ToolItem | ThoughtItem)[] = [];
  const flush = () => {
    if (!run.length) return;
    // Thinking at the very end of a run belongs with what follows, not the group.
    const tail: ThoughtItem[] = [];
    while (run.length && run[run.length - 1].type === "thought") tail.unshift(run.pop() as ThoughtItem);
    const tools = run.filter((item): item is ToolItem => item.type === "tool");
    if (tools.length >= 2) {
      out.push({
        type: "group",
        key: `group:${run[0].key}`,
        summary: summarizeTools(tools.map((item) => item.tool)),
        items: run,
        hasError: tools.some((item) => hasToolError(item.tool)),
      });
    } else {
      out.push(...run);
    }
    out.push(...tail);
    run = [];
  };
  for (const item of turn.items) {
    if (item.type === "tool" || (item.type === "thought" && run.length > 0)) {
      run.push(item);
    } else {
      flush();
      out.push(item);
    }
  }
  flush();
  return out;
}

/** Approvals that are waiting for the user, oldest first. */
export function waitingApprovals(state: ThreadState): ApprovalItem[] {
  const out: ApprovalItem[] = [];
  for (const entry of state.entries) {
    if (entry.type !== "turn" || entry.status !== "running") continue;
    for (const item of entry.items) if (item.type === "approval" && item.status === "waiting") out.push(item);
  }
  return out;
}

/** Paths changed by the turn that is running now (for the Files panel's dots). */
export function runningChangedPaths(state: ThreadState): string[] {
  return runningTurn(state)?.changedPaths ?? [];
}
