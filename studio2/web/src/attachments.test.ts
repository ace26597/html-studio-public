import { describe, expect, it } from "vitest";
import { planAttachments, type AttachmentState } from "./attachments";

const item = (id: string, status: AttachmentState["status"], path: string | null = null): AttachmentState => ({
  id,
  name: `${id}.png`,
  status,
  path,
});

describe("planAttachments", () => {
  it("sends uploaded paths in order", () => {
    const plan = planAttachments([item("a", "done", "attachments/a.png"), item("b", "done", "attachments/b.csv")]);
    expect(plan).toEqual({ paths: ["attachments/a.png", "attachments/b.csv"], ids: ["a", "b"], blocked: null });
  });

  it("waits for uploads still running", () => {
    expect(planAttachments([item("a", "done", "x"), item("b", "uploading")]).blocked).toBe("uploading");
  });

  it("blocks on a failed upload until it is removed, even while others upload", () => {
    expect(planAttachments([item("a", "error"), item("b", "uploading")]).blocked).toBe("failed");
    expect(planAttachments([item("b", "done", "x")]).blocked).toBeNull();
  });

  it("allows a message with no attachments", () => {
    expect(planAttachments([])).toEqual({ paths: [], ids: [], blocked: null });
  });
});
