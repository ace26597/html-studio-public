// Renders the thread model produced by the event reducer.

import { VerificationCard } from "./EffortControl";
import { RECHECK_COVERS, recheckHeadline, type Recheck } from "./effort";
import { memo, useEffect, useState } from "react";
import { isDrawingAttachment } from "./drawing";
import { baseName, checkpointLabel, costLabel, formatCount, formatDuration, isImagePath, prettyInput, truncate } from "./format";
import { Icon, Spinner, toolIcon } from "./icons";
import { checkPageLine } from "./library";
import { LibraryPickCard } from "./LibraryPickCard";
import { Markdown } from "./markdown";
import { shownSteps, type NextStep } from "./nextSteps";
import {
  turnViewItems,
  type ActivityGroup,
  type ApprovalItem,
  type CheckpointItem,
  type NoticeItem,
  type ThoughtItem,
  type ThreadEntry,
  type ToolNode,
  type Turn,
} from "./thread";
import type { PlanEntry, Selection } from "./types";

/** The client's side of an approval while a decision is being sent. */
export interface DecisionState {
  pending: "allow" | "deny" | null;
  error: string | null;
}

export interface ThreadActions {
  onRetry: (turn: Turn) => void;
  onOpenVersion: (sha: string) => void;
  canRetry: boolean;
  onDecide: (item: ApprovalItem, decision: "allow" | "deny") => void;
  decisions: Record<string, DecisionState>;
  /** Where a workspace file can be loaded from for a thumbnail or link, or null when it cannot. */
  fileHref: (path: string) => string | null;
  /** The region screenshots listed in a saved drawing file. */
  readDrawing?: (path: string) => Promise<string[]>;
  /** Step 3o: the project, for "Always / Never in this project" on the picked card. */
  projectId?: string | null;
  /** A suggested next step was tapped: its text goes into the chat box, and nothing is sent. */
  onUseStep?: (step: NextStep) => void;
  /** A message is queued or being sent: the last request's chips are not shown. */
  busy?: boolean;
}

/** Chips under the last answer. A tap only fills the chat box (CONTRACT.md, "Suggested next steps"). */
export function NextStepChips({ steps, onUse }: { steps: NextStep[]; onUse: (step: NextStep) => void }) {
  if (steps.length === 0) return null;
  return (
    <div className="next-steps" role="group" aria-label="Suggested next steps">
      {steps.map((step) => (
        <button
          key={step.id}
          type="button"
          className={`next-step next-step-${step.from}`}
          title={`Puts this in the chat box: ${step.text}`}
          onClick={() => onUse(step)}
        >
          {step.label}
        </button>
      ))}
    </div>
  );
}

/** A drawing sent with a message: named "Drawing", with its region screenshots when there are any. */
function DrawingAttachment({
  path,
  fileHref,
  readDrawing,
}: {
  path: string;
  fileHref: (path: string) => string | null;
  readDrawing?: (path: string) => Promise<string[]>;
}) {
  const [images, setImages] = useState<string[]>([]);
  useEffect(() => {
    if (!readDrawing) return;
    let cancelled = false;
    readDrawing(path).then(
      (list) => {
        if (!cancelled) setImages(list);
      },
      // The drawing is still named; only its thumbnails are missing.
      () => {},
    );
    return () => {
      cancelled = true;
    };
  }, [path, readDrawing]);
  return (
    <>
      <span className="attachment is-drawing" title={path}>
        <Icon name="pencil" size={12} />
        <span className="attachment-name">Drawing</span>
      </span>
      {images.map((image) => (
        <AttachmentView key={image} path={image} href={fileHref(image)} />
      ))}
    </>
  );
}

/** One attached file on a message: a thumbnail for images, a named chip otherwise. */
export function AttachmentView({ path, href }: { path: string; href: string | null }) {
  const [broken, setBroken] = useState(false);
  const name = baseName(path);
  const image = isImagePath(path) && href && !broken;
  const body = image ? (
    <img src={href} alt={name} loading="lazy" onError={() => setBroken(true)} />
  ) : (
    <>
      <Icon name="paperclip" size={12} />
      <span className="attachment-name">{name}</span>
    </>
  );
  const className = `attachment${image ? " is-image" : ""}`;
  if (!href) {
    return (
      <span className={className} title={path}>
        {body}
      </span>
    );
  }
  return (
    <a className={className} href={href} target="_blank" rel="noreferrer" title={`${path} (opens in a new tab)`}>
      {body}
    </a>
  );
}

export function AttachmentList({
  paths,
  fileHref,
  readDrawing,
}: {
  paths: string[];
  fileHref: (path: string) => string | null;
  readDrawing?: (path: string) => Promise<string[]>;
}) {
  if (!paths.length) return null;
  return (
    <ul className="attachment-list" aria-label={paths.length === 1 ? "1 attachment" : `${paths.length} attachments`}>
      {paths.map((path) => (
        <li key={path}>
          {isDrawingAttachment(path) ? (
            <DrawingAttachment path={path} fileHref={fileHref} readDrawing={readDrawing} />
          ) : (
            <AttachmentView path={path} href={fileHref(path)} />
          )}
        </li>
      ))}
    </ul>
  );
}

export function SelectionChip({ selection, onRemove }: { selection: Selection; onRemove?: () => void }) {
  const text = truncate(selection.text, 60);
  return (
    <span className="chip" title={selection.selector}>
      <Icon name="pointer" size={12} />
      <span className="chip-tag">{selection.tag || "element"}</span>
      {text && <span className="chip-text">{text}</span>}
      {onRemove && (
        <button type="button" className="chip-remove" aria-label="Remove the selected element" onClick={onRemove}>
          <Icon name="x" size={11} strokeWidth={2.2} />
        </button>
      )}
    </span>
  );
}

function ToolStatusMark({ status }: { status: ToolNode["status"] }) {
  if (status === "running") return <Spinner size={13} className="tool-mark" />;
  if (status === "ok") return <Icon name="check" size={13} strokeWidth={2.2} className="tool-mark is-ok" />;
  if (status === "error") return <Icon name="alert" size={13} strokeWidth={2} className="tool-mark is-error" />;
  return <span className="tool-mark is-cancelled" aria-hidden="true" />;
}

const STATUS_WORD: Record<ToolNode["status"], string> = {
  running: "running",
  ok: "done",
  error: "failed",
  cancelled: "not finished",
};

function ToolRow({ tool }: { tool: ToolNode }) {
  const [open, setOpen] = useState(false);
  const input = prettyInput(tool.input);
  const hasDetail = Boolean(input || tool.summary);
  // The page check answers in JSON; its counts read better as one line.
  const checkLine = tool.name === "mcp__studio__check_page" ? checkPageLine(tool.summary) : null;
  return (
    <div className={`tool${tool.status === "error" ? " is-error" : ""}`}>
      <button
        type="button"
        className="tool-head"
        aria-expanded={hasDetail ? open : undefined}
        onClick={() => hasDetail && setOpen((value) => !value)}
      >
        <Icon name={toolIcon(tool.kind)} size={14} className="tool-icon" />
        <span className="tool-title">{tool.title}</span>
        <span className="sr-only">{STATUS_WORD[tool.status]}</span>
        <ToolStatusMark status={tool.status} />
      </button>
      {checkLine && <div className="tool-check">{checkLine}</div>}
      {open && hasDetail && (
        <div className="tool-detail">
          {input && (
            <>
              <div className="tool-detail-label">Input</div>
              <pre>{input}</pre>
            </>
          )}
          {tool.summary && (
            <>
              <div className="tool-detail-label">{tool.status === "error" ? "Error" : "Result"}</div>
              <pre>{tool.summary}</pre>
            </>
          )}
        </div>
      )}
      {tool.children.length > 0 && (
        <div className="tool-children">
          {tool.children.map((child) => (
            <ToolRow key={child.toolId} tool={child} />
          ))}
        </div>
      )}
    </div>
  );
}

function ThoughtRow({ item, live }: { item: ThoughtItem; live: boolean }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="thought">
      <button type="button" className="tool-head thought-head" aria-expanded={open} onClick={() => setOpen((v) => !v)}>
        <Icon name="bulb" size={14} className="tool-icon" />
        <span className="tool-title">{live ? "Thinking…" : "Thinking"}</span>
        <Icon name={open ? "chevron-down" : "chevron-right"} size={13} className="tool-mark" />
      </button>
      {open && <div className="thought-text">{item.text}</div>}
    </div>
  );
}

function GroupRow({ group }: { group: ActivityGroup }) {
  const [open, setOpen] = useState(false);
  return (
    <div className={`group${open ? " is-open" : ""}`}>
      <button type="button" className="tool-head group-head" aria-expanded={open} onClick={() => setOpen((v) => !v)}>
        <Icon name={open ? "chevron-down" : "chevron-right"} size={14} className="tool-icon" />
        <span className="tool-title">{group.summary}</span>
        {group.hasError && <Icon name="alert" size={13} strokeWidth={2} className="tool-mark is-error" />}
      </button>
      {open && (
        <div className="group-body">
          {group.items.map((item) =>
            item.type === "tool" ? (
              <ToolRow key={item.key} tool={item.tool} />
            ) : (
              <ThoughtRow key={item.key} item={item} live={false} />
            ),
          )}
        </div>
      )}
    </div>
  );
}

function PlanList({ entries }: { entries: PlanEntry[] }) {
  return (
    <ol className="plan-list">
      {entries.map((entry, index) => (
        <li key={index} className={`plan-entry is-${entry.status}`}>
          <span className="plan-box" aria-hidden="true">
            {entry.status === "completed" && <Icon name="check" size={10} strokeWidth={3} />}
            {entry.status === "in_progress" && <span className="plan-dot" />}
          </span>
          <span className="plan-text">{entry.content}</span>
          <span className="sr-only">
            {entry.status === "completed" ? "(done)" : entry.status === "in_progress" ? "(in progress)" : "(to do)"}
          </span>
        </li>
      ))}
    </ol>
  );
}

function Plan({ entries, running }: { entries: PlanEntry[]; running: boolean }) {
  const done = entries.filter((entry) => entry.status === "completed").length;
  // Pinned while the turn runs: open as a checklist, or folded to the current step.
  const [open, setOpen] = useState(running);
  if (!entries.length) return null;
  if (running) {
    const current =
      entries.find((entry) => entry.status === "in_progress") ?? entries.find((entry) => entry.status !== "completed");
    return (
      <section className={`plan${open ? " is-open" : ""}`} aria-label="Plan">
        <button type="button" className="plan-header" aria-expanded={open} onClick={() => setOpen((v) => !v)}>
          <span className="plan-label">Plan</span>
          {!open && <span className="plan-current">{current?.content ?? "All steps done"}</span>}
          <span className="plan-count">
            {done} of {entries.length}
          </span>
          <Icon name={open ? "chevron-down" : "chevron-right"} size={13} />
        </button>
        {open && <PlanList entries={entries} />}
      </section>
    );
  }
  return (
    <div className="group">
      <button type="button" className="tool-head group-head" aria-expanded={open} onClick={() => setOpen((v) => !v)}>
        <Icon name={open ? "chevron-down" : "chevron-right"} size={14} className="tool-icon" />
        <span className="tool-title">
          Plan · {done} of {entries.length} done
        </span>
      </button>
      {open && (
        <div className="group-body">
          <PlanList entries={entries} />
        </div>
      )}
    </div>
  );
}

function Checkpoint({ item, onOpen }: { item: CheckpointItem; onOpen: (sha: string) => void }) {
  const label = checkpointLabel(item.kind, item.message);
  // "Uploaded data.csv" already names the file.
  const count = label.startsWith("Uploaded ") ? 0 : item.files.length;
  return (
    <button
      type="button"
      className="marker"
      onClick={() => onOpen(item.sha)}
      title={item.message ? `${item.message} — show in Versions` : "Show in Versions"}
    >
      <Icon name="commit" size={14} />
      <span>
        {label}
        {count > 0 && ` · ${count} ${count === 1 ? "file" : "files"}`}
      </span>
    </button>
  );
}

function Notice({ item }: { item: NoticeItem }) {
  const level = item.level === "error" || item.level === "warning" ? item.level : "info";
  return (
    <div className={`notice notice-${level}`} role={level === "error" ? "alert" : "note"}>
      <Icon name={level === "error" ? "alert" : level === "warning" ? "warning" : "info"} size={14} />
      <span>{item.message}</span>
    </div>
  );
}

const RESOLVED_LABEL: Record<string, string> = {
  allowed: "Allowed",
  denied: "Denied",
};

function ApprovalCard({ item, actions }: { item: ApprovalItem; actions: ThreadActions }) {
  const decision = actions.decisions[item.approvalId];
  if (item.status !== "waiting") {
    const label = item.status === "denied" && item.by === "turn_end" ? "Denied — the turn ended" : RESOLVED_LABEL[item.status];
    return (
      <div className={`approval-record is-${item.status}`}>
        <Icon name={item.status === "allowed" ? "check" : "x"} size={13} strokeWidth={2.2} className="approval-record-icon" />
        <span className="approval-record-label">{label}</span>
        {(item.detail || item.title) && (
          <code className="approval-record-detail" title={item.detail || item.title}>
            {item.detail || item.title}
          </code>
        )}
      </div>
    );
  }
  const busy = decision?.pending ?? null;
  return (
    <section className="approval" aria-label={`Approval needed: ${item.title}`}>
      <div className="approval-head">
        <Icon name="hand" size={15} className="approval-icon" />
        <strong>{item.title}</strong>
      </div>
      {item.detail && <pre className="approval-detail">{item.detail}</pre>}
      {item.reason && <p className="approval-reason">{item.reason}</p>}
      {decision?.error && (
        <p className="approval-error" role="alert">
          {decision.error}
        </p>
      )}
      <div className="approval-actions">
        <span className="approval-hint">The agent is waiting for you.</span>
        <button
          type="button"
          className="btn btn-small"
          disabled={busy !== null}
          aria-label={`Deny: ${item.title}`}
          onClick={() => actions.onDecide(item, "deny")}
        >
          {busy === "deny" ? "Denying…" : "Deny"}
        </button>
        <button
          type="button"
          className="btn btn-small btn-primary"
          disabled={busy !== null}
          aria-label={`Allow: ${item.title}`}
          onClick={() => actions.onDecide(item, "allow")}
        >
          {busy === "allow" ? "Allowing…" : "Allow"}
        </button>
      </div>
    </section>
  );
}

interface UsagePart {
  text: string;
  tip: string | null;
}

function usageParts(turn: Turn): UsagePart[] {
  const parts: UsagePart[] = [];
  const usage = turn.usage;
  let duration = usage?.duration_ms ?? null;
  if (duration === null && turn.finishedAt) {
    const span = Date.parse(turn.finishedAt) - Date.parse(turn.startedAt);
    if (Number.isFinite(span) && span >= 0) duration = span;
  }
  if (duration !== null) parts.push({ text: formatDuration(duration), tip: null });
  const cost = costLabel(usage);
  if (cost) parts.push(cost);
  const tokens = (usage?.input_tokens ?? 0) + (usage?.output_tokens ?? 0);
  if (usage && (usage.input_tokens != null || usage.output_tokens != null)) {
    parts.push({ text: `${formatCount(tokens)} tokens`, tip: null });
  }
  return parts;
}

function UsageLine({ parts }: { parts: UsagePart[] }) {
  return (
    <>
      {parts.map((part, index) => (
        <span key={index} className="usage-part">
          {index > 0 && <span aria-hidden="true"> · </span>}
          {part.tip ? (
            <span
              className="usage-estimate"
              tabIndex={0}
              data-tip={part.tip}
              data-tip-side="top"
              aria-label={`${part.text}. ${part.tip}`}
            >
              {part.text}
            </span>
          ) : (
            part.text
          )}
        </span>
      ))}
    </>
  );
}

const TurnView = memo(function TurnView({
  turn,
  isLast,
  actions,
}: {
  turn: Turn;
  isLast: boolean;
  actions: ThreadActions;
}) {
  const running = turn.status === "running";
  const items = turnViewItems(turn);
  const last = turn.items[turn.items.length - 1];
  // Dots while we wait on the model (nothing visibly in progress).
  const waiting =
    running &&
    (!last ||
      last.type === "checkpoint" ||
      last.type === "notice" ||
      (last.type === "approval" && last.status !== "waiting") ||
      (last.type === "tool" && last.tool.status !== "running"));
  const usage = usageParts(turn);
  // After the turn the plan is only worth showing if it was left unfinished.
  const unfinishedPlan =
    !running && turn.plan && turn.plan.some((entry) => entry.status !== "completed") ? turn.plan : null;

  return (
    <article className={`turn is-${turn.status}`} aria-label="Exchange">
      {(turn.text || turn.selection || turn.attachments.length > 0) && (
        <div className="msg-user">
          {turn.selection && <SelectionChip selection={turn.selection} />}
          <AttachmentList paths={turn.attachments} fileHref={actions.fileHref} readDrawing={actions.readDrawing} />
          {turn.text && <div className="msg-user-text">{turn.text}</div>}
        </div>
      )}

      {turn.plan && running && <Plan key="plan-live" entries={turn.plan} running />}

      <div className="turn-body">
        {turn.pick && <LibraryPickCard pick={turn.pick} projectId={actions.projectId} />}
        {items.map((item, index) => {
          switch (item.type) {
            case "message":
              return <Markdown key={item.key} text={item.text} />;
            case "thought":
              return <ThoughtRow key={item.key} item={item} live={running && index === items.length - 1} />;
            case "tool":
              return <ToolRow key={item.key} tool={item.tool} />;
            case "group":
              return <GroupRow key={item.key} group={item} />;
            case "checkpoint":
              return <Checkpoint key={item.key} item={item} onOpen={actions.onOpenVersion} />;
            case "notice":
              return <Notice key={item.key} item={item} />;
            case "approval":
              return <ApprovalCard key={item.key} item={item} actions={actions} />;
            case "verification":
              return (
                <div key={item.key} className="verification-with-recheck">
                  {item.recheck && <RecheckNote recheck={item.recheck} />}
                  <VerificationCard item={item} />
                </div>
              );
          }
        })}
        {unfinishedPlan && <Plan key="plan-after" entries={unfinishedPlan} running={false} />}
        {waiting && (
          <div className="waiting" aria-hidden="true">
            <span />
            <span />
            <span />
          </div>
        )}
      </div>

      {turn.status === "failed" && (
        <div className="turn-error" role="alert">
          <Icon name="alert" size={15} />
          <div className="turn-error-text">
            <strong>This didn’t finish</strong>
            <span>{turn.error || "The agent stopped with an error."}</span>
          </div>
          {isLast && turn.text && (
            <button
              type="button"
              className="btn btn-small"
              disabled={!actions.canRetry}
              onClick={() => actions.onRetry(turn)}
            >
              Try again
            </button>
          )}
        </div>
      )}
      {turn.status === "stopped" && (
        <div className="turn-foot">
          <Icon name="stop" size={12} />
          <span>
            Stopped
            {usage.length > 0 && <span aria-hidden="true"> · </span>}
            <UsageLine parts={usage} />
          </span>
        </div>
      )}
      {turn.status === "completed" && usage.length > 0 && (
        <div className="turn-foot">
          <span>
            <UsageLine parts={usage} />
          </span>
        </div>
      )}
      {actions.onUseStep && (
        <NextStepChips steps={shownSteps(turn, isLast, actions.busy === true)} onUse={actions.onUseStep} />
      )}
    </article>
  );
});

export function ThreadView({ entries, actions }: { entries: ThreadEntry[]; actions: ThreadActions }) {
  let lastTurnIndex = -1;
  for (let i = entries.length - 1; i >= 0; i--) {
    if (entries[i].type === "turn") {
      lastTurnIndex = i;
      break;
    }
  }
  return (
    <>
      {entries.map((entry, index) => {
        if (entry.type === "turn") {
          return <TurnView key={entry.key} turn={entry} isLast={index === lastTurnIndex} actions={actions} />;
        }
        if (entry.type === "checkpoint") {
          return (
            <div key={entry.key} className="thread-marker">
              <Checkpoint item={entry} onOpen={actions.onOpenVersion} />
            </div>
          );
        }
        return (
          <div key={entry.key} className="thread-marker">
            <Notice item={entry} />
          </div>
        );
      })}
    </>
  );
}

/** The studio's own re-check of the page, shown above the agent's counts: what it still finds
 * wrong listed first, or one line that it agrees; always what it covers. */
export function RecheckNote({ recheck }: { recheck: Recheck }) {
  const wrong = recheck.total > 0;
  const pages = new Set(recheck.problems.map((p) => p.page));
  return (
    <section
      className={`verification-recheck ${wrong ? "verification-recheck-wrong" : "verification-recheck-ok"}`}
      aria-label="The studio’s own check"
    >
      <p className="verification-recheck-title">
        <strong>{recheckHeadline(recheck)}</strong>
      </p>
      {wrong && (
        <ul className="verification-recheck-list">
          {recheck.problems.map((problem, index) => (
            <li key={index}>
              <span className="finding-what">{problem.what}</span>
              {(problem.where || pages.size > 1) && (
                <span className="finding-where">
                  {" "}
                  — {[pages.size > 1 ? problem.page : "", problem.where].filter(Boolean).join(", ")}
                </span>
              )}
              {problem.why && <span className="finding-note">{problem.why}</span>}
            </li>
          ))}
          {recheck.cut > 0 && <li className="finding-cut">{recheck.cut} more were found and not listed.</li>}
        </ul>
      )}
      {recheck.pages
        .filter((p) => p.error)
        .map((p) => (
          <p key={p.page} className="verification-recheck-error">
            Could not check {p.page}: {p.error}
          </p>
        ))}
      <p className="verification-means">{RECHECK_COVERS}</p>
    </section>
  );
}
