// Dictate a message: record in the browser, transcribe on this machine, and hand
// the text to the composer. It never sends anything by itself.

import { useCallback, useEffect, useReducer, useRef, useState } from "react";
import { api, errorMessage } from "./api";
import { Icon, Spinner } from "./icons";
import {
  extensionFor,
  formatElapsed,
  idleVoice,
  MAX_RECORDING_MS,
  microphoneProblem,
  pickRecordingFormat,
  voiceReducer,
  type VoiceState,
} from "./voice";

interface VoiceButtonProps {
  /** Called as the button is pressed (before focus moves), so the composer can remember its caret. */
  onBegin: () => void;
  onText: (text: string) => void;
  /** A plain message for the person: nothing heard, microphone refused, or a server error. */
  onMessage: (message: string, kind: "info" | "error") => void;
  onPhaseChange?: (phase: VoiceState["phase"]) => void;
}

function minuteAnnouncement(ms: number): string {
  const minutes = Math.floor(ms / 60000);
  return minutes ? `Recording, ${minutes} ${minutes === 1 ? "minute" : "minutes"}` : "Recording";
}

export function VoiceButton({ onBegin, onText, onMessage, onPhaseChange }: VoiceButtonProps) {
  const [state, dispatch] = useReducer(voiceReducer, idleVoice);
  const [now, setNow] = useState(() => Date.now());
  const stream = useRef<MediaStream | null>(null);
  const recorder = useRef<MediaRecorder | null>(null);
  const chunks = useRef<Blob[]>([]);
  const extension = useRef("webm");
  // Set when the person cancels, so the recording that stops is thrown away.
  const discard = useRef(false);
  const handlers = useRef({ onText, onMessage });
  useEffect(() => {
    handlers.current = { onText, onMessage };
  });

  useEffect(() => {
    onPhaseChange?.(state.phase);
  }, [state.phase, onPhaseChange]);

  const releaseStream = useCallback(() => {
    // Stopping every track is what turns the browser's recording indicator off.
    stream.current?.getTracks().forEach((track) => track.stop());
    stream.current = null;
  }, []);

  const transcribe = useCallback(async (recording: Blob) => {
    try {
      const answer = await api.transcribe(recording, `voice.${extension.current}`);
      const text = (answer.text ?? "").trim();
      if (text) handlers.current.onText(text);
      else handlers.current.onMessage("Nothing was heard.", "info");
    } catch (error) {
      handlers.current.onMessage(errorMessage(error), "error");
    } finally {
      dispatch({ type: "done" });
    }
  }, []);

  const start = useCallback(async () => {
    dispatch({ type: "ask" });
    let media: MediaStream;
    try {
      media = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch (error) {
      dispatch({ type: "cancel" });
      handlers.current.onMessage(microphoneProblem(error), "error");
      return;
    }
    if (discard.current) {
      // Cancelled while the browser was asking for permission.
      discard.current = false;
      media.getTracks().forEach((track) => track.stop());
      return;
    }
    stream.current = media;
    const format = pickRecordingFormat((mime) => {
      try {
        return MediaRecorder.isTypeSupported(mime);
      } catch {
        return false;
      }
    });
    let rec: MediaRecorder;
    try {
      rec = format.mimeType ? new MediaRecorder(media, { mimeType: format.mimeType }) : new MediaRecorder(media);
    } catch (error) {
      releaseStream();
      dispatch({ type: "cancel" });
      handlers.current.onMessage(microphoneProblem(error), "error");
      return;
    }
    extension.current = extensionFor(rec.mimeType, format.extension);
    chunks.current = [];
    rec.ondataavailable = (event) => {
      if (event.data && event.data.size) chunks.current.push(event.data);
    };
    rec.onstop = () => {
      releaseStream();
      recorder.current = null;
      if (discard.current) {
        discard.current = false;
        chunks.current = [];
        dispatch({ type: "cancel" });
        return;
      }
      const recording = new Blob(chunks.current, { type: rec.mimeType || chunks.current[0]?.type || "audio/webm" });
      chunks.current = [];
      dispatch({ type: "stopped" });
      void transcribe(recording);
    };
    recorder.current = rec;
    rec.start();
    const at = Date.now();
    setNow(at);
    dispatch({ type: "started", at });
  }, [releaseStream, transcribe]);

  const stop = useCallback(() => {
    if (recorder.current && recorder.current.state !== "inactive") recorder.current.stop();
  }, []);

  const cancel = useCallback(() => {
    discard.current = true;
    if (recorder.current && recorder.current.state !== "inactive") recorder.current.stop();
    else {
      releaseStream();
      dispatch({ type: "cancel" });
    }
  }, [releaseStream]);

  // The timer, and the five-minute limit.
  useEffect(() => {
    if (state.phase !== "recording") return;
    const timer = window.setInterval(() => {
      const t = Date.now();
      setNow(t);
      if (t - state.startedAt >= MAX_RECORDING_MS) stop();
    }, 250);
    return () => window.clearInterval(timer);
  }, [state, stop]);

  // Escape cancels a recording (before the chat's own Escape closes it).
  useEffect(() => {
    if (state.phase !== "recording" && state.phase !== "asking") return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      event.preventDefault();
      event.stopPropagation();
      cancel();
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [state.phase, cancel]);

  // Leaving the page mid-recording must not leave the microphone on.
  useEffect(
    () => () => {
      discard.current = true;
      if (recorder.current && recorder.current.state !== "inactive") recorder.current.stop();
      stream.current?.getTracks().forEach((track) => track.stop());
    },
    [],
  );

  if (state.phase === "idle") {
    return (
      <button
        type="button"
        className="icon-btn voice-btn"
        aria-label="Dictate a message"
        data-tip="Dictate a message"
        data-tip-side="top"
        // Before focus leaves the message box, so its caret can be remembered.
        onPointerDown={onBegin}
        onClick={() => void start()}
      >
        <Icon name="mic" size={16} />
      </button>
    );
  }

  if (state.phase === "transcribing") {
    return (
      <span className="voice-live is-busy" role="status">
        <Spinner size={13} /> Transcribing…
      </span>
    );
  }

  const elapsed = state.phase === "recording" ? formatElapsed(now - state.startedAt) : "0:00";
  return (
    <span className="voice-live">
      <button
        type="button"
        className="icon-btn voice-cancel"
        aria-label="Cancel recording"
        data-tip="Cancel (Esc)"
        data-tip-side="top"
        onClick={cancel}
      >
        <Icon name="x" size={15} />
      </button>
      <span className="voice-timer" role="timer">
        <span className="voice-dot" aria-hidden="true" />
        {state.phase === "asking" ? "…" : elapsed}
      </span>
      {/* Announced once when recording starts and then once a minute, not every second. */}
      <span className="sr-only" aria-live="polite">
        {state.phase === "asking" ? "Asking for the microphone" : minuteAnnouncement(now - (state.phase === "recording" ? state.startedAt : now))}
      </span>
      <button
        type="button"
        className="icon-btn voice-stop"
        aria-label="Stop recording"
        data-tip="Stop and transcribe"
        data-tip-side="top"
        disabled={state.phase !== "recording"}
        onClick={stop}
      >
        <Icon name="stop" size={15} />
      </button>
    </span>
  );
}
