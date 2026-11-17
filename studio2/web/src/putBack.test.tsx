import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { draftHolds, putBackDraft } from "./nextSteps";
import type { NoticeItem } from "./thread";
import { ThreadView, type ThreadActions } from "./ThreadView";

const actions = {
  onRetry: () => undefined,
  onOpenVersion: () => undefined,
  canRetry: true,
  onDecide: () => undefined,
  decisions: {},
  fileHref: () => null,
  onPutBack: () => undefined,
} as unknown as ThreadActions;

const note = (extra: Partial<NoticeItem> = {}): NoticeItem => ({
  type: "notice",
  key: "unqueued:5",
  level: "info",
  message: "Stop also removed a message that was waiting, so it was not sent: “make the heading red”",
  ts: "2026-11-17T09:00:00Z",
  restoreText: "make the heading red",
  ...extra,
});

const render = (item: NoticeItem, draft = "", used: string[] = []) =>
  renderToStaticMarkup(<ThreadView entries={[item]} actions={actions} putBack={{ draft, used: new Set(used) }} />);

describe("Put it back", () => {
  it("puts the text back once: a second tap does not add it again", () => {
    const once = putBackDraft("use these notes", "make the heading red");
    expect(once).toBe("use these notes\nmake the heading red");
    expect(putBackDraft(once, "make the heading red")).toBe(once);
    expect(putBackDraft("make the heading red\nuse these notes", "make the heading red")).toBe(
      "make the heading red\nuse these notes",
    );
    expect(draftHolds("make the heading red\nuse these notes", "make the heading red")).toBe(true);
    expect(draftHolds("", "make the heading red")).toBe(false);
  });

  it("offers the button only while it would do something", () => {
    expect(render(note())).toContain("Put it back in the message box");
    // Used already on this page, or the box holds that text now: no invitation to tap again.
    expect(render(note(), "", ["unqueued:5"])).not.toContain("<button");
    expect(render(note(), "make the heading red")).not.toContain("<button");
    expect(render(note(), "make the heading red")).toContain("It is in the message box.");
    // Sent again since (also seen after a reload, from the log): nothing left to offer.
    const resent = render(note({ resent: true }));
    expect(resent).not.toContain("<button");
    expect(resent).toContain("It was sent again later.");
  });
});
