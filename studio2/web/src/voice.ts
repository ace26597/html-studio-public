// Voice input: pure parts (state, recording format, inserting the text, the timer).

/** Recording stops by itself after this long. */
export const MAX_RECORDING_MS = 5 * 60 * 1000;

export type VoiceState =
  | { phase: "idle" }
  | { phase: "asking" }
  | { phase: "recording"; startedAt: number }
  | { phase: "transcribing" };

export type VoiceAction =
  | { type: "ask" }
  | { type: "started"; at: number }
  | { type: "stopped" }
  | { type: "done" }
  | { type: "cancel" };

export const idleVoice: VoiceState = { phase: "idle" };

export function voiceReducer(state: VoiceState, action: VoiceAction): VoiceState {
  switch (action.type) {
    case "ask":
      return state.phase === "idle" ? { phase: "asking" } : state;
    case "started":
      return state.phase === "asking" ? { phase: "recording", startedAt: action.at } : state;
    case "stopped":
      return state.phase === "recording" ? { phase: "transcribing" } : state;
    case "done":
    case "cancel":
      return state.phase === "idle" ? state : idleVoice;
  }
}

const CANDIDATES: { mime: string; extension: string }[] = [
  { mime: "audio/webm;codecs=opus", extension: "webm" },
  { mime: "audio/webm", extension: "webm" },
  { mime: "audio/mp4", extension: "mp4" },
];

/**
 * The first recording format the browser supports; `mimeType` undefined means
 * "the browser's default". The extension names the upload, which the server reads.
 */
export function pickRecordingFormat(isSupported: (mime: string) => boolean): { mimeType: string | undefined; extension: string } {
  for (const candidate of CANDIDATES) {
    if (isSupported(candidate.mime)) return { mimeType: candidate.mime, extension: candidate.extension };
  }
  return { mimeType: undefined, extension: "webm" };
}

/** The extension for what the recorder actually produced (its `mimeType`), falling back to the chosen one. */
export function extensionFor(actualMime: string | undefined, fallback: string): string {
  if (!actualMime) return fallback;
  if (/mp4|m4a|aac/i.test(actualMime)) return "mp4";
  if (/webm/i.test(actualMime)) return "webm";
  if (/ogg/i.test(actualMime)) return "ogg";
  if (/wav/i.test(actualMime)) return "wav";
  return fallback;
}

/** m:ss for the recording timer. */
export function formatElapsed(ms: number): string {
  const total = Math.max(0, Math.floor(ms / 1000));
  const minutes = Math.floor(total / 60);
  const seconds = total % 60;
  return `${minutes}:${String(seconds).padStart(2, "0")}`;
}

/**
 * Put dictated text into the draft: at the caret (replacing a selection) when there is one,
 * otherwise at the end; with a single space between it and neighbouring text.
 */
export function insertDictation(
  draft: string,
  text: string,
  selectionStart: number | null,
  selectionEnd: number | null = selectionStart,
): { value: string; caret: number } {
  const spoken = text.trim();
  if (!spoken) return { value: draft, caret: selectionEnd ?? draft.length };
  const start = selectionStart === null ? draft.length : Math.min(Math.max(0, selectionStart), draft.length);
  const end = selectionEnd === null ? start : Math.min(Math.max(start, selectionEnd), draft.length);
  const before = draft.slice(0, start);
  const after = draft.slice(end);
  const lead = before && !/\s$/.test(before) ? " " : "";
  const trail = after && !/^\s/.test(after) ? " " : "";
  const value = `${before}${lead}${spoken}${trail}${after}`;
  return { value, caret: before.length + lead.length + spoken.length };
}

/** Whether this browser, on this page, can record (needs a secure page). */
export function canRecord(env: { mediaDevices?: { getUserMedia?: unknown } | undefined; MediaRecorder?: unknown }): boolean {
  return typeof env.mediaDevices?.getUserMedia === "function" && typeof env.MediaRecorder === "function";
}

/** Plain words for why the microphone could not be used. */
export function microphoneProblem(error: unknown): string {
  const name = (error as { name?: string } | null)?.name ?? "";
  if (name === "NotAllowedError" || name === "SecurityError" || name === "PermissionDeniedError") {
    return "The browser did not allow the microphone.";
  }
  if (name === "NotFoundError" || name === "DevicesNotFoundError" || name === "OverconstrainedError") {
    return "No microphone was found.";
  }
  if (name === "NotReadableError" || name === "TrackStartError") {
    return "The microphone is in use by another app.";
  }
  return "The microphone could not be started.";
}
