// A project: the page fills the window; the studio is a slim bar, a floating
// chat, and two panels that open only when asked for.

import { CHECK_ONLY_OPTIONS, POLISH_OPTIONS, POLISH_TEXT, projectOptions, VERIFY_ONLY_TEXT, type TurnOptions } from "./effort";
import { useCallback, useEffect, useReducer, useRef, useState } from "react";
import { ApiError, api, errorMessage, type AppAction, type TurnInput } from "./api";
import { attachDetail, attachLabel } from "./attach";
import { AppControl } from "./AppControl";
import { ShipPanel } from "./ShipPanel";
import { shipButton } from "./ship";
import { Chat, FOCUS_SHORTCUT, type ChatHandle, type PendingAttachment } from "./Chat";
import { FilesPanel } from "./FilesPanel";
import { useChatFiles } from "./chatFiles";
import { planAttachments } from "./attachments";
import type { LargePicture } from "./enlarge";
import { describePaste, pastedName, type PastedText } from "./paste";
import { drawingImages, drawReducer, initialDraw, messageText, turnAttachments } from "./drawing";
import { CostDialog } from "./CostDialog";
import { ExportDialog } from "./ExportDialog";
import { DrawToolbar } from "./DrawToolbar";
import { appStateLabel, projectFileUrl } from "./format";
import { Icon, Spinner } from "./icons";
import { offeredName } from "./nameOffer";
import { LogsDrawer } from "./LogsDrawer";
import { chatStartsOpen, useCompact, useKeyboardInset } from "./media";
import { ProjectLibraryPanel, type LibraryTab } from "./ProjectLibraryPanel";
import { ContextDialog } from "./ContextDialog";
import { InstructionsDialog } from "./InstructionsDialog";
import { instructionsEntries, instructionsKept, instructionsSavedMessage } from "./instructions";
import { libraryButtonText, putContextItem, sameContext } from "./context";
import { MakeSkillDialog, makeSkillEntry } from "./MakeSkillDialog";
import { signedInEntries } from "./signedInLook";
import { SaveTemplateDialog } from "./SaveTemplateDialog";
import { fromWorkRequest } from "./skillMaking";
import { PictureViewer } from "./PictureViewer";
import { Preview, type PreviewHandle } from "./Preview";
import { refreshFailure, REFRESH_RETRY_MS } from "./projectLoad";
import { mayDiscard } from "./discard";
import { escapeTarget, MODAL_SELECTOR } from "./escape";
import { holdUnload, setLeaveGuard, type Navigate } from "./router";
import { holdSignIn } from "./signIn";
import { loadString, saveString } from "./storage";
import type { Theme } from "./theme";
import { changesPage, drawingHoldsReload, heldReloadNote, shouldReloadPreview, type ReloadReason } from "./reload";
import { optionsForText } from "./nextSteps";
import { lastTurn, runningChangedPaths, sendsQueued, waitingOnYou, type ApprovalItem, type Turn } from "./thread";
import type { QuestionActions } from "./QuestionCard";
import type { DecisionState } from "./ThreadView";
import type {
  AppInfo,
  Approvals,
  ContextItem,
  Health,
  Mode,
  Project,
  Selection,
  ShipStepEvent,
  StudioEvent,
  TextEdit,
} from "./types";
import { IconButton, Menu, useToast, type MenuEntry } from "./ui";
import { useProjectEvents } from "./useProjectEvents";
import { VersionsDrawer } from "./VersionsDrawer";

type RightPanel = "versions" | "logs" | "library" | null;

let attachmentCounter = 0;

interface ProjectPageProps {
  id: string;
  health: Health;
  navigate: Navigate;
  theme: Theme;
  onToggleTheme: () => void;
}

const MODES: { id: Mode; label: string; tip: string }[] = [
  { id: "browse", label: "Browse", tip: "Use the page normally" },
  { id: "select", label: "Select", tip: "Point the agent at part of the page" },
  { id: "edit", label: "Edit", tip: "Change text directly on the page" },
  { id: "draw", label: "Draw", tip: "Draw on the page and send the drawing" },
];

function editRequest(edit: TextEdit): string {
  return [
    `Please make this text change on the page (${edit.path}). I tried to edit it directly but it could not be saved automatically.`,
    "",
    `Element: \`${edit.selector}\``,
    `Current text: "${edit.before_text.trim()}"`,
    `New text: "${edit.after_text.trim()}"`,
  ].join("\n");
}

export function ProjectPage({ id, health, navigate, theme, onToggleTheme }: ProjectPageProps) {
  const toast = useToast();
  const [project, setProject] = useState<Project | null>(null);
  const [loadError, setLoadError] = useState<{ missing: boolean; message: string } | null>(null);

  const [mode, setMode] = useState<Mode>("browse");
  const [filesOpen, setFilesOpen] = useState(false);
  /** A file a card in the chat asked the Files panel to open. */
  const [fileAsk, setFileAsk] = useState<{ path: string; n: number } | null>(null);
  const [costOpen, setCostOpen] = useState(false);
  const [exportOpen, setExportOpen] = useState(false);
  const [rightPanel, setRightPanel] = useState<RightPanel>(null);
  const versionsOpen = rightPanel === "versions";
  const logsOpen = rightPanel === "logs";
  const libraryOpen = rightPanel === "library";
  const [selectedSha, setSelectedSha] = useState<string | null>(null);
  // Open or closed is remembered per project. With nothing remembered, a desktop starts with the
  // chat open and a phone or iPad with it closed (the round button), so the page has the screen.
  const [chatOpen, setChatOpenState] = useState(() => chatStartsOpen(loadString(`chat:${id}`), window.innerWidth));
  const [draft, setDraftState] = useState(() => loadString(`draft:${id}`) ?? "");
  const [selection, setSelection] = useState<Selection | null>(null);
  const [starting, setStarting] = useState(false);
  const [reloadToken, setReloadToken] = useState(0);
  const [renaming, setRenaming] = useState(false);
  const [nameDraft, setNameDraft] = useState("");
  const [attachments, setAttachments] = useState<PendingAttachment[]>([]);
  const [decisions, setDecisions] = useState<Record<string, DecisionState>>({});
  const [answering, setAnswering] = useState<QuestionActions["answering"]>({});
  const [unqueuing, setUnqueuing] = useState<ReadonlySet<string>>(() => new Set());
  const [appInfo, setAppInfo] = useState<AppInfo | null>(null);
  const [appError, setAppError] = useState<string | null>(null);
  const [appBusy, setAppBusy] = useState<AppAction | null>(null);
  const [savingTemplate, setSavingTemplate] = useState(false);
  const [makingSkill, setMakingSkill] = useState(false);
  const [shipOpen, setShipOpen] = useState(false);
  const [shipTick, setShipTick] = useState(0);
  const [shipStep, setShipStep] = useState<ShipStepEvent | null>(null);
  // A picture from the page, opened large over the studio (the bridge's `enlarge`). The page
  // sends that message, so it is held to what a person does: one at a time, and not the moment
  // the last one was closed.
  const [largePicture, setLargePicture] = useState<LargePicture | null>(null);
  const [sendError, setSendError] = useState<string | null>(null);
  // "Show what changed on the page": only when the page's helper can say (CONTRACT.md).
  const [canShowChanges, setCanShowChanges] = useState(false);
  const largeClosedAt = useRef(0);
  const openLarge = useCallback((picture: LargePicture) => {
    if (Date.now() - largeClosedAt.current < 600) return;
    setLargePicture((open) => open ?? picture);
  }, []);
  const closeLarge = useCallback(() => {
    largeClosedAt.current = Date.now();
    setLargePicture(null);
  }, []);

  const chat = useRef<ChatHandle>(null);
  const reloadTimer = useRef(0);
  const reopenChat = useRef(false);

  // ---- project record -------------------------------------------------------
  // Read by a refresh that fails: only a page with nothing to show yet gets the error screen.
  const hasProject = useRef(false);
  hasProject.current = project !== null;
  const refreshRetry = useRef(0);
  const refreshProject = useCallback(() => {
    const load = () => {
      window.clearTimeout(refreshRetry.current);
      api.getProject(id).then(
        (record) => {
          setProject(record);
          setLoadError(null);
        },
        (error) => {
          const failure = refreshFailure(hasProject.current, error);
          if (failure) setLoadError(failure);
          // The page stays as it was; the record is read again shortly (or by the next event).
          else refreshRetry.current = window.setTimeout(load, REFRESH_RETRY_MS);
        },
      );
    };
    load();
  }, [id]);
  useEffect(() => () => window.clearTimeout(refreshRetry.current), []);

  useEffect(() => {
    refreshProject();
  }, [refreshProject]);

  useEffect(() => {
    document.title = project ? `${project.name} · Studio` : "Studio";
    return () => {
      document.title = "Studio";
    };
  }, [project]);

  // ---- events -----------------------------------------------------------------
  // The visible page's `ready.auto_reload`: false for pages that refresh themselves.
  const autoReload = useRef<boolean | undefined>(undefined);
  // Set below from the draw state: a reload would wipe a drawing, so it waits (`reloadWaiting`).
  const reloadHeld = useRef(false);
  const [reloadWaiting, setReloadWaiting] = useState(false);
  const scheduleReload = useCallback((reason: ReloadReason) => {
    if (!shouldReloadPreview(reason, autoReload.current)) return;
    if (reloadHeld.current) {
      setReloadWaiting(true);
      return;
    }
    window.clearTimeout(reloadTimer.current);
    reloadTimer.current = window.setTimeout(() => setReloadToken((token) => token + 1), 250);
  }, []);
  useEffect(() => () => window.clearTimeout(reloadTimer.current), []);

  // Set below, once the event hook exists; read by `onLive`.
  const refreshAppRef = useRef<() => void>(() => {});
  const lastAppState = useRef<string | null>(null);

  const onLive = useCallback(
    (event: StudioEvent) => {
      if (event.type === "files_changed") {
        if (changesPage(event.paths)) scheduleReload("files_changed");
        if (event.paths?.includes("studio.json")) refreshAppRef.current();
      } else if (event.type === "checkpoint" && event.kind === "restore") scheduleReload("restore");
      else if (event.type === "turn_finished") {
        refreshProject();
        refreshAppRef.current();
      } else if (event.type.startsWith("ship_")) {
        // Make it live: the panel reads its record again; a card opens it by itself.
        refreshProject();
        setShipTick((tick) => tick + 1);
        if (event.type === "ship_step") setShipStep(event as ShipStepEvent);
        else if (event.type === "ship_ready") {
          // A replayed card counts as live for a moment after a reload: open only for the one still waiting.
          const shipId = event.ship_id;
          api.getShip(id).then(
            (view) => {
              if (view.running === shipId && view.card) setShipOpen(true);
            },
            () => undefined,
          );
        }
        else if (event.type === "ship_finished") setShipStep(null);
      } else if (event.type === "app_status") {
        refreshAppRef.current();
        const previous = lastAppState.current;
        lastAppState.current = event.state;
        // The project URL now serves the app (or its status page instead of it): show that.
        if (event.state === "running") scheduleReload("app_running");
        else if (previous === "running") scheduleReload("app_stopped");
      }
    },
    [id, scheduleReload, refreshProject],
  );

  const { thread, stream, receivedSeq, applyAppInfo } = useProjectEvents(id, onLive, !loadError?.missing);
  const running = thread.runningTurnId !== null;
  const queueing = sendsQueued(thread);
  const needsYou = waitingOnYou(thread) > 0;
  // The chips under the last answer: a message that is exactly one of them goes with its options.
  const lastSteps = useRef(lastTurn(thread)?.nextSteps ?? []);
  lastSteps.current = lastTurn(thread)?.nextSteps ?? [];
  const shipState = shipButton(project?.ship);
  // A guest on a shared studio: making an app live, templates and context from this computer are the owner's.
  const guest = health.shared === true && health.owner !== true;
  const attachedCount = project?.data_sources?.length ?? 0;
  // The library button counts both: the data sources attached and the context items.
  const libraryButton = libraryButtonText(attachedCount, project?.context?.length ?? 0);
  /** The Add dialog (`item` null) or the edit dialog for an item; null when closed. */
  const [contextDialog, setContextDialog] = useState<{ item: ContextItem | null } | null>(null);
  const [instructionsOpen, setInstructionsOpen] = useState(false);
  // The person's instructions for every project; null while unknown or when this studio offers none.
  const [everywhere, setEverywhere] = useState<string | null>(null);
  useEffect(() => {
    const stop = new AbortController();
    api.everywhereInstructions(stop.signal).then(
      (kept) => setEverywhere(typeof kept?.text === "string" ? kept.text : null),
      () => setEverywhere(null),     // a shared studio, or a server started before this existed
    );
    return () => stop.abort();
  }, []);
  /** Which tab the library panel should show; null keeps its own default. */
  const [libraryTab, setLibraryTab] = useState<{ tab: LibraryTab; n: number } | null>(null);

  // ---- the project's app ---------------------------------------------------------
  const refreshApp = useCallback(() => {
    const requestedAt = receivedSeq();
    api.getApp(id).then(
      (info) => {
        setAppInfo(info);
        setAppError(null);
        applyAppInfo(info, requestedAt);
      },
      (error) => setAppError(errorMessage(error)),
    );
  }, [id, receivedSeq, applyAppInfo]);
  useEffect(() => {
    refreshAppRef.current = refreshApp;
  }, [refreshApp]);

  useEffect(() => {
    setAppInfo(null);
    setAppError(null);
    lastAppState.current = null;
    if (!loadError?.missing) refreshApp();
  }, [refreshApp, loadError?.missing]);

  useEffect(() => {
    if (thread.app) lastAppState.current = thread.app.state;
  }, [thread.app]);

  const appConfigured = Boolean(thread.app?.configured ?? appInfo?.configured ?? project?.app?.configured);

  const runAppAction = useCallback(
    (action: AppAction) => {
      if (appBusy) return;
      setAppBusy(action);
      const requestedAt = receivedSeq();
      api
        .appAction(id, action)
        .then(
          (info) => {
            setAppInfo(info);
            setAppError(null);
            applyAppInfo(info, requestedAt);
          },
          (error) => toast({ message: errorMessage(error), kind: "error" }),
        )
        .finally(() => setAppBusy(null));
    },
    [id, appBusy, receivedSeq, applyAppInfo, toast],
  );

  /** Workspace files load from `<preview_url>__studio/files/…`, which works with or without an app. */
  const previewUrl = project?.preview_url ?? null;
  const readDrawing = useCallback(
    (path: string) => api.getFile(id, path).then((file) => drawingImages(file.content)),
    [id],
  );
  const fileHref = useCallback(
    (path: string) => (previewUrl ? projectFileUrl(previewUrl, path) : null),
    [previewUrl],
  );
  // The chat's file cards: sizes from the listing, and opening a file in the Files panel.
  const chatFiles = useChatFiles(id, thread);
  const openFileFromChat = useCallback((path: string | null) => {
    setFilesOpen(true);
    setFileAsk((ask) => (path ? { path, n: (ask?.n ?? 0) + 1 } : null));
  }, []);
  // An ask is for the panel as it is open now: a later opening starts with no file asked for.
  useEffect(() => {
    if (!filesOpen) setFileAsk(null);
  }, [filesOpen]);
  const effectiveMode: Mode = running && mode === "edit" ? "browse" : mode;
  const compact = useCompact();
  useKeyboardInset(compact);

  // A new, empty project on a phone or iPad: the chat is where it starts, so open it. The events
  // have no "replay finished" mark; a thread still empty a moment after the stream opened is new.
  const chatRemembered = useRef(loadString(`chat:${id}`) !== null);
  const threadEmpty = useRef(true);
  threadEmpty.current = thread.entries.length === 0 && !running;
  useEffect(() => {
    if (!compact || chatRemembered.current || stream !== "open") return;
    const timer = window.setTimeout(() => {
      if (chatRemembered.current) return;
      chatRemembered.current = true;
      if (threadEmpty.current) setChatOpenState(true);
    }, 1200);
    return () => window.clearTimeout(timer);
  }, [compact, stream]);

  // ---- chat state ---------------------------------------------------------------
  const setChatOpen = useCallback(
    (open: boolean) => {
      reopenChat.current = false;
      chatRemembered.current = true;
      setChatOpenState(open);
      saveString(`chat:${id}`, open ? "1" : "0");
    },
    [id],
  );

  // On a phone or iPad portrait the whole page is the canvas while drawing: the sheet closes and
  // its button is hidden; Done opens the chat with the drawing's chip when something was drawn.
  useEffect(() => {
    if (compact && effectiveMode === "draw") setChatOpenState(false);
  }, [compact, effectiveMode]);

  const setDraft = useCallback(
    (text: string) => {
      setDraftState(text);
      saveString(`draft:${id}`, text);
    },
    [id],
  );

  const openChatAndFocus = useCallback(() => {
    setChatOpen(true);
    // If it was already open the open-effect will not run, so focus directly.
    window.setTimeout(() => chat.current?.focusComposer(), 0);
  }, [setChatOpen]);

  // ---- attachments (uploaded as soon as they are added) -----------------------------
  const attachmentsRef = useRef<PendingAttachment[]>([]);
  useEffect(() => {
    attachmentsRef.current = attachments;
  });
  const dropAttachments = useCallback((ids: Set<string> | null) => {
    setAttachments((list) => {
      for (const item of list) if ((!ids || ids.has(item.id)) && item.thumbUrl) URL.revokeObjectURL(item.thumbUrl);
      return ids ? list.filter((item) => !ids.has(item.id)) : [];
    });
  }, []);
  // A different project starts with an empty composer tray.
  useEffect(() => () => dropAttachments(null), [id, dropAttachments]);

  const addAttachments = useCallback(
    (files: File[], pasted?: PastedText) => {
      for (const file of files) {
        const itemId = `att${++attachmentCounter}`;
        const thumbUrl = file.type.startsWith("image/") ? URL.createObjectURL(file) : null;
        const item: PendingAttachment = {
          id: itemId,
          name: file.name,
          size: file.size,
          status: "uploading",
          path: null,
          error: null,
          thumbUrl,
          pasted,
        };
        setAttachments((list) => [...list, item]);
        const update = (patch: Partial<PendingAttachment>) =>
          setAttachments((list) => list.map((entry) => (entry.id === itemId ? { ...entry, ...patch } : entry)));
        api.upload(id, file, "attachments").then(
          (result) => update({ status: "done", path: result.path }),
          (error) => update({ status: "error", error: errorMessage(error) }),
        );
      }
    },
    [id],
  );

  const removeAttachment = useCallback((itemId: string) => dropAttachments(new Set([itemId])), [dropAttachments]);

  // Long pasted text: a file in attachments/, shown as a card (paste.ts).
  const pastedCount = useRef({ second: 0, count: 0 });
  const addPasted = useCallback(
    (text: string) => {
      const now = new Date();
      const second = Math.floor(now.getTime() / 1000);
      const seen = pastedCount.current;
      seen.count = seen.second === second ? seen.count + 1 : 0;
      seen.second = second;
      const file = new File([text], pastedName(now, seen.count), { type: "text/plain" });
      addAttachments([file], describePaste(text));
    },
    [addAttachments],
  );

  // ---- drawing on the page ----------------------------------------------------------------
  const preview = useRef<PreviewHandle>(null);
  const [draw, dispatchDraw] = useReducer(drawReducer, initialDraw);
  const drawRef = useRef(draw);
  useEffect(() => {
    drawRef.current = draw;
  });
  const holdReload = drawingHoldsReload(effectiveMode === "draw", draw.count);
  reloadHeld.current = holdReload;
  // The drawing was sent or removed: the page the person looks at catches up now.
  useEffect(() => {
    if (holdReload || !reloadWaiting) return;
    setReloadWaiting(false);
    setReloadToken((token) => token + 1);
  }, [holdReload, reloadWaiting]);
  // What changed on the page, when asked. A request beginning is the moment the page notes what it says.
  const showChanges = useCallback(() => preview.current?.send({ type: "show-changes" }), []);
  const runningTurn = thread.runningTurnId;
  useEffect(() => {
    if (runningTurn) preview.current?.send({ type: "changes-base" });
  }, [runningTurn]);

  const clearDrawing = useCallback(() => {
    preview.current?.send({ type: "draw-clear" });
    dispatchDraw({ type: "forget" });
  }, []);

  // ---- sending -------------------------------------------------------------------------
  const send = useCallback(
    async (
      typed: string,
      withSelection: Selection | null,
      fromComposer: boolean,
      retryPaths: string[] = [],
      options?: TurnOptions,
    ): Promise<boolean> => {
      if (!project || starting) return false;
      const plan = fromComposer ? planAttachments(attachmentsRef.current) : null;
      // Never send without an attachment the user added: wait for uploads, or for a failed one to be removed.
      if (plan?.blocked) return false;
      const drawing = fromComposer && drawRef.current.count > 0 ? drawRef.current.drawing : null;
      const text = messageText(typed, drawing !== null);
      if (!text) return false;
      setStarting(true);
      if (fromComposer) {
        setDraft("");
        setSelection(null);
      }
      let drawingPath: string | null = null;
      if (drawing) {
        // The drawing is saved first; without it the message would lose what was drawn.
        try {
          const saved = await api.saveDrawing(id, drawing);
          drawingPath = saved.path;
          if (!saved.rendered && saved.note) toast({ message: saved.note });
        } catch (error) {
          setDraft(typed);
          setSelection(withSelection);
          toast({ message: `The drawing couldn’t be saved, so nothing was sent: ${errorMessage(error)}`, kind: "error" });
          setStarting(false);
          return false;
        }
      }
      const paths = turnAttachments(plan ? plan.paths : retryPaths, drawingPath);
      const input: TurnInput = {
        text,
        selection: withSelection,
        connection: project.connection,
        model: project.model,
        attachments: paths,
        // While a turn runs, or messages already wait, the message waits behind them.
        queue: queueing,
        options:
          options ??
          (fromComposer ? optionsForText(lastSteps.current, typed) : undefined) ??
          projectOptions(project.turn_options),
      };
      try {
        try {
          await api.startTurn(id, input);
        } catch (error) {
          // A turn started after this page last heard: queue behind it instead of failing.
          if (!input.queue && error instanceof ApiError && error.code === "turn_running") {
            await api.startTurn(id, { ...input, queue: true });
          } else {
            throw error;
          }
        }
        if (plan) dropAttachments(new Set(plan.ids));
        if (drawingPath) clearDrawing();
        setSendError(null);
        return true;
      } catch (error) {
        if (fromComposer) {
          setDraft(typed);
          setSelection(withSelection);
          // The message is back in the box: say why beside it, and keep saying it.
          setSendError(errorMessage(error));
        } else toast({ message: errorMessage(error), kind: "error" });
        return false;
      } finally {
        setStarting(false);
      }
    },
    [id, project, starting, queueing, setDraft, toast, dropAttachments, clearDrawing],
  );

  /** "Make a skill from this…": an ordinary request, so it queues behind a running one. */
  const startSkillFromWork = useCallback(
    async (note: string, part: Selection | null) => {
      if (!(await send(fromWorkRequest(note), part, false))) return;
      if (part) setSelection(null);
      setMakingSkill(false);
      setChatOpen(true);
    },
    [send, setChatOpen],
  );

  const stop = useCallback(() => {
    if (!thread.runningTurnId) return;
    api.stopTurn(id, thread.runningTurnId).catch((error) => toast({ message: errorMessage(error), kind: "error" }));
  }, [id, thread.runningTurnId, toast]);

  const retry = useCallback(
    (turn: Turn) => void send(turn.text, turn.selection, false, turn.attachments),
    [send],
  );

  // ---- approvals and the queue --------------------------------------------------------
  const decide = useCallback(
    (item: ApprovalItem, decision: "allow" | "deny") => {
      setDecisions((map) => ({ ...map, [item.approvalId]: { pending: decision, error: null } }));
      api.resolveApproval(id, item.turnId, item.approvalId, decision).then(
        // The card collapses when `approval_resolved` arrives; until then it stays busy.
        () => {},
        (error) =>
          setDecisions((map) => ({ ...map, [item.approvalId]: { pending: null, error: errorMessage(error) } })),
      );
    },
    [id],
  );

  const answerQuestion = useCallback<QuestionActions["onAnswer"]>(
    (item, body) => {
      const pending = "skip" in body ? "skip" : "send";
      setAnswering((map) => ({ ...map, [item.questionId]: { pending, error: null } }));
      api.answerQuestion(id, item.turnId, item.questionId, body).then(
        // The card becomes a record when `question_resolved` arrives; until then it stays busy.
        () => {},
        (error) => setAnswering((map) => ({ ...map, [item.questionId]: { pending: null, error: errorMessage(error) } })),
      );
    },
    [id],
  );

  const unqueue = useCallback(
    (queuedId: string) => {
      setUnqueuing((set) => new Set(set).add(queuedId));
      api
        .unqueue(id, queuedId)
        .catch((error) => toast({ message: errorMessage(error), kind: "error" }))
        .finally(() =>
          setUnqueuing((set) => {
            const next = new Set(set);
            next.delete(queuedId);
            return next;
          }),
        );
    },
    [id, toast],
  );

  const setSignedInLooks = useCallback(
    (on: boolean) => {
      if (!project) return;
      const previous = project;
      setProject({ ...project, signed_in_looks: on });
      api.patchProject(id, { signed_in_looks: on }).then(
        (updated) => {
          setProject(updated);
          toast({
            message: on ? "The agent now sees this app signed in as you" : "The agent no longer uses your sign-in",
            kind: "success",
          });
        },
        (error) => {
          setProject(previous);
          toast({ message: errorMessage(error), kind: "error" });
        },
      );
    },
    [id, project, toast],
  );

  const setApprovals = useCallback(
    (value: Approvals) => {
      if (!project || (project.approvals ?? "ask") === value) return;
      const previous = project;
      setProject({ ...project, approvals: value });
      api.patchProject(id, { approvals: value }).then(
        (updated) => {
          setProject(updated);
          toast({
            message: value === "allow" ? "The agent now runs risky commands without asking" : "The agent now asks before risky commands",
            kind: "success",
          });
        },
        (error) => {
          setProject(previous);
          toast({ message: errorMessage(error), kind: "error" });
        },
      );
    },
    [id, project, toast],
  );

  // ---- uploads to the workspace (the Files panel, or pasted text kept in the project) ------
  const uploadToWorkspace = useCallback(
    async (files: File[]) => {
      const added: string[] = [];
      for (const file of files) {
        try {
          const result = await api.upload(id, file);
          added.push(result.path);
        } catch (error) {
          toast({ message: `${file.name} wasn’t added: ${errorMessage(error)}`, kind: "error" });
        }
      }
      if (added.length) {
        toast({
          message: added.length === 1 ? `Added ${added[0]} to the project` : `Added ${added.length} files to the project`,
          kind: "success",
        });
      }
      return added;
    },
    [id, toast],
  );

  // "Keep in the project" on a pasted-text card: the same text, also in the top folder.
  const keeping = useRef(new Set<string>());
  const keepPasted = useCallback(
    (itemId: string) => {
      const item = attachments.find((entry) => entry.id === itemId);
      if (!item?.pasted || item.pasted.kept || keeping.current.has(itemId)) return;   // a second press while it saves
      keeping.current.add(itemId);
      const file = new File([item.pasted.text], item.name, { type: "text/plain" });
      void uploadToWorkspace([file]).then((added) => {
        keeping.current.delete(itemId);
        if (!added.length) return;
        setAttachments((list) =>
          list.map((entry) => (entry.id === itemId && entry.pasted ? { ...entry, pasted: { ...entry.pasted, kept: true } } : entry)),
        );
      });
    },
    [attachments, uploadToWorkspace],
  );

  const changeOptions = useCallback(
    (next: TurnOptions) => {
      setProject((current) => (current ? { ...current, turn_options: next } : current));
      api
        .patchProject(id, { turn_options: next })
        .then(setProject, (error) => toast({ message: errorMessage(error), kind: "error" }));
    },
    [id, toast],
  );

  const pickModel = useCallback(
    (connection: string, model: string | null) => {
      setSendError(null);
      api
        .patchProject(id, { connection, model })
        .then(setProject, (error) => toast({ message: errorMessage(error), kind: "error" }));
    },
    [id, toast],
  );

  // ---- bridge -------------------------------------------------------------------
  const onSelect = useCallback(
    (picked: Selection) => {
      setSelection(picked);
      openChatAndFocus();
    },
    [openChatAndFocus],
  );

  const onTextEdit = useCallback(
    (edit: TextEdit) => {
      if (edit.before_text === edit.after_text) return;
      api.domEdit(id, { path: edit.path, tag: edit.tag || undefined, before_text: edit.before_text, after_text: edit.after_text }).then(
        () => {
          // Pages that refresh themselves skip file-change reloads; an inline edit still reloads.
          scheduleReload("inline_edit");
          toast({ message: "Saved your edit", kind: "success" });
        },
        (error) => {
          // The frame still shows the typed text; reload so the page matches the file again.
          setReloadToken((token) => token + 1);
          const handOff =
            error instanceof ApiError &&
            error.status === 409 &&
            (error.code === "ambiguous_edit" || error.code === "edit_not_found");
          if (!handOff) {
            toast({ message: errorMessage(error), kind: "error" });
            return;
          }
          toast({
            message:
              error.code === "ambiguous_edit"
                ? "That text appears more than once in the file, so the edit wasn’t saved."
                : "That text couldn’t be found in the file, so the edit wasn’t saved.",
            kind: "error",
            action: {
              label: "Ask the agent to do it",
              onClick: () => {
                setDraft(editRequest(edit));
                openChatAndFocus();
              },
            },
          });
        },
      );
    },
    [id, toast, setDraft, openChatAndFocus, scheduleReload],
  );

  // ---- panels ---------------------------------------------------------------------
  const openRight = useCallback((panel: Exclude<RightPanel, null>) => {
    setRightPanel(panel);
    // The drawer takes the chat's corner; step the chat aside and bring it back after.
    setChatOpenState((open) => {
      if (open) reopenChat.current = true;
      return false;
    });
  }, []);

  const openVersions = useCallback(
    (sha: string | null) => {
      openRight("versions");
      setSelectedSha(sha);
    },
    [openRight],
  );

  const closeRight = useCallback(() => {
    setRightPanel(null);
    setSelectedSha(null);
    if (reopenChat.current) {
      reopenChat.current = false;
      setChatOpenState(true);
    }
  }, []);

  const toggleLibrary = useCallback(() => {
    if (libraryOpen) closeRight();
    else {
      setSelectedSha(null);
      setLibraryTab(null);
      openRight("library");
    }
  }, [libraryOpen, closeRight, openRight]);

  // ---- context from this computer -------------------------------------------------
  /** A chip above the message box: the library panel, on its Context tab. */
  const openContextTab = useCallback(() => {
    setSelectedSha(null);
    setLibraryTab((current) => ({ tab: "context", n: (current?.n ?? 0) + 1 }));
    openRight("library");
  }, [openRight]);

  /** The items as the server listed them, or after a removal: chips and counts follow at once. */
  const setContextItems = useCallback((items: ContextItem[]) => {
    setProject((current) => (!current || sameContext(current.context, items) ? current : { ...current, context: items }));
  }, []);

  const contextSaved = useCallback(
    (item: ContextItem) => {
      const added = !contextDialog?.item;
      setContextDialog(null);
      setProject((current) => (current ? { ...current, context: putContextItem(current.context ?? [], item) } : current));
      if (added) toast({ message: `Added “${item.label}” as context. The agent is told about it from the next message.`, kind: "success" });
    },
    [contextDialog, toast],
  );

  const setAttached = useCallback((ids: string[]) => {
    setProject((current) => {
      if (!current) return current;
      const before = current.data_sources ?? [];
      if (before.length === ids.length && before.every((value, index) => value === ids[index])) return current;
      return { ...current, data_sources: ids };
    });
  }, []);

  const toggleLogs = useCallback(() => {
    if (logsOpen) closeRight();
    else {
      setSelectedSha(null);
      openRight("logs");
    }
  }, [logsOpen, closeRight, openRight]);

  // Unsaved edits in the file viewer: closing Files from the top bar or with Escape asks first,
  // the same as the panel's own close button.
  const filesDirty = useRef(false);
  const onFilesDirty = useCallback((dirty: boolean) => {
    filesDirty.current = dirty;
  }, []);
  const closeFiles = useCallback(() => {
    if (mayDiscard(filesDirty.current, (question) => window.confirm(question))) setFilesOpen(false);
  }, []);
  // Leaving the project ("All pages", Library or Setup, the browser's Back) asks the same
  // question; closing or reloading the tab gets the browser's own.
  useEffect(() => setLeaveGuard(() => mayDiscard(filesDirty.current, (question) => window.confirm(question))), []);
  // A 401 while there are edits keeps the tab here and asks for signing in in a new tab.
  useEffect(() => holdSignIn(() => filesDirty.current), []);
  useEffect(() => {
    const onUnload = (event: BeforeUnloadEvent) => holdUnload(event, filesDirty.current);
    window.addEventListener("beforeunload", onUnload);
    return () => window.removeEventListener("beforeunload", onUnload);
  }, []);

  // ---- keyboard ---------------------------------------------------------------------
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && !event.shiftKey && !event.altKey && event.key.toLowerCase() === "k") {
        event.preventDefault();
        openChatAndFocus();
        return;
      }
      if (event.key === "Escape" && !event.defaultPrevented) {
        // Focus is outside every panel: close the most recently relevant thing. An open dialog
        // closes itself (its own listener may run after this one), so the page leaves it alone.
        const target = escapeTarget({
          modalOpen: document.querySelector(MODAL_SELECTOR) !== null,
          versionSelected: selectedSha !== null,
          rightPanel: rightPanel !== null,
          chatOpen,
          filesOpen,
        });
        if (target === "version") setSelectedSha(null);
        else if (target === "right") closeRight();
        else if (target === "chat") setChatOpen(false);
        else if (target === "files") closeFiles();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [openChatAndFocus, selectedSha, rightPanel, filesOpen, chatOpen, setChatOpen, closeRight, closeFiles]);

  // ---- rename -------------------------------------------------------------------------
  const commitName = () => {
    setRenaming(false);
    const name = nameDraft.trim();
    if (!project || !name || name === project.name) return;
    setProject({ ...project, name });
    api.patchProject(id, { name }).then(setProject, (error) => {
      toast({ message: errorMessage(error), kind: "error" });
      refreshProject();
    });
  };

  // The studio only offers a name for a page still called "Untitled": one tap takes it, the cross drops it.
  const offered = offeredName(project);
  const answerOffer = (take: boolean) => {
    if (!project || !offered) return;
    setProject({ ...project, name: take ? offered : project.name, name_suggestion: null });
    api.patchProject(id, take ? { name: offered } : { name_suggestion: null }).then(setProject, (error) => {
      toast({ message: errorMessage(error), kind: "error" });
      refreshProject();
    });
  };

  // ---- render ---------------------------------------------------------------------------
  if (loadError) {
    return (
      <div className="fullpage">
        <div className="fullpage-card">
          <h1>{loadError.missing ? "This page doesn’t exist" : "This page couldn’t be opened"}</h1>
          <p>{loadError.missing ? "It may have been deleted." : loadError.message}</p>
          <div className="fullpage-actions">
            <button type="button" className="btn btn-primary" onClick={() => navigate("/")}>
              Back to all pages
            </button>
            {!loadError.missing && (
              <button type="button" className="btn" onClick={refreshProject}>
                Try again
              </button>
            )}
          </div>
        </div>
      </div>
    );
  }

  // ---- the compact top bar's menu (phone, iPad portrait) -----------------------------------
  const appLive = thread.app?.state === "running" || thread.app?.state === "starting";
  const compactMenu: MenuEntry[] = [
    ...(project?.attach
      ? ([
          { type: "section", label: attachLabel(project.attach), note: attachDetail(project.attach) },
          ...(guest ? [] : [{ label: shipState.label, icon: "play", onSelect: () => setShipOpen(true) }]),
        ] as MenuEntry[])
      : []),
    ...(project?.attach ? ([{ type: "separator" }] as MenuEntry[]) : []),
    { type: "section", label: "Page" },
    ...MODES.map((item) => ({
      label: item.label,
      hint: item.id === "edit" && running ? "Paused while the agent works" : undefined,
      checked: effectiveMode === item.id,
      disabled: item.id === "edit" && running,
      onSelect: () => setMode(item.id),
    })),
    { type: "separator" },
    { label: "Files", icon: "folder", onSelect: () => setFilesOpen(true) },
    { label: "Versions", icon: "history", onSelect: () => openVersions(null) },
    { label: "Cost", icon: "dollar", onSelect: () => setCostOpen(true) },
    {
      label: "Library and data sources",
      icon: "book",
      hint: libraryButton.count ? `${libraryButton.count} for this project` : undefined,
      onSelect: () => {
        setSelectedSha(null);
        setLibraryTab(null);
        openRight("library");
      },
    },
    ...(appConfigured && thread.app?.state === "external"
      ? ([
          { type: "section", label: "App · Your server", note: appInfo?.address ?? "The studio shows it and does not start or stop it." },
        ] as MenuEntry[])
      : appConfigured && thread.app
      ? ([
          { type: "section", label: `App · ${appStateLabel(thread.app).label}` },
          { label: "Start", icon: "play", disabled: appBusy !== null || appLive, onSelect: () => runAppAction("start") },
          { label: "Stop", icon: "stop", disabled: appBusy !== null || !appLive, onSelect: () => runAppAction("stop") },
          { label: "Restart", icon: "refresh", disabled: appBusy !== null, onSelect: () => runAppAction("restart") },
          { label: "App logs", icon: "terminal", onSelect: () => openRight("logs") },
        ] as MenuEntry[])
      : []),
    ...(!appConfigured && appInfo?.error
      ? ([
          {
            label: "studio.json problem",
            icon: "warning",
            onSelect: () => toast({ message: appInfo.error ?? "", kind: "error" }),
          },
        ] as MenuEntry[])
      : []),
    { type: "separator" },
    ...(project?.uses_template
      ? ([{ type: "section", label: `Using template: ${project.uses_template.label}` }] as MenuEntry[])
      : []),
    {
      label: "Save as template…",
      icon: "page",
      hint: guest ? "Only the studio's owner" : running ? "After the agent finishes" : undefined,
      disabled: running || guest,
      onSelect: () => setSavingTemplate(true),
    },
    makeSkillEntry(() => setMakingSkill(true)),
    ...instructionsEntries(project, () => setInstructionsOpen(true)),
    {
      label: "Export…",
      icon: "download",
      onSelect: () => setExportOpen(true),
    },
    ...(project
      ? ([{ label: "Open in a new tab", icon: "external", onSelect: () => window.open(project.preview_url, "_blank", "noopener") }] as MenuEntry[])
      : []),
    {
      label: theme === "dark" ? "Light theme" : "Dark theme",
      icon: theme === "dark" ? "sun" : "moon",
      onSelect: onToggleTheme,
    },
    ...signedInEntries(project, Boolean(health.shared), setSignedInLooks),
  ];

  const status = needsYou ? "Waiting for you" : running ? "Working" : stream === "reconnecting" ? "Reconnecting" : "";

  return (
    <div
      className={`project${compact ? " is-compact" : ""}${effectiveMode === "draw" ? " is-drawing" : ""}${rightPanel ? " has-right" : ""}${logsOpen || libraryOpen ? " has-logs" : ""}${filesOpen ? " has-left" : ""}${chatOpen ? " has-chat" : ""}`}
    >
      {compact ? (
        <header className="topbar is-compact">
          <IconButton icon="arrow-left" label="All pages" tipAlign="start" onClick={() => navigate("/")} />
          {renaming ? (
            <input
              className="name-input"
              aria-label="Page name"
              value={nameDraft}
              autoFocus
              maxLength={120}
              onFocus={(event) => event.target.select()}
              onChange={(event) => setNameDraft(event.target.value)}
              onBlur={commitName}
              onKeyDown={(event) => {
                if (event.key === "Enter") commitName();
                if (event.key === "Escape") {
                  event.stopPropagation();
                  setRenaming(false);
                }
              }}
            />
          ) : (
            <button
              type="button"
              className="name-btn"
              aria-label={`Rename ${project?.name ?? "this page"}`}
              disabled={!project}
              onClick={() => {
                setNameDraft(project?.name ?? "");
                setRenaming(true);
              }}
            >
              <span>{project?.name ?? " "}</span>
            </button>
          )}
          <span
            className={`status is-dot${status ? " is-on" : ""}${running ? " is-working" : ""}${needsYou ? " is-attention" : ""}`}
            aria-live="polite"
          >
            {status && (
              <>
                <span className="status-dot" aria-hidden="true" />
                <span className="sr-only">{status}</span>
              </>
            )}
          </span>
          <span className="topbar-spacer" />
          {effectiveMode !== "browse" && (
            <span className="mode-chip" aria-hidden="true">
              {effectiveMode === "select" ? "Select" : effectiveMode === "draw" ? "Draw" : "Edit"}
            </span>
          )}
          {project?.attach && !guest && (
            <button
              type="button"
              className={`ship-btn is-${shipState.state} is-compact`}
              aria-label={shipState.label}
              onClick={() => setShipOpen(true)}
            >
              {shipState.state === "running" ? <Spinner size={13} /> : <Icon name="play" size={13} />}
              <span>{shipState.short}</span>
            </button>
          )}
          <Menu
            label="Page menu"
            align="end"
            entries={compactMenu}
            trigger={(props) => (
              <button type="button" className="icon-btn topbar-more" aria-label="Page menu" {...props}>
                <Icon name="more" size={18} />
                {(libraryButton.count > 0 || appConfigured) && <span className="topbar-more-dot" aria-hidden="true" />}
              </button>
            )}
          />
        </header>
      ) : (
      <header className="topbar">
        <div className="topbar-side">
          <IconButton icon="arrow-left" label="All pages" tipAlign="start" onClick={() => navigate("/")} />
          <IconButton
            icon="folder"
            label="Files"
            tip={filesOpen ? "Hide files" : "Files"}
            active={filesOpen}
            onClick={() => (filesOpen ? closeFiles() : setFilesOpen(true))}
          />
          <span className="topbar-divider" aria-hidden="true" />
          {renaming ? (
            <input
              className="name-input"
              aria-label="Page name"
              value={nameDraft}
              autoFocus
              maxLength={120}
              onFocus={(event) => event.target.select()}
              onChange={(event) => setNameDraft(event.target.value)}
              onBlur={commitName}
              onKeyDown={(event) => {
                if (event.key === "Enter") commitName();
                if (event.key === "Escape") {
                  event.stopPropagation();
                  setRenaming(false);
                }
              }}
            />
          ) : (
            <button
              type="button"
              className="name-btn"
              data-tip="Rename"
              data-tip-align="start"
              disabled={!project}
              onClick={() => {
                setNameDraft(project?.name ?? "");
                setRenaming(true);
              }}
            >
              <span>{project?.name ?? " "}</span>
            </button>
          )}
          <span
            className={`status${status ? " is-on" : ""}${running ? " is-working" : ""}${needsYou ? " is-attention" : ""}`}
            aria-live="polite"
          >
            {status && (
              <>
                <span className="status-dot" aria-hidden="true" />
                {status}…
              </>
            )}
          </span>
          {project?.attach && (
            <span
              className={`attach-chip${project.attach.mode === "in_place" ? " is-in-place" : ""}`}
              data-tip={`${attachDetail(project.attach)} (${project.attach.path})`}
              data-tip-align="start"
              aria-label={`${attachLabel(project.attach)}. ${attachDetail(project.attach)}`}
            >
              <Icon name="folder" size={12} />
              <span>{attachLabel(project.attach)}</span>
            </span>
          )}
        </div>

        <div className="segmented" role="radiogroup" aria-label="Preview mode">
          <span
            className="segmented-thumb"
            data-index={MODES.findIndex((item) => item.id === effectiveMode)}
            aria-hidden="true"
          />
          {MODES.map((item) => {
            const disabled = item.id === "edit" && running;
            return (
              <button
                key={item.id}
                type="button"
                role="radio"
                aria-checked={effectiveMode === item.id}
                aria-disabled={disabled || undefined}
                className={`segment${effectiveMode === item.id ? " is-on" : ""}${disabled ? " is-disabled" : ""}`}
                data-tip={disabled ? "Editing is paused while the agent works" : item.tip}
                aria-description={disabled ? "Editing is paused while the agent works" : item.tip}
                onClick={() => {
                  if (!disabled) setMode(item.id);
                }}
              >
                {item.label}
              </button>
            );
          })}
        </div>

        <div className="topbar-side is-end">
          {project?.attach && !guest && (
            <button
              type="button"
              className={`ship-btn is-${shipState.state}`}
              data-tip={
                shipState.state === "setup"
                  ? "Set up how this app is made live"
                  : `Send the change to ${project.attach.path}`
              }
              data-tip-align="end"
              onClick={() => setShipOpen(true)}
            >
              {shipState.state === "running" ? <Spinner size={13} /> : <Icon name="play" size={13} />}
              <span>{shipState.label}</span>
            </button>
          )}
          <AppControl
            app={thread.app}
            info={appInfo}
            error={appError}
            projectSaysConfigured={Boolean(project?.app?.configured)}
            busy={appBusy}
            logsOpen={logsOpen}
            onAction={runAppAction}
            onShowLogs={() => {
              if (!logsOpen) toggleLogs();
            }}
            onRetry={refreshApp}
          />
          {appConfigured && (
            <IconButton
              icon="terminal"
              label="App logs"
              tip={logsOpen ? "Hide app logs" : "App logs"}
              tipAlign="end"
              active={logsOpen}
              onClick={toggleLogs}
            />
          )}
          <button
            type="button"
            className={`icon-btn lib-btn${libraryOpen ? " is-active" : ""}`}
            aria-pressed={libraryOpen}
            aria-label={libraryButton.label}
            data-tip={libraryButton.tip}
            data-tip-align="end"
            onClick={toggleLibrary}
          >
            <Icon name="book" size={16} />
            {libraryButton.count > 0 && (
              <span className="lib-btn-count" aria-hidden="true">
                {libraryButton.count}
              </span>
            )}
          </button>
          <IconButton
            icon="history"
            label="Versions"
            tip={versionsOpen ? "Hide versions" : "Versions"}
            tipAlign="end"
            active={versionsOpen}
            onClick={() => (versionsOpen ? closeRight() : openVersions(null))}
          />
          <IconButton icon="dollar" label="Cost" tip="What the requests cost" tipAlign="end" onClick={() => setCostOpen(true)} />
          {project ? (
            <a
              className="icon-btn"
              href={project.preview_url}
              target="_blank"
              rel="noreferrer"
              aria-label="Open the page in a new tab"
              data-tip="Open in a new tab"
              data-tip-align="end"
            >
              <Icon name="external" size={16} />
            </a>
          ) : (
            <span className="icon-btn" aria-hidden="true" />
          )}
          <IconButton
            icon={theme === "dark" ? "sun" : "moon"}
            label={theme === "dark" ? "Switch to light theme" : "Switch to dark theme"}
            tip={theme === "dark" ? "Light theme" : "Dark theme"}
            tipAlign="end"
            onClick={onToggleTheme}
          />
          <Menu
            label="More"
            align="end"
            entries={[
              ...(project?.attach
                ? ([{ type: "section", label: attachLabel(project.attach), note: attachDetail(project.attach) }] as MenuEntry[])
                : []),
              ...(project?.uses_template
                ? ([{ type: "section", label: `Using template: ${project.uses_template.label}` }] as MenuEntry[])
                : []),
              {
                label: "Save as template…",
                icon: "page",
                hint: running ? "After the agent finishes" : undefined,
                disabled: running,
                onSelect: () => setSavingTemplate(true),
              },
              makeSkillEntry(() => setMakingSkill(true)),
              ...instructionsEntries(project, () => setInstructionsOpen(true)),
              {
                label: "Export…",
                icon: "download",
                onSelect: () => setExportOpen(true),
              },
              ...signedInEntries(project, Boolean(health.shared), setSignedInLooks),
            ]}
            trigger={(props) => (
              <button type="button" className="icon-btn" aria-label="More" data-tip="More" data-tip-align="end" {...props}>
                <Icon name="more" size={16} />
              </button>
            )}
          />
        </div>
      </header>
      )}
      {savingTemplate && project && (
        <SaveTemplateDialog
          projectId={id}
          projectName={project.name}
          hasBackEnd={appConfigured}
          onClose={() => setSavingTemplate(false)}
          onSaved={(template) => {
            setSavingTemplate(false);
            toast({ message: `Saved as a template: “${template.label}”`, kind: "success" });
          }}
        />
      )}
      {makingSkill && project && (
        <MakeSkillDialog
          hasBackEnd={appConfigured}
          selection={selection}
          sending={starting}
          onStart={startSkillFromWork}
          onClose={() => setMakingSkill(false)}
        />
      )}

      <main className="stage">
        {reloadWaiting && effectiveMode !== "draw" && (
          <div className="stage-note" role="status">
            {heldReloadNote(false, draw.count)}
          </div>
        )}
        {offered && !renaming && effectiveMode === "browse" && !reloadWaiting && (
          <div className="name-suggest" role="status">
            <span className="name-suggest-text">
              Name this page <strong>{offered}</strong>?
            </span>
            <button type="button" className="btn btn-small name-suggest-use" onClick={() => answerOffer(true)}>
              Use it
            </button>
            <button
              type="button"
              className="name-suggest-no"
              aria-label="Keep it untitled"
              onClick={() => answerOffer(false)}
            >
              <Icon name="x" size={14} />
            </button>
          </div>
        )}
        {effectiveMode === "draw" && (
          <DrawToolbar
            tool={draw.tool}
            color={draw.color}
            count={draw.count}
            note={reloadWaiting ? heldReloadNote(true, draw.count) : null}
            onTool={(tool) => dispatchDraw({ type: "tool", tool })}
            onColor={(color) => dispatchDraw({ type: "color", color })}
            onUndo={() => preview.current?.send({ type: "draw-undo" })}
            onClear={() => preview.current?.send({ type: "draw-clear" })}
            onDone={() => {
              setMode("browse");
              // Show where the drawing went: the composer's chip.
              if (draw.count > 0) setChatOpen(true);
            }}
          />
        )}
        {compact && (effectiveMode === "select" || effectiveMode === "edit") && (
          <div className="mode-bar" role="status">
            <span>
              {effectiveMode === "select"
                ? "Tap anything on the page to point the agent at it."
                : "Tap any text on the page to change it."}
            </span>
            <button type="button" className="btn btn-small" onClick={() => setMode("browse")}>
              Done
            </button>
          </div>
        )}
        {project ? (
          <Preview
            key={project.id}
            previewUrl={project.preview_url}
            signsInByItself={health.page_sign_in === true}
            title={project.name}
            mode={effectiveMode}
            reloadToken={reloadToken}
            onSelect={onSelect}
            onTextEdit={onTextEdit}
            onAutoReload={(value) => {
              autoReload.current = value;
            }}
            onEnlarge={openLarge}
            onCanShowChanges={setCanShowChanges}
            onChanges={(count) =>
              toast({
                message:
                  count === null
                    ? "This tab has no note of the page from before the request, so it cannot tell what changed."
                    : count === 0
                      ? "Nothing on this page reads differently. The change may be on another page, or something was removed."
                      : `${count} ${count === 1 ? "place" : "places"} on this page changed: outlined for a few seconds.`,
              })
            }
            ref={preview}
            drawTool={draw.tool}
            drawColor={draw.color}
            onDrawing={(count, drawing) => dispatchDraw({ type: "frame-drawing", count, drawing })}
            onPageLoad={() => dispatchDraw({ type: "frame-ready" })}
          />
        ) : (
          <div className="preview" />
        )}

        {shipOpen && project?.attach && project.ship && (
          <ShipPanel
            project={project}
            view={project.ship}
            liveStep={shipStep}
            tick={shipTick}
            onClose={() => setShipOpen(false)}
            onChanged={() => {
              refreshProject();
              setShipTick((tick) => tick + 1);
            }}
          />
        )}

        {filesOpen && project && (
          <FilesPanel
            projectId={id}
            previewUrl={project.preview_url}
            filesRev={thread.filesRev}
            changedPaths={runningChangedPaths(thread)}
            running={running}
            onUpload={uploadToWorkspace}
            downloadUrl={api.downloadUrl(id)}
            onClose={() => setFilesOpen(false)}
            onDirtyChange={onFilesDirty}
            openRequest={fileAsk}
          />
        )}

        {versionsOpen && (
          <VersionsDrawer
            projectId={id}
            versionsRev={thread.versionsRev}
            running={running}
            selectedSha={selectedSha}
            onSelect={setSelectedSha}
            onClose={closeRight}
          />
        )}

        {libraryOpen && (
          <ProjectLibraryPanel
            projectId={id}
            onAttachedChange={setAttached}
            project={project}
            running={running}
            onProjectChange={setProject}
            onManage={() => navigate("/library#data-sources")}
            onClose={closeRight}
            tabRequest={libraryTab}
            onAddContext={guest ? undefined : () => setContextDialog({ item: null })}
            onEditContext={(item) => setContextDialog({ item })}
            onContextChange={setContextItems}
          />
        )}

        {contextDialog && (
          <ContextDialog
            projectId={id}
            item={contextDialog.item}
            count={project?.context?.length ?? 0}
            onSaved={contextSaved}
            onClose={() => setContextDialog(null)}
          />
        )}

        {largePicture && <PictureViewer picture={largePicture} onClose={closeLarge} />}

        {instructionsOpen && project && instructionsKept(project) && (
          <InstructionsDialog
            projectId={id}
            saved={project.instructions ?? ""}
            everywhere={everywhere}
            running={running}
            onSaved={(next, text) => {
              setInstructionsOpen(false);
              setProject((current) => (current ? { ...current, instructions: next.instructions ?? "" } : current));
              toast({ message: instructionsSavedMessage(text), kind: "success" });
            }}
            onSavedEverywhere={(text) => {
              setInstructionsOpen(false);
              setEverywhere(text);
              toast({ message: instructionsSavedMessage(text, "everywhere"), kind: "success" });
            }}
            onClose={() => setInstructionsOpen(false)}
          />
        )}

        {logsOpen && <LogsDrawer projectId={id} app={thread.app} onClose={closeRight} />}

        {costOpen && <CostDialog projectId={id} running={running} onClose={() => setCostOpen(false)} />}
        {exportOpen && <ExportDialog projectId={id} onClose={() => setExportOpen(false)} />}

        {project && (
          <Chat
            ref={chat}
            open={chatOpen}
            onOpenChange={setChatOpen}
            thread={thread}
            project={project}
            connections={health.connections}
            draft={draft}
            onDraftChange={setDraft}
            selection={selection}
            onClearSelection={() => setSelection(null)}
            onSend={(text) => void send(text, selection, true)}
            onStop={stop}
            onRetry={retry}
            onOpenVersion={openVersions}
            onPickModel={pickModel}
            options={projectOptions(project.turn_options)}
            onOptionsChange={changeOptions}
            onVerifyPage={() =>
              // Only a check: no depth block, nothing new asked for, and nothing picked by the studio itself.
              void send(VERIFY_ONLY_TEXT, null, false, [], CHECK_ONLY_OPTIONS)
            }
            onPolishPage={() =>
              // Only the look: nothing new asked for, the project's depth and verify choice left out of it.
              void send(POLISH_TEXT, null, false, [], POLISH_OPTIONS)
            }
            starting={starting}
            attachments={attachments}
            onAddFiles={addAttachments}
            onRemoveAttachment={removeAttachment}
            onAddPasted={addPasted}
            sendError={sendError}
            onShowChanges={canShowChanges ? showChanges : undefined}
            onKeepPasted={keepPasted}
            onDecide={decide}
            decisions={decisions}
            onAnswer={answerQuestion}
            answering={answering}
            fileHref={fileHref}
            chatFiles={chatFiles}
            onOpenFile={openFileFromChat}
            readDrawing={readDrawing}
            onUnqueue={unqueue}
            unqueuing={unqueuing}
            onSetApprovals={setApprovals}
            drawingMarks={draw.count}
            onRemoveDrawing={clearDrawing}
            hidden={compact && effectiveMode === "draw"}
            compact={compact}
            canDictate={Boolean(health.transcription?.available)}
            onAddContext={guest ? undefined : () => setContextDialog({ item: null })}
            onOpenContext={openContextTab}
            onOpenInstructions={instructionsKept(project) ? () => setInstructionsOpen(true) : undefined}
            everywhereInstructions={everywhere}
          />
        )}

      </main>
      <span className="sr-only">Press {FOCUS_SHORTCUT} to write to the agent.</span>
    </div>
  );
}
