import { describe, expect, it } from "vitest";
import {
  canRecord,
  extensionFor,
  formatElapsed,
  idleVoice,
  insertDictation,
  microphoneProblem,
  pickRecordingFormat,
  voiceReducer,
} from "./voice";

describe("voiceReducer", () => {
  it("goes idle → asking → recording → transcribing → idle", () => {
    let state = voiceReducer(idleVoice, { type: "ask" });
    expect(state.phase).toBe("asking");
    state = voiceReducer(state, { type: "started", at: 1000 });
    expect(state).toEqual({ phase: "recording", startedAt: 1000 });
    state = voiceReducer(state, { type: "stopped" });
    expect(state.phase).toBe("transcribing");
    expect(voiceReducer(state, { type: "done" })).toBe(idleVoice);
  });

  it("cancels from asking or recording, and ignores steps out of order", () => {
    expect(voiceReducer({ phase: "asking" }, { type: "cancel" })).toBe(idleVoice);
    expect(voiceReducer({ phase: "recording", startedAt: 1 }, { type: "cancel" })).toBe(idleVoice);
    expect(voiceReducer(idleVoice, { type: "stopped" })).toBe(idleVoice);
    expect(voiceReducer(idleVoice, { type: "started", at: 5 })).toBe(idleVoice);
    const recording = { phase: "recording" as const, startedAt: 1 };
    expect(voiceReducer(recording, { type: "ask" })).toBe(recording);
  });
});

describe("recording format", () => {
  it("prefers webm/opus, then webm, then mp4 (Safari), else the default", () => {
    expect(pickRecordingFormat(() => true)).toEqual({ mimeType: "audio/webm;codecs=opus", extension: "webm" });
    expect(pickRecordingFormat((mime) => mime === "audio/webm")).toEqual({ mimeType: "audio/webm", extension: "webm" });
    expect(pickRecordingFormat((mime) => mime === "audio/mp4")).toEqual({ mimeType: "audio/mp4", extension: "mp4" });
    expect(pickRecordingFormat(() => false)).toEqual({ mimeType: undefined, extension: "webm" });
  });

  it("names the upload after what was actually recorded", () => {
    expect(extensionFor("audio/mp4;codecs=mp4a.40.2", "webm")).toBe("mp4");
    expect(extensionFor("audio/webm;codecs=opus", "mp4")).toBe("webm");
    expect(extensionFor("", "mp4")).toBe("mp4");
    expect(extensionFor(undefined, "webm")).toBe("webm");
  });
});

describe("insertDictation", () => {
  it("appends with a space when there is no caret", () => {
    expect(insertDictation("Make the title", "bigger please", null)).toEqual({
      value: "Make the title bigger please",
      caret: 28,
    });
    expect(insertDictation("", " hello ", null)).toEqual({ value: "hello", caret: 5 });
    expect(insertDictation("Ends with space ", "next", null).value).toBe("Ends with space next");
  });

  it("inserts at the caret with spaces on both sides as needed", () => {
    expect(insertDictation("Make bigger", "the title", 4)).toEqual({ value: "Make the title bigger", caret: 14 });
    expect(insertDictation("Make  bigger", "it", 5).value).toBe("Make it bigger");
  });

  it("replaces a selection", () => {
    expect(insertDictation("Make it red now", "blue", 8, 11).value).toBe("Make it blue now");
  });

  it("leaves the draft alone when nothing was said", () => {
    expect(insertDictation("Keep me", "   ", 2)).toEqual({ value: "Keep me", caret: 2 });
  });

  it("copes with a caret past the end", () => {
    expect(insertDictation("abc", "d", 99).value).toBe("abc d");
  });
});

describe("timer and checks", () => {
  it("formats m:ss", () => {
    expect(formatElapsed(0)).toBe("0:00");
    expect(formatElapsed(9_999)).toBe("0:09");
    expect(formatElapsed(65_000)).toBe("1:05");
    expect(formatElapsed(300_000)).toBe("5:00");
    expect(formatElapsed(-5)).toBe("0:00");
  });

  it("needs getUserMedia and MediaRecorder to record", () => {
    expect(canRecord({ mediaDevices: { getUserMedia: () => null }, MediaRecorder: function () {} })).toBe(true);
    expect(canRecord({ mediaDevices: undefined, MediaRecorder: function () {} })).toBe(false);
    expect(canRecord({ mediaDevices: { getUserMedia: () => null } })).toBe(false);
  });

  it("explains microphone failures plainly", () => {
    expect(microphoneProblem({ name: "NotAllowedError" })).toBe("The browser did not allow the microphone.");
    expect(microphoneProblem({ name: "NotFoundError" })).toBe("No microphone was found.");
    expect(microphoneProblem(new Error("x"))).toBe("The microphone could not be started.");
  });
});
