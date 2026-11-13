import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { ExportWays } from "./ExportDialog";
import { exportWays, type ExportOptions } from "./exportInfo";

const page: ExportOptions = {
  name: "Sales", app: false, pages: ["index.html", "about.html"], page: "index.html",
  html: { available: true, reason: null, bytes: 413_973, inlined: 1, embedded: { files: 4, bytes: 220_932 },
          external: ["cdn.jsdelivr.net"], notes: ["It links to 1 other page of the project (about.html), which are not in the file."] },
  folder: { available: true, reason: null, files: 10, bytes: 348_653, run: null, tools: [], needs: [] },
};
const app: ExportOptions = {
  ...page, app: true,
  html: { ...page.html, available: false, reason: "This project has its own back end.", bytes: 0, inlined: 0,
          embedded: { files: 0, bytes: 0 }, external: [], notes: [] },
  folder: { available: true, reason: null, files: 8, bytes: 21_770, run: "uv run uvicorn server:app --port $PORT",
            tools: ["uv (https://docs.astral.sh/uv/)"], needs: ["the studio's model address (STUDIO_MODEL_URL)"] },
};

describe("export", () => {
  it("says what one file holds and what it still needs", () => {
    const [html, folder, everything] = exportWays(page);
    expect(html.facts).toBe("404 KB · holds 1 style, script or picture and 4 data files (216 KB)");
    expect(html.notes[0]).toBe("Needs the internet for what it still loads from cdn.jsdelivr.net.");
    expect(html.notes[1]).toContain("about.html");
    expect(folder.title).toBe("A folder to host");
    expect(folder.facts).toBe("10 files · 340 KB before zipping");
    expect(folder.notes[0]).toContain("static host");
    expect(everything.what).toContain("not for sharing");
    const alone = exportWays({ ...page, html: { ...page.html, external: [], notes: [] } })[0];
    expect(alone.notes).toEqual(["Needs no internet: nothing is loaded from elsewhere."]);
  });

  it("gives the reason when a way is not available, and what an app needs to run", () => {
    const [html, folder] = exportWays(app);
    expect(html.available).toBe(false);
    expect(html.reason).toContain("back end");
    expect(html.facts).toBe("");
    expect(html.notes).toEqual([]);
    expect(folder.title).toBe("A folder to run");
    expect(folder.notes[0]).toBe("To run it you need uv (https://docs.astral.sh/uv/).");
    expect(folder.notes[1]).toContain("STUDIO_MODEL_URL");
    expect(folder.notes[2]).toContain("starts empty");
  });

  it("draws a download link only for what is available", () => {
    const html = renderToStaticMarkup(<ExportWays ways={exportWays(app)} href={(key) => `/get/${key}`} />);
    expect(html.match(/<a /g)?.length).toBe(2);
    expect(html).not.toContain('href="/get/html"');
    expect(html).toContain('href="/get/folder"');
    expect(html).toContain('href="/get/everything"');
    expect(html).toContain("Not available: This project has its own back end.");
    expect(html.match(/download=""/g)?.length).toBe(2);
  });
});
