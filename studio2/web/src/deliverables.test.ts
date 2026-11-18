import { describe, expect, it } from "vitest";
import { cardMeta, DELIVERABLE_CAP, deliverablesOf, fileNow, isDeliverable, kindLabel, moreLine } from "./deliverables";
import { formatBytes } from "./format";

const added = (path: string) => ({ path, status: "added" });
const modified = (path: string) => ({ path, status: "modified" });
const agent = (...files: { path: string; status: string }[]) => ({ kind: "agent", files });

describe("what gets a card", () => {
  it("takes anything added or changed under files/, but not code or working folders", () => {
    expect(isDeliverable(added("files/deck.pptx"))).toBe(true);
    expect(isDeliverable(modified("files/notes.md"))).toBe(true);
    expect(isDeliverable(added("files/report/summary.txt"))).toBe(true);
    expect(isDeliverable(added("files/build.py"))).toBe(false);
    expect(isDeliverable(added("files/deck.pptx.preview/index.html"))).toBe(false);
    expect(isDeliverable(added("files/deck.pptx.preview/slide-1.png"))).toBe(false);
    expect(isDeliverable(added("files/.cache/x.csv"))).toBe(false);
    expect(isDeliverable(added("files/node_modules/a/b.pdf"))).toBe(false);
    expect(isDeliverable(added("files/img/chart.png"))).toBe(false);
    expect(isDeliverable(added("files/diagrams/flow.svg"))).toBe(false);
    expect(isDeliverable(added("files/diagrams/flow.mmd"))).toBe(false);
  });

  it("takes Office, PDF, pictures, sound, video and tables added elsewhere", () => {
    for (const path of ["report.docx", "out/sheet.xlsx", "brochure.pdf", "media/launch.mp4", "media/voice.mp3", "data/rows.csv", "img/hero.png"]) {
      expect(isDeliverable(added(path)), path).toBe(true);
    }
  });

  it("does not take a changed file elsewhere, an SVG elsewhere, or the page's own code", () => {
    expect(isDeliverable(modified("img/hero.png"))).toBe(false);
    expect(isDeliverable(added("img/logo.svg"))).toBe(false);
    expect(isDeliverable(added("files/logo.svg"))).toBe(true);
    for (const path of ["index.html", "styles.css", "app.js", "src/main.tsx", "notes.md", "data.json"]) {
      expect(isDeliverable(added(path)), path).toBe(false);
    }
  });

  it("never takes the studio's own and working folders, hidden folders, or deleted files", () => {
    for (const top of ["sources", "checks", "attachments", "tools", "research", "state", "template", "notes"]) {
      expect(isDeliverable(added(`${top}/thing.pdf`)), top).toBe(false);
    }
    expect(isDeliverable(added(".studio/x.pdf"))).toBe(false);
    expect(isDeliverable(added("node_modules/pkg/doc.pdf"))).toBe(false);
    expect(isDeliverable({ path: "files/deck.pptx", status: "deleted" })).toBe(false);
  });

  it("leaves out a video's poster picture", () => {
    const { shown } = deliverablesOf([agent(added("media/launch.mp4"), added("media/launch.poster.jpg"), added("media/old.mp4"), added("media/old.jpg"), added("media/cat.jpg"))]);
    expect(shown.map((f) => f.path)).toEqual(["media/launch.mp4", "media/old.mp4", "media/cat.jpg"]);
  });

  it("puts files/ first, skips the message's own attachments and other kinds of version", () => {
    const { shown } = deliverablesOf(
      [{ kind: "upload", files: [added("files/up.pdf")] }, agent(added("media/a.mp4"), modified("files/deck.pptx"), added("data/in.csv"))],
      ["data/in.csv"],
    );
    expect(shown.map((f) => f.path)).toEqual(["files/deck.pptx", "media/a.mp4"]);
  });

  it("caps the cards and counts the rest", () => {
    const many = Array.from({ length: DELIVERABLE_CAP + 3 }, (_, i) => added(`files/chart-${i}.png`));
    const { shown, more } = deliverablesOf([agent(...many)]);
    expect(shown).toHaveLength(8);
    expect(more).toBe(3);
    expect(moreLine(more)).toBe("and 3 more in Files");
    expect(moreLine(0)).toBeNull();
    expect(deliverablesOf([agent(added("index.html"))]).shown).toEqual([]);
  });
});

describe("the card's words", () => {
  it("names each kind plainly", () => {
    expect(kindLabel("files/deck.pptx")).toBe("PowerPoint deck");
    expect(kindLabel("files/model.xlsx")).toBe("Excel workbook");
    expect(kindLabel("files/letter.docx")).toBe("Word document");
    expect(kindLabel("files/brochure.PDF")).toBe("PDF");
    expect(kindLabel("img/hero.webp")).toBe("Picture");
    expect(kindLabel("media/launch.mp4")).toBe("Video");
    expect(kindLabel("media/voice.mp3")).toBe("Sound");
    expect(kindLabel("files/rows.csv")).toBe("Table (CSV)");
    expect(kindLabel("files/rows.tsv")).toBe("Table (TSV)");
    expect(kindLabel("files/notes.md")).toBe("Text");
    expect(kindLabel("files/blob.bin")).toBe("File");
  });

  it("gives the size now, or says the file is gone", () => {
    const listing = new Map([["files/deck.pptx", 48 * 1024]]);
    const here = fileNow(listing, "files/deck.pptx", true);
    expect(cardMeta(added("files/deck.pptx"), here, formatBytes)).toBe("PowerPoint deck · 48 KB · new");
    expect(cardMeta(modified("files/deck.pptx"), here, formatBytes)).toBe("PowerPoint deck · 48 KB · changed");
    const gone = fileNow(listing, "files/old.pdf", true);
    expect(gone).toEqual({ state: "gone" });
    expect(cardMeta(added("files/old.pdf"), gone, formatBytes)).toBe("PDF · no longer in the project");
    // A listing cut at its cap, or not read yet, cannot say a file is gone.
    expect(fileNow(listing, "files/old.pdf", false)).toEqual({ state: "unknown" });
    expect(cardMeta(added("files/old.pdf"), fileNow(null, "files/old.pdf", true), formatBytes)).toBe("PDF · new");
  });
});
