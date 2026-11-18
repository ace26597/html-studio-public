import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { FileShown, shownAs, svgImageSrc } from "./FileShown";

const html = (path: string, text: string) => renderToStaticMarkup(<FileShown path={path} text={text} as={shownAs(path)!} />);

describe("files shown as what they are", () => {
  it("knows which files have a shown view", () => {
    expect(shownAs("README.md")).toBe("markdown");
    expect(shownAs("files/rows.CSV")).toBe("table");
    expect(shownAs("files/rows.tsv")).toBe("table");
    expect(shownAs("img/logo.svg")).toBe("picture");
    expect(shownAs("index.html")).toBeNull();
  });

  it("shows an SVG only as an image, so its script and handlers never run in the studio", () => {
    const svg = '<svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)"><script>alert(2)</script><circle r="4"/></svg>';
    const out = html("img/logo.svg", svg);
    expect(out).toMatch(/^<div class="viewer-image"><img src="data:image\/svg\+xml;charset=utf-8,[^"]*" alt="img\/logo.svg"\/><\/div>$/);
    // Nothing of the file becomes markup in the page: no <svg>, <script> or onload attribute.
    expect(out).not.toContain("<svg");
    expect(out).not.toContain("<script");
    expect(out).not.toMatch(/\sonload=/);
    expect(decodeURIComponent(svgImageSrc(svg).split(",").slice(1).join(","))).toBe(svg);
  });

  it("renders Markdown without running HTML from the file", () => {
    const out = html("notes.md", '# Title\n\n<script>alert(1)</script>\n\n<img src=x onerror="alert(2)"> **bold**');
    expect(out).toContain("<h3>Title</h3>");
    expect(out).toContain("<strong>bold</strong>");
    expect(out).not.toContain("<script");
    expect(out).not.toContain("<img");
    expect(out).toContain("&lt;script&gt;");
  });

  it("shows a CSV as a table with its header and right-aligned numbers, and cells as text", () => {
    const out = html("files/rows.csv", 'name,total\n"<b>Jo</b>",12\n');
    expect(out).toContain('<th scope="col">name</th><th class="is-num" scope="col">total</th>');
    expect(out).toContain('<td>&lt;b&gt;Jo&lt;/b&gt;</td><td class="is-num">12</td>');
  });
});
