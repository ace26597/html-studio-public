import { describe, expect, it } from "vitest";
import {
  groupTemplates,
  idleImport,
  importReducer,
  saveChoices,
  saveTemplateBody,
  templateKindLabel,
  templateOrigin,
  templatePreviewSrc,
  templateStartLine,
} from "./templates";
import type { Template } from "./types";

// Shapes for the rules under test only.
const builtIn: Template = { id: "blank", label: "Blank page", description: "", app: false };
const appBuiltIn: Template = { id: "fastapi-app", label: "FastAPI app", description: "", app: true, source: "studio", kind: "app", mode: "copy" };
const pattern: Template = {
  id: "tpl_1", label: "Report look", description: "", app: false, source: "yours", kind: "page", mode: "pattern",
  created_at: "2026-11-11T10:00:00Z", from_project: "Coffee market", preview: "/api/templates/tpl_1/preview",
};
const appCopy: Template = { id: "tpl_2", label: "Tracker", description: "", app: true, source: "yours", kind: "app", mode: "copy", imported: true, preview: null };

describe("listing templates", () => {
  it("groups built-in and saved templates, keeping order", () => {
    expect(groupTemplates([builtIn, pattern, appBuiltIn, appCopy])).toEqual({ builtIn: [builtIn, appBuiltIn], yours: [pattern, appCopy] });
  });

  it("names kinds plainly", () => {
    expect(templateKindLabel(builtIn)).toBe("Page");
    expect(templateKindLabel(appBuiltIn)).toBe("App");
    expect(templateKindLabel({ kind: "deck", app: false })).toBe("PowerPoint deck");
    expect(templateKindLabel({ kind: "workbook", app: false })).toBe("Excel workbook");
    expect(templateKindLabel({ kind: "document", app: false })).toBe("Word document");
  });

  it("says where a saved template came from", () => {
    expect(templateOrigin(pattern)).toBe("from Coffee market");
    expect(templateOrigin(appCopy)).toBe("imported");
    expect(templateOrigin(builtIn)).toBeNull();
  });

  it("busts the preview cache with created_at", () => {
    expect(templatePreviewSrc(pattern)).toBe("/api/templates/tpl_1/preview?v=2026-11-11T10%3A00%3A00Z");
    expect(templatePreviewSrc(appCopy)).toBeNull();
  });

  it("explains what starting from a saved template does", () => {
    expect(templateStartLine(pattern)).toContain("Starts with an empty page");
    expect(templateStartLine(appCopy)).toBe("Starts as a copy of this app.");
    expect(templateStartLine(appBuiltIn)).toBeNull();
  });
});

describe("saving a template", () => {
  const files = [
    { path: "index.html" },
    { path: "files/deck.pptx" },
    { path: "files/deck.pptx.preview/index.html" },
    { path: "files/model.xlsx" },
    { path: "files/brief.docx" },
    { path: "files/old.ppt" },
    { path: "template/files/x.pptx" },
  ];

  it("defaults to the whole page, or the whole app when there is a back end", () => {
    expect(saveChoices([], false)[0]).toMatchObject({ value: "whole", label: "This whole page", kind: "page" });
    expect(saveChoices([], true)[0]).toMatchObject({ value: "whole", label: "This whole app", kind: "app" });
  });

  it("offers the project's .pptx, .xlsx and .docx files by name and kind", () => {
    expect(saveChoices(files, false).slice(1).map((choice) => [choice.value, choice.label, choice.kind])).toEqual([
      ["files/brief.docx", "brief.docx · Word document", "document"],
      ["files/deck.pptx", "deck.pptx · PowerPoint deck", "deck"],
      ["files/model.xlsx", "model.xlsx · Excel workbook", "workbook"],
    ]);
  });

  it("sends the kind for the whole project and the file for a file", () => {
    const [whole, docx] = saveChoices(files, true);
    expect(saveTemplateBody(" Tracker ", "", whole)).toEqual({ name: "Tracker", kind: "app" });
    expect(saveTemplateBody("Brief", " keep the headings ", docx)).toEqual({
      name: "Brief",
      description: "keep the headings",
      file: "files/brief.docx",
    });
  });
});

describe("importReducer", () => {
  const file = new File(["x"], "t.zip");

  it("uploads, asks to confirm a template with code, then uploads again confirmed", () => {
    let state = importReducer(idleImport, { type: "pick", file });
    expect(state).toEqual({ phase: "uploading", file, confirmed: false });
    state = importReducer(state, { type: "needs-confirmation", message: "This template contains code." });
    expect(state).toEqual({ phase: "confirm", file, message: "This template contains code." });
    state = importReducer(state, { type: "confirm" });
    expect(state).toEqual({ phase: "uploading", file, confirmed: true });
    expect(importReducer(state, { type: "done" })).toBe(idleImport);
  });

  it("cancels at the question, and never loops on a second refusal", () => {
    const asking = importReducer(importReducer(idleImport, { type: "pick", file }), { type: "needs-confirmation", message: "m" });
    expect(importReducer(asking, { type: "cancel" })).toBe(idleImport);
    const confirmed = { phase: "uploading" as const, file, confirmed: true };
    expect(importReducer(confirmed, { type: "needs-confirmation", message: "m" })).toBe(idleImport);
  });

  it("ignores a second pick while busy", () => {
    const busy = importReducer(idleImport, { type: "pick", file });
    expect(importReducer(busy, { type: "pick", file: new File(["y"], "u.zip") })).toBe(busy);
  });
});
