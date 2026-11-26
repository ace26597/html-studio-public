import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import type { CheckpointItem } from "./thread";
import { ThreadView, type ThreadActions } from "./ThreadView";

const actions = {
  onRetry: () => undefined,
  onOpenVersion: () => undefined,
  canRetry: true,
  onDecide: () => undefined,
  decisions: {},
  fileHref: () => null,
} as unknown as ThreadActions;

const version = (extra: Partial<CheckpointItem> = {}): CheckpointItem => ({
  type: "checkpoint",
  key: "checkpoint:3",
  sha: "abc1234",
  kind: "agent",
  message: "Build the dashboard",
  files: [{ path: "index.html", status: "modified" }],
  ts: "2026-11-25T09:00:00Z",
  ...extra,
});

const render = (item: CheckpointItem) => renderToStaticMarkup(<ThreadView entries={[item]} actions={actions} />);

describe("the saved version line and files too large for versions", () => {
  it("says nothing extra when no file was left out", () => {
    const html = render(version());
    expect(html).toContain("Saved version · 1 file");
    expect(html).not.toContain("not in versions");
  });

  it("says so briefly, with the names and sizes on hover", () => {
    const html = render(version({ leftOut: { files: [{ path: "data/sales.csv", size: 150 * 1024 * 1024 }], count: 1 } }));
    expect(html).toContain("Saved version · 1 file · 1 large file not in versions");
    expect(html).toMatch(/title="[^"]*data\/sales\.csv \(150\.0 MB\)/);
  });
});
