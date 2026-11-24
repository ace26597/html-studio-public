import { describe, expect, it } from "vitest";
import { MAX_PICTURE_BYTES, readEnlarge } from "./enlarge";

const png = new Blob([new Uint8Array([1, 2, 3])], { type: "image/png" });

describe("the bridge's enlarge message", () => {
  it("takes a picture's bytes with its size, text and the colour behind it", () => {
    const picture = readEnlarge({ type: "enlarge", blob: png, alt: "One turn", background: "rgb(17, 17, 19)", width: 1250, height: 752 });
    expect(picture).toEqual({ blob: png, type: "image/png", url: null, alt: "One turn", background: "rgb(17, 17, 19)", width: 1250, height: 752 });
  });

  it("takes an http(s) address when there are no bytes, and nothing else as an address", () => {
    expect(readEnlarge({ type: "enlarge", url: "https://example.org/a.png" })?.url).toBe("https://example.org/a.png");
    expect(readEnlarge({ type: "enlarge", url: "javascript:alert(1)" })).toBeNull();
    expect(readEnlarge({ type: "enlarge", url: "data:text/html,<script>1</script>" })).toBeNull();
    expect(readEnlarge({ type: "enlarge", url: "/files/a.png" })).toBeNull();
    expect(readEnlarge({ type: "enlarge" })).toBeNull();
  });

  it("prefers the bytes when both are given", () => {
    const picture = readEnlarge({ type: "enlarge", blob: png, url: "https://example.org/a.png" });
    expect(picture?.blob).toBe(png);
    expect(picture?.url).toBeNull();
  });

  it("takes only kinds of picture, whatever else the file's type carries", () => {
    const of = (type: string) => readEnlarge({ type: "enlarge", blob: new Blob(["x"], { type }) })?.type ?? null;
    expect(of("image/svg+xml")).toBe("image/svg+xml");
    expect(of("image/svg+xml; charset=utf-8")).toBe("image/svg+xml");
    expect(of("image/jpeg")).toBe("image/jpeg");
    expect(of("text/html")).toBeNull();
    expect(of("application/xhtml+xml")).toBeNull();
    expect(of("image/svg+xml-not")).toBeNull();
    expect(of("")).toBeNull();
  });

  it("refuses an empty file, one over 25 MB, and anything that is not a file", () => {
    expect(readEnlarge({ type: "enlarge", blob: new Blob([], { type: "image/png" }) })).toBeNull();
    const huge = { size: MAX_PICTURE_BYTES + 1, type: "image/png" };
    Object.setPrototypeOf(huge, Blob.prototype);
    expect(readEnlarge({ type: "enlarge", blob: huge })).toBeNull();
    expect(readEnlarge({ type: "enlarge", blob: "<svg/>" })).toBeNull();
    expect(readEnlarge({ type: "ready", blob: png })).toBeNull();
  });

  it("keeps a colour only in the form a browser reports", () => {
    const colour = (background: unknown) => readEnlarge({ type: "enlarge", blob: png, background })?.background;
    expect(colour("rgb(255, 255, 255)")).toBe("rgb(255, 255, 255)");
    expect(colour("rgba(0, 0, 0, 0.8)")).toBe("rgba(0, 0, 0, 0.8)");
    expect(colour("rgb(10 20 30 / 50%)")).toBe("rgb(10 20 30 / 50%)");
    expect(colour("url(https://example.org/x)")).toBeNull();
    expect(colour("red; position: fixed")).toBeNull();
    expect(colour("rgb(0,0,0);background:url(x)")).toBeNull();
    expect(colour(7)).toBeNull();
  });

  it("uses a size only when both sides are sensible numbers", () => {
    const size = (width: unknown, height: unknown) => {
      const picture = readEnlarge({ type: "enlarge", blob: png, width, height });
      return [picture?.width, picture?.height];
    };
    expect(size(800, 600)).toEqual([800, 600]);
    expect(size(800, 0)).toEqual([null, null]);
    expect(size("800", 600)).toEqual([null, null]);
    expect(size(Infinity, 600)).toEqual([null, null]);
    expect(size(800, 99999)).toEqual([null, null]);
  });

  it("cuts a long text alternative", () => {
    expect(readEnlarge({ type: "enlarge", blob: png, alt: "a".repeat(900) })?.alt.length).toBe(300);
    expect(readEnlarge({ type: "enlarge", blob: png, alt: 5 })?.alt).toBe("");
  });
});
