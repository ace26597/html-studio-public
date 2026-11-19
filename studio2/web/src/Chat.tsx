// The floating chat: a round button bottom-right that opens a panel with the
// thread and the composer. It floats over the page and never pushes it.

import { EffortControl } from "./EffortControl";
import type { TurnOptions } from "./effort";
import { draftHolds, fillDraft, type NextStep } from "./nextSteps";
import {
  forwardRef,
  useCallback,
  useEffect,
  useImperativeHandle,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { planAttachments } from "./attachments";
import { marksLabel } from "./drawing";
import { canRecord, insertDictation, type VoiceState } from "./voice";
import { VoiceButton } from "./VoiceButton";
import { formatBytes, truncate } from "./format";
import { Icon, Spinner } from "./icons";
import { AttachmentList, SelectionChip, ThreadView, type DecisionState } from "./ThreadView";
import type { ChatFiles } from "./chatFiles";
import { latestActivity, waitingApprovals, type ApprovalItem, type NoticeItem, type ThreadState, type Turn } from "./thread";
import { chipList, chipTip, kindIcon } from "./context";
import type { Approvals, Connection, ContextItem, Project, Selection } from "./types";
import { IconButton, Menu, useToast, type MenuEntry } from "./ui";

/** A file attached in the composer: uploaded straight away, sent with the next message. */
export interface PendingAttachment {
  id: string;
  name: string;
  size: number;
  status: "uploading" | "done" | "error";
  /** Workspace path, once uploaded. */
  path: string | null;
  error: string | null;
  /** An object URL for an image's thumbnail, made from the local file. */
  thumbUrl: string | null;
}

const IS_MAC = typeof navigator !== "undefined" && /Mac|iPhone|iPad/.test(navigator.platform);
export const FOCUS_SHORTCUT = IS_MAC ? "⌘K" : "Ctrl K";

const SUGGESTIONS = [
  "A one-page competitor analysis",
  "A dashboard from a CSV I’ll upload",
  "A simple landing page for an idea",
];

export interface ChatHandle {
  focusComposer: () => void;
}

interface ChatProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  thread: ThreadState;
  project: Project;
  connections: Connection[];
  draft: string;
  onDraftChange: (text: string) => void;
  selection: Selection | null;
  onClearSelection: () => void;
  onSend: (text: string) => void;
  onStop: () => void;
  onRetry: (turn: Turn) => void;
  onOpenVersion: (sha: string) => void;
  onPickModel: (connection: string, model: string | null) => void;
  /** Depth and verify, remembered per project; "Verify content" sends a verify-only request. */
  options: TurnOptions;
  onOptionsChange: (next: TurnOptions) => void;
  onVerifyPage: () => void;
  /** "Polish the look": a request about how the page looks, with nothing new asked for. */
  onPolishPage: () => void;
  starting: boolean;
  attachments: PendingAttachment[];
  onAddFiles: (files: File[]) => void;
  onRemoveAttachment: (id: string) => void;
  onDecide: (item: ApprovalItem, decision: "allow" | "deny") => void;
  decisions: Record<string, DecisionState>;
  fileHref: (path: string) => string | null;
  /** The chat's file cards: sizes now, and opening a file (or, with null, the whole list) in Files. */
  chatFiles?: ChatFiles;
  onOpenFile?: (path: string | null) => void;
  readDrawing?: (path: string) => Promise<string[]>;
  onUnqueue: (queuedId: string) => void;
  unqueuing: ReadonlySet<string>;
  onSetApprovals: (value: Approvals) => void;
  /** Phone / iPad portrait: the chat is a bottom sheet (bar → half → full), never a floating window. */
  compact?: boolean;
  /** Marks drawn on the page that go with the next message (0: none). */
  drawingMarks?: number;
  onRemoveDrawing?: () => void;
  /** Out of the way entirely (drawing on a phone). */
  hidden?: boolean;
  /** The studio can transcribe recordings (`health.transcription.available`). */
  canDictate?: boolean;
  /** "Add context" beside "Attach files": opens the dialog that adds a folder, a file or an address. */
  onAddContext?: () => void;
  /** A context chip: opens the project's panel on its Context tab. */
  onOpenContext?: () => void;
}

function ApprovalsMenu({ value, onChange }: { value: Approvals; onChange: (value: Approvals) => void }) {
  const label = value === "allow" ? "Allow everything" : "Ask before risky commands";
  return (
    <Menu
      label="Risky commands"
      align="end"
      entries={[
        { type: "section", label: "Risky commands" },
        { label: "Ask before risky commands", checked: value === "ask", onSelect: () => onChange("ask") },
        { label: "Allow everything", checked: value === "allow", onSelect: () => onChange("allow") },
      ]}
      trigger={(props) => (
        <button
          type="button"
          className={`icon-btn approvals-btn${value === "allow" ? " is-allow" : ""}`}
          aria-label={`Risky commands: ${label}`}
          data-tip={`Risky commands: ${label}`}
          data-tip-align="end"
          {...props}
        >
          <Icon name="shield" size={15} />
        </button>
      )}
    />
  );
}

/** "Context", then a chip for each item (the first three) and "+N" for the rest. */
export function ContextChips({ items, onOpen }: { items: readonly ContextItem[]; onOpen?: () => void }) {
  if (!items.length) return null;
  const { shown, more } = chipList(items);
  return (
    <div className="ctx-chips" role="group" aria-label="Context for the agent">
      <span className="ctx-chips-title">Context</span>
      {shown.map((item) => (
        <button
          key={item.id}
          type="button"
          className="chip ctx-chip"
          title={chipTip(item)}
          aria-label={`${item.label}: ${chipTip(item)}. Open the Context tab`}
          onClick={onOpen}
        >
          <Icon name={kindIcon(item.kind)} size={12} />
          <span className="chip-text">{item.label}</span>
        </button>
      ))}
      {more > 0 && (
        <button
          type="button"
          className="chip ctx-chip ctx-more"
          title={items
            .slice(shown.length)
            .map((item) => item.label)
            .join(", ")}
          aria-label={`${more} more. Open the Context tab`}
          onClick={onOpen}
        >
          +{more}
        </button>
      )}
    </div>
  );
}

function ComposerAttachment({ item, onRemove }: { item: PendingAttachment; onRemove: () => void }) {
  const failed = item.status === "error";
  return (
    <li
      className={`composer-attachment is-${item.status}${item.thumbUrl ? " is-image" : ""}`}
      title={failed ? `${item.name}: ${item.error ?? "Upload failed"}` : `${item.name} · ${formatBytes(item.size)}`}
    >
      {item.thumbUrl ? (
        <img src={item.thumbUrl} alt="" />
      ) : (
        <Icon name={failed ? "alert" : "paperclip"} size={13} className="composer-attachment-icon" />
      )}
      <span className="composer-attachment-name">{truncate(item.name, 32)}</span>
      {item.status === "uploading" && <Spinner size={12} />}
      {failed && <span className="composer-attachment-failed">Not uploaded</span>}
      <button type="button" className="chip-remove" aria-label={`Remove ${item.name}`} onClick={onRemove}>
        <Icon name="x" size={11} strokeWidth={2.2} />
      </button>
    </li>
  );
}

/** Pasted images all arrive as "image.png"; give each its own name. */
function namePasted(file: File, index: number): File {
  if (!/^image\.\w+$/i.test(file.name)) return file;
  const ext = file.name.split(".").pop();
  const stamp = new Date().toISOString().replace(/[-:]/g, "").replace(/\..+$/, "").replace("T", "-");
  return new File([file], `pasted-${stamp}${index ? `-${index + 1}` : ""}.${ext}`, { type: file.type });
}

function modelLabel(project: Project, connections: Connection[]): string {
  const connection = connections.find((item) => item.id === project.connection) ?? connections[0];
  if (!connection) return "Model";
  const modelId = project.model ?? connection.default_model;
  const model = connection.models.find((item) => item.id === modelId);
  return model?.label ?? modelId ?? connection.label;
}

function ModelPicker({
  project,
  connections,
  onPick,
}: {
  project: Project;
  connections: Connection[];
  onPick: (connection: string, model: string | null) => void;
}) {
  const entries = useMemo(() => {
    const list: MenuEntry[] = [];
    for (const connection of connections) {
      const isCurrent = connection.id === project.connection;
      const currentModel = isCurrent ? (project.model ?? connection.default_model) : null;
      list.push({
        type: "section",
        label: connection.label,
        // Step 3n: said wherever a model that is not Claude's can be chosen.
        note: connection.available
          ? connection.experimental
            ? "Claude models work best with the studio; other models are experimental."
            : null
          : (connection.reason ?? "Not available"),
      });
      if (!connection.models.length) {
        list.push({
          label: "Default model",
          checked: isCurrent,
          disabled: !connection.available,
          onSelect: () => onPick(connection.id, null),
        });
      }
      for (const model of connection.models) {
        list.push({
          label: model.label,
          hint: model.experimental ? "experimental" : undefined,
          checked: isCurrent && model.id === currentModel,
          disabled: !connection.available,
          onSelect: () => onPick(connection.id, model.id),
        });
      }
    }
    return list;
  }, [connections, project.connection, project.model, onPick]);

  if (!connections.length) return null;
  return (
    <Menu
      label="Model"
      side="top"
      align="start"
      entries={entries}
      trigger={(props) => (
        <button
          type="button"
          className="model-btn"
          data-tip="Model for the next message"
          data-tip-side="top"
          data-tip-align="start"
          {...props}
        >
          <span>{modelLabel(project, connections)}</span>
          <Icon name="chevron-down" size={12} strokeWidth={2} />
        </button>
      )}
    />
  );
}

export const Chat = forwardRef<ChatHandle, ChatProps>(function Chat(props, ref) {
  const {
    open,
    onOpenChange,
    thread,
    project,
    connections,
    draft,
    onDraftChange,
    selection,
    onClearSelection,
    onSend,
    options,
    onOptionsChange,
    onVerifyPage,
    onPolishPage,
    onStop,
    onRetry,
    onOpenVersion,
    onPickModel,
    starting,
    attachments,
    onAddFiles,
    onRemoveAttachment,
    onDecide,
    decisions,
    fileHref,
    chatFiles,
    onOpenFile,
    readDrawing,
    onUnqueue,
    unqueuing,
    onSetApprovals,
    compact = false,
    drawingMarks = 0,
    onRemoveDrawing,
    hidden = false,
    canDictate = false,
    onAddContext,
    onOpenContext,
  } = props;
  const toast = useToast();
  // Where the caret was when dictation began, if the box had focus and the draft hasn't changed since.
  const dictationCaret = useRef<{ draft: string; start: number; end: number } | null>(null);
  const [voicePhase, setVoicePhase] = useState<VoiceState["phase"]>("idle");
  const recordingOk = useMemo(
    () => canDictate && typeof window !== "undefined" && canRecord({ mediaDevices: navigator.mediaDevices, MediaRecorder: window.MediaRecorder }),
    [canDictate],
  );
  const draftRef = useRef(draft);
  draftRef.current = draft;
  const beginDictation = useCallback(() => {
    const element = textarea.current;
    dictationCaret.current =
      element && document.activeElement === element
        ? { draft: element.value, start: element.selectionStart, end: element.selectionEnd }
        : null;
  }, []);
  const putDictation = useCallback(
    (text: string) => {
      const current = draftRef.current;
      const element = textarea.current;
      let start: number | null = null;
      let end: number | null = null;
      if (element && document.activeElement === element) {
        start = element.selectionStart;
        end = element.selectionEnd;
      } else if (dictationCaret.current && dictationCaret.current.draft === current) {
        start = dictationCaret.current.start;
        end = dictationCaret.current.end;
      }
      const { value, caret } = insertDictation(current, text, start, end);
      onDraftChange(value);
      // Back to the box with the caret after the dictated words, once the new draft is rendered.
      pendingCaret.current = caret;
    },
    [onDraftChange],
  );
  const pendingCaret = useRef<number | null>(null);
  useLayoutEffect(() => {
    const caret = pendingCaret.current;
    const box = textarea.current;
    if (caret === null || !box) return;
    pendingCaret.current = null;
    box.focus();
    box.setSelectionRange(caret, caret);
  }, [draft]);

  const running = thread.runningTurnId !== null;
  const queue = thread.queue;
  const empty = thread.entries.length === 0 && queue.length === 0;
  const waiting = waitingApprovals(thread);
  const needsYou = waiting.length > 0;
  const fileInput = useRef<HTMLInputElement>(null);
  const [dropping, setDropping] = useState(false);
  const dropDepth = useRef(0);
  const textarea = useRef<HTMLTextAreaElement>(null);
  const scroller = useRef<HTMLDivElement>(null);
  const stick = useRef(true);
  const [showJump, setShowJump] = useState(false);
  // The sheet's height when open on a phone: half the screen, or all of it.
  const [full, setFull] = useState(false);
  const sheet = useRef<HTMLElement>(null);
  const drag = useRef<{ y: number } | null>(null);

  // The collapsed sheet's height, so the page preview can stop above it.
  useEffect(() => {
    const element = sheet.current;
    const root = document.documentElement;
    if (!compact || open || !element || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(() => root.style.setProperty("--sheet-bar", `${element.offsetHeight}px`));
    observer.observe(element);
    return () => observer.disconnect();
  }, [compact, open]);
  useEffect(
    () => () => {
      document.documentElement.style.removeProperty("--sheet-bar");
    },
    [],
  );

  // On a phone an approval would otherwise wait unseen behind the collapsed bar.
  const waitingCount = waitingApprovals(thread).length;
  const lastWaiting = useRef(waitingCount);
  useEffect(() => {
    if (compact && waitingCount > lastWaiting.current && !open) onOpenChange(true);
    lastWaiting.current = waitingCount;
  }, [compact, waitingCount, open, onOpenChange]);

  useImperativeHandle(ref, () => ({
    focusComposer: () => textarea.current?.focus(),
  }));

  // Focus the composer when the panel opens (not on touch: that would pop the keyboard up).
  useEffect(() => {
    if (open && !compact) textarea.current?.focus();
  }, [open, compact]);

  // Auto-grow the textarea with its content.
  useLayoutEffect(() => {
    const element = textarea.current;
    if (!element) return;
    element.style.height = "auto";
    element.style.height = `${Math.min(element.scrollHeight, 168)}px`;
  }, [draft, open]);

  // Stay pinned to the latest message unless the reader scrolled up.
  useLayoutEffect(() => {
    const element = scroller.current;
    if (element && stick.current) element.scrollTop = element.scrollHeight;
  }, [thread, open]);

  // …and when the panel itself changes size (window resize, composer growing).
  useEffect(() => {
    const element = scroller.current;
    if (!open || !element || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(() => {
      if (stick.current) element.scrollTop = element.scrollHeight;
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, [open]);

  const onScroll = () => {
    const element = scroller.current;
    if (!element) return;
    const atBottom = element.scrollHeight - element.scrollTop - element.clientHeight < 48;
    stick.current = atBottom;
    setShowJump(!atBottom);
  };

  const jump = useCallback(() => {
    const element = scroller.current;
    if (!element) return;
    stick.current = true;
    setShowJump(false);
    element.scrollTo({ top: element.scrollHeight, behavior: "smooth" });
  }, []);

  const { blocked } = planAttachments(attachments);
  // A drawing can go on its own; the message then says what to do with it.
  const canSend = (draft.trim().length > 0 || drawingMarks > 0) && !starting && blocked === null;
  const sendTip =
    blocked === "uploading"
      ? "Waiting for the attachments to upload"
      : blocked === "failed"
        ? "Remove the attachment that failed to upload"
      : running
        ? "Queue: sends when the current turn ends (Enter)"
        : "Send (Enter)";

  const send = () => {
    if (!canSend) return;
    stick.current = true;
    setShowJump(false);
    onSend(draft.trim());
  };

  // A suggested next step: its text goes into the box, the caret after it, and nothing is sent.
  const fillText = useCallback(
    (text: string) => {
      const next = fillDraft(draftRef.current, text);
      pendingCaret.current = next.length;
      if (next === draftRef.current) {
        // Nothing to render (a second tap): the layout effect will not run, so focus here.
        pendingCaret.current = null;
        textarea.current?.focus();
        textarea.current?.setSelectionRange(next.length, next.length);
      } else {
        onDraftChange(next);
      }
    },
    [onDraftChange],
  );
  const fillFromStep = useCallback((step: NextStep) => fillText(step.text), [fillText]);

  // "Put it back": the text once (never doubled), and the note then stops offering it.
  const [usedNotes, setUsedNotes] = useState<ReadonlySet<string>>(() => new Set());
  const putBackNote = useCallback(
    (item: NoticeItem) => {
      if (!item.restoreText) return;
      if (draftHolds(draftRef.current, item.restoreText)) {
        textarea.current?.focus();
      } else {
        fillText(item.restoreText);
      }
      setUsedNotes((used) => new Set(used).add(item.key));
    },
    [fillText],
  );
  const putBack = useMemo(() => ({ draft, used: usedNotes }), [draft, usedNotes]);

  const actions = useMemo(
    () => ({
      onRetry,
      onOpenVersion,
      canRetry: !running && !starting,
      onDecide,
      decisions,
      fileHref,
      chatFiles,
      onOpenFile,
      readDrawing,
      projectId: project.id,
      onUseStep: fillFromStep,
      onPutBack: putBackNote,
      busy: starting || queue.length > 0,
    }),
    [onRetry, onOpenVersion, running, starting, onDecide, decisions, fileHref, chatFiles, onOpenFile, readDrawing, project.id, fillFromStep, putBackNote, queue.length],
  );

  const activity = running ? latestActivity(thread) : null;

  const hasFiles = (event: React.DragEvent) => event.dataTransfer.types.includes("Files");

  if (!open && !compact) {
    return (
      <div className="chat-dock">
        {running && activity && (
          <div className={`chat-ticker${needsYou ? " is-attention" : ""}`} aria-hidden="true">
            {needsYou ? <Icon name="hand" size={13} /> : <Spinner size={12} />}
            <span key={activity}>{activity}</span>
          </div>
        )}
        <button
          type="button"
          className={`chat-fab${running ? " is-running" : ""}${needsYou ? " is-attention" : ""}`}
          aria-label={
            needsYou
              ? "Open chat. The agent is waiting for your approval"
              : running
                ? `Open chat. Working: ${activity ?? ""}`
                : "Open chat"
          }
          data-tip={needsYou ? "The agent is waiting for you" : `Chat (${FOCUS_SHORTCUT})`}
          data-tip-side="top"
          data-tip-align="end"
          onClick={() => onOpenChange(true)}
        >
          <Icon name="chat" size={22} strokeWidth={1.8} />
          {running && !needsYou && <span className="chat-fab-ring" aria-hidden="true" />}
          {needsYou && <span className="chat-fab-badge" aria-hidden="true" />}
        </button>
      </div>
    );
  }

  const sheetState = !compact ? null : !open ? "bar" : full ? "full" : "half";
  const statusLine = needsYou ? (
    <>
      <Icon name="hand" size={14} /> Waiting for you
    </>
  ) : running ? (
    <>
      <Spinner size={13} /> <span className="sheet-activity">{activity ?? "Working"}</span>
    </>
  ) : (
    "Chat"
  );

  const onGrabStart = (event: React.PointerEvent) => {
    drag.current = { y: event.clientY };
  };
  const onGrabEnd = (event: React.PointerEvent) => {
    const start = drag.current;
    drag.current = null;
    if (!start) return;
    const dy = event.clientY - start.y;
    if (Math.abs(dy) < 30) return; // a tap; the button's click handles it
    event.preventDefault();
    if (dy < 0) {
      if (!open) onOpenChange(true);
      else setFull(true);
    } else if (full) setFull(false);
    else onOpenChange(false);
  };

  return (
    <section
      ref={sheet}
      className={`chat${dropping ? " is-dropping" : ""}${sheetState ? ` sheet is-${sheetState}` : ""}${hidden ? " is-hidden" : ""}`}
      hidden={hidden}
      aria-label="Chat"
      onKeyDown={(event) => {
        if (event.key === "Escape" && !event.defaultPrevented) {
          event.stopPropagation();
          onOpenChange(false);
        }
      }}
      onDragEnter={(event) => {
        if (!hasFiles(event)) return;
        event.preventDefault();
        event.stopPropagation();
        dropDepth.current++;
        setDropping(true);
      }}
      onDragOver={(event) => {
        if (!hasFiles(event)) return;
        event.preventDefault();
        event.stopPropagation();
        event.dataTransfer.dropEffect = "copy";
      }}
      onDragLeave={(event) => {
        event.stopPropagation();
        dropDepth.current = Math.max(0, dropDepth.current - 1);
        if (!dropDepth.current) setDropping(false);
      }}
      onDrop={(event) => {
        if (!hasFiles(event)) return;
        event.preventDefault();
        event.stopPropagation();
        dropDepth.current = 0;
        setDropping(false);
        onAddFiles(Array.from(event.dataTransfer.files));
        textarea.current?.focus();
      }}
    >
      {compact ? (
        <header className="sheet-header">
          <button
            type="button"
            className="sheet-grab"
            aria-expanded={open}
            aria-label={open ? "Collapse the chat" : needsYou ? "Open the chat: the agent is waiting for you" : "Open the chat"}
            onPointerDown={onGrabStart}
            onPointerUp={onGrabEnd}
            onClick={() => {
              if (open) {
                setFull(false);
                onOpenChange(false);
              } else onOpenChange(true);
            }}
          >
            <span className="sheet-grabber" aria-hidden="true" />
            <span className={`sheet-status${needsYou ? " is-attention" : ""}`}>{statusLine}</span>
            {!open && needsYou && <span className="sheet-review">Review</span>}
          </button>
          {open && (
            <span className="chat-header-actions">
              <ApprovalsMenu value={project.approvals === "allow" ? "allow" : "ask"} onChange={onSetApprovals} />
              <IconButton
                icon={full ? "chevron-down" : "arrow-up"}
                label={full ? "Make the chat smaller" : "Make the chat full screen"}
                onClick={() => setFull((value) => !value)}
              />
            </span>
          )}
        </header>
      ) : (
      <header className="chat-header">
        <span className={`chat-title${needsYou ? " is-attention" : ""}`}>
          {needsYou ? (
            <>
              <Icon name="hand" size={13} /> Waiting for you
            </>
          ) : running ? (
            <>
              <Spinner size={12} /> Working
            </>
          ) : (
            <>
              Chat
              {project.uses_template && (
                <span className="chat-template" title={`This project follows the template “${project.uses_template.label}”`}>
                  · Using template: {project.uses_template.label}
                </span>
              )}
            </>
          )}
        </span>
        <span className="chat-header-actions">
          <ApprovalsMenu value={project.approvals === "allow" ? "allow" : "ask"} onChange={onSetApprovals} />
          <IconButton
            icon="chevron-down"
            label="Collapse chat"
            tip="Collapse (Esc)"
            tipAlign="end"
            onClick={() => onOpenChange(false)}
          />
        </span>
      </header>
      )}

      {open && (
      <div className="chat-body">
        <div className="chat-scroll" ref={scroller} onScroll={onScroll}>
          {empty ? (
            <div className="chat-empty">
              <h2>What should this page become?</h2>
              <p>Describe it. The page changes while the agent works, and you can stop it at any time.</p>
              <div className="chat-suggestions">
                {SUGGESTIONS.map((text) => (
                  <button
                    key={text}
                    type="button"
                    className="suggestion"
                    onClick={() => {
                      onDraftChange(text);
                      textarea.current?.focus();
                    }}
                  >
                    {text}
                  </button>
                ))}
              </div>
            </div>
          ) : (
            <div className="thread" role="log" aria-label="Conversation">
              <ThreadView entries={thread.entries} actions={actions} putBack={putBack} />
              {queue.map((item) => (
                <div key={item.queuedId} className="queued">
                  <div className="msg-user is-queued">
                    <span className="queued-label">
                      <Icon name="history" size={11} /> Queued
                    </span>
                    {item.selection && <SelectionChip selection={item.selection} />}
                    <AttachmentList paths={item.attachments} fileHref={fileHref} readDrawing={readDrawing} />
                    {item.text && <div className="msg-user-text">{item.text}</div>}
                  </div>
                  <IconButton
                    icon="x"
                    className="queued-remove"
                    label={`Remove the queued message “${truncate(item.text, 40)}”`}
                    tip="Remove from the queue"
                    tipAlign="end"
                    disabled={unqueuing.has(item.queuedId)}
                    onClick={() => onUnqueue(item.queuedId)}
                  />
                </div>
              ))}
            </div>
          )}
        </div>

        {showJump && !empty && (
          <div className="jump-row">
            <button type="button" className="jump" onClick={jump}>
              <Icon name="arrow-down" size={13} strokeWidth={2} /> Jump to latest
            </button>
          </div>
        )}
      </div>
      )}

      <div className="composer">
        {dropping && (
          <div className="drop-veil composer-veil" aria-hidden="true">
            <Icon name="paperclip" size={18} />
            <span>Drop to attach to your message</span>
          </div>
        )}
        {(selection || attachments.length > 0 || drawingMarks > 0) && (
          <div className="composer-chips">
            {selection && <SelectionChip selection={selection} onRemove={onClearSelection} />}
            {drawingMarks > 0 && (
              <span className="chip drawing-chip">
                <Icon name="pencil" size={12} />
                <span className="chip-text">Drawing · {marksLabel(drawingMarks)}</span>
                {onRemoveDrawing && (
                  <button type="button" className="chip-remove" aria-label="Remove the drawing" onClick={onRemoveDrawing}>
                    <Icon name="x" size={11} strokeWidth={2.2} />
                  </button>
                )}
              </span>
            )}
            {attachments.length > 0 && (
              <ul className="composer-attachments" aria-label="Attachments">
                {attachments.map((item) => (
                  <ComposerAttachment key={item.id} item={item} onRemove={() => onRemoveAttachment(item.id)} />
                ))}
              </ul>
            )}
            {attachments
              .filter((item) => item.status === "error")
              .map((item) => (
                <p key={item.id} className="composer-error" role="alert">
                  {item.name} wasn’t uploaded: {item.error ?? "the upload failed"}. Remove it to send.
                </p>
              ))}
          </div>
        )}
        <ContextChips items={project.context ?? []} onOpen={onOpenContext} />
        <label className="sr-only" htmlFor="composer-input">
          Message
        </label>
        <textarea
          id="composer-input"
          ref={textarea}
          rows={1}
          value={draft}
          placeholder={
            drawingMarks > 0 && !draft.trim()
              ? "Say what to change, or just send the drawing"
              : running
              ? "Queue a message for when this turn ends…"
              : selection
                ? "What should change here?"
                : empty
                  ? "Describe the page you want…"
                  : "Ask for a change…"
          }
          onChange={(event) => onDraftChange(event.target.value)}
          onPaste={(event) => {
            const files = Array.from(event.clipboardData.files);
            if (!files.length) return;
            event.preventDefault();
            onAddFiles(files.map(namePasted));
          }}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
              event.preventDefault();
              send();
            }
          }}
        />
        <div className="composer-bar">
          <input
            ref={fileInput}
            type="file"
            multiple
            hidden
            tabIndex={-1}
            onChange={(event) => {
              if (event.target.files?.length) onAddFiles(Array.from(event.target.files));
              event.target.value = "";
            }}
          />
          <IconButton
            icon="paperclip"
            label="Attach files"
            tip="Attach files (or paste, or drop them here)"
            tipSide="top"
            tipAlign="start"
            className="attach-btn"
            onClick={() => fileInput.current?.click()}
          />
          {onAddContext && (
            <IconButton
              icon="folder"
              label="Add context"
              tip="Add context (a folder, file or address)"
              tipSide="top"
              tipAlign="start"
              className="attach-btn ctx-add-btn"
              onClick={onAddContext}
            />
          )}
          {recordingOk && (
            <VoiceButton
              onBegin={beginDictation}
              onText={putDictation}
              onMessage={(message, kind) => toast({ message, kind: kind === "error" ? "error" : "info" })}
              onPhaseChange={setVoicePhase}
            />
          )}
          {/* While dictating, the model and depth choices step aside so Stop and Send stay on the row. */}
          {voicePhase === "idle" && <ModelPicker project={project} connections={connections} onPick={onPickModel} />}
          {voicePhase === "idle" && (
            <EffortControl options={options} onChange={onOptionsChange} onVerifyPage={onVerifyPage} onPolishPage={onPolishPage} disabled={starting} />
          )}
          <span className="composer-spacer" />
          {running && (
            <button
              type="button"
              className="send-btn is-stop"
              aria-label={queue.length ? `Stop. This also cancels ${queue.length} queued ${queue.length === 1 ? "message" : "messages"}` : "Stop"}
              data-tip={queue.length ? `Stop (also cancels ${queue.length} queued)` : "Stop"}
              data-tip-side="top"
              data-tip-align="end"
              onClick={onStop}
            >
              <Icon name="stop" size={16} />
            </button>
          )}
          {(!running || draft.trim().length > 0 || attachments.length > 0) && (
            <button
              type="button"
              className={`send-btn${running ? " is-queue" : ""}`}
              aria-label={running ? "Queue message" : "Send"}
              aria-description={sendTip}
              data-tip={sendTip}
              data-tip-side="top"
              data-tip-align="end"
              disabled={!canSend}
              onClick={send}
            >
              {starting ? <Spinner size={14} /> : <Icon name="arrow-up" size={16} strokeWidth={2.2} />}
            </button>
          )}
        </div>
      </div>
    </section>
  );
});
