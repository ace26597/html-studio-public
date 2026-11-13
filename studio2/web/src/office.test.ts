import { describe, expect, it } from "vitest";
import { findPreview, isPreviewDir, officeKind, orderTreeItems, previewIndexPath } from "./office";

describe("office files", () => {
  it("knows Office and PDF files by extension, any case", () => {
    expect(officeKind("files/deck.pptx")).toBe("presentation");
    expect(officeKind("old.PPT")).toBe("presentation");
    expect(officeKind("files/report.docx")).toBe("document");
    expect(officeKind("letter.rtf")).toBe("document");
    expect(officeKind("model.xlsx")).toBe("workbook");
    expect(officeKind("model.xls")).toBe("workbook");
    expect(officeKind("paper.pdf")).toBe("pdf");
    expect(officeKind("index.html")).toBeNull();
    expect(officeKind("files/deck.pptx.preview/index.html")).toBeNull();
    expect(officeKind("notes.pptx.txt")).toBeNull();
  });

  it("finds the rendered preview next to the file", () => {
    expect(previewIndexPath("files/deck.pptx")).toBe("files/deck.pptx.preview/index.html");
    const listing = [{ path: "files/deck.pptx" }, { path: "files/deck.pptx.preview/index.html" }, { path: "files/memo.docx" }];
    expect(findPreview("files/deck.pptx", listing)).toBe("files/deck.pptx.preview/index.html");
    expect(findPreview("files/memo.docx", listing)).toBeNull();
  });

  it("recognises generated preview folders", () => {
    expect(isPreviewDir("deck.pptx.preview")).toBe(true);
    expect(isPreviewDir(".preview")).toBe(false);
    expect(isPreviewDir("previews")).toBe(false);
  });
});

describe("orderTreeItems", () => {
  const dir = (name: string) => ({ name });
  const file = (path: string) => ({ path });

  it("puts each preview folder right after its file, after ordinary folders", () => {
    const items = orderTreeItems(
      [dir("a.pptx.preview"), dir("data"), dir("orphan.docx.preview")],
      [file("files/a.pptx"), file("files/b.docx")],
    );
    expect(items.map((item) => (item.type === "dir" ? `dir:${item.dir.name}${item.preview ? "*" : ""}` : `file:${item.file.path}`))).toEqual([
      "dir:data",
      "file:files/a.pptx",
      "dir:a.pptx.preview*",
      "file:files/b.docx",
      "dir:orphan.docx.preview*",
    ]);
  });

  it("leaves a folder without previews as before: folders, then files", () => {
    const items = orderTreeItems([dir("x"), dir("y")], [file("a.txt")]);
    expect(items.map((item) => item.type)).toEqual(["dir", "dir", "file"]);
  });
});
