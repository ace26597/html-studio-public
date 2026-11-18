import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { threadHasCards } from "./chatFiles";
import { FileCards } from "./FileCards";
import { initialThread, lastTurn, reduceEvents, type Turn } from "./thread";
import type { StudioEvent } from "./types";

const ev = (seq: number, e: Record<string, unknown>) =>
  ({ seq, ts: `2026-11-18T09:00:0${seq}Z`, turn_id: "t_a", ...e }) as unknown as StudioEvent;

const files = [
  { path: "files/deck.pptx", status: "added" },
  { path: "files/old.pdf", status: "modified" },
  { path: "index.html", status: "modified" },
];
const events = [
  ev(1, { type: "turn_started", text: "Make a deck", selection: null, connection: null, model: null }),
  ev(2, { type: "checkpoint", sha: "abc123", kind: "agent", message: "Make a deck", files }),
];
const finished = ev(3, { type: "turn_finished", status: "completed", error: null, usage: null, session_id: "s" });

const render = (turn: Turn, sizes: Map<string, number> | null) =>
  renderToStaticMarkup(
    <FileCards
      turn={turn}
      files={{ sizes, complete: true }}
      onOpen={() => {}}
      onOpenVersion={() => {}}
      fileHref={(path) => `http://p/__studio/files/${path}`}
    />,
  );

describe("file cards in the chat", () => {
  it("are offered only once the request has ended", () => {
    expect(threadHasCards(reduceEvents(initialThread, events))).toBe(false);
    expect(threadHasCards(reduceEvents(initialThread, [...events, finished]))).toBe(true);
  });

  it("open a file that is there, and offer the version for one that is gone", () => {
    const turn = lastTurn(reduceEvents(initialThread, [...events, finished]))!;
    const out = render(turn, new Map([["files/deck.pptx", 2048]]));
    expect(out).toContain('aria-label="Open deck.pptx in Files. PowerPoint deck · 2.0 KB · new"');
    expect(out).toContain("PDF · no longer in the project");
    expect(out).toContain("Show version");
    expect(out).not.toContain("index.html");
  });

  it("do not call a file gone before the listing has come", () => {
    const turn = lastTurn(reduceEvents(initialThread, [...events, finished]))!;
    const out = render(turn, null);
    expect(out).toContain("PDF · changed");
    expect(out).not.toContain("no longer");
  });
});
