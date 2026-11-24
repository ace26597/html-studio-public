import { describe, expect, it } from "vitest";
import { BAR, KEEP, MAX_ZOOM, PAD, READABLE_START, clampView, fitScale, fitView, startView, wheelFactor, zoomAt } from "./zoom";

const desktop = { w: 1440, h: 900 };
const phone = { w: 390, h: 800 };
const diagram = { w: 1250, h: 752 };

describe("a picture opened large", () => {
  it("fits the window and never goes above its own size", () => {
    expect(fitScale(diagram, desktop)).toBe(1);
    expect(fitScale({ w: 300, h: 200 }, desktop)).toBe(1);
    expect(fitScale(diagram, phone)).toBeCloseTo((390 - 2 * PAD) / 1250, 5);
    expect(fitScale({ w: 400, h: 4000 }, desktop)).toBeCloseTo((900 - PAD - BAR) / 4000, 5);
    expect(fitScale({ w: 0, h: 0 }, desktop)).toBe(1);
  });

  it("centres a fitted picture in the room above the buttons", () => {
    const view = fitView(diagram, desktop);
    expect(view).toEqual({ k: 1, x: 95, y: 36 });
  });

  it("opens fitted on a desktop, and readable from the top left on a phone", () => {
    expect(startView(diagram, desktop)).toEqual(fitView(diagram, desktop));
    expect(startView(diagram, phone)).toEqual({ k: READABLE_START, x: PAD, y: PAD });
    // A picture that fits a phone at 60% or more is simply fitted.
    const small = { w: 500, h: 300 };
    expect(startView(small, phone)).toEqual(fitView(small, phone));
  });

  it("zooms about the point under the pointer", () => {
    const from = { k: 1, x: 100, y: 50 };
    const to = zoomAt(from, 2, 400, 300, diagram, desktop);
    expect(to.k).toBe(2);
    // The picture's point that was under (400, 300) still is.
    expect((400 - to.x) / to.k).toBeCloseTo((400 - from.x) / from.k, 5);
    expect((300 - to.y) / to.k).toBeCloseTo((300 - from.y) / from.k, 5);
  });

  it("stops at four times its size and at the smaller of fitted and readable", () => {
    const start = startView(diagram, phone);
    expect(zoomAt(start, 99, 100, 100, diagram, phone).k).toBe(MAX_ZOOM);
    expect(zoomAt(start, 0.001, 100, 100, diagram, phone).k).toBeCloseTo(fitScale(diagram, phone), 5);
    expect(zoomAt({ k: 1, x: 0, y: 0 }, 0.001, 0, 0, diagram, desktop).k).toBe(READABLE_START);
  });

  it("cannot be dragged out of sight", () => {
    const lost = clampView({ k: 1, x: 99999, y: -99999 }, diagram, desktop);
    expect(lost.x).toBe(desktop.w - KEEP);
    expect(lost.y).toBe(KEEP - diagram.h);
    const other = clampView({ k: 1, x: -99999, y: 99999 }, diagram, desktop);
    expect(other.x).toBe(KEEP - diagram.w);
    expect(other.y).toBe(desktop.h - KEEP);
    expect(clampView({ k: 1, x: 10, y: 10 }, diagram, desktop)).toEqual({ k: 1, x: 10, y: 10 });
  });

  it("turns a wheel step into a gentle factor, and a trackpad pinch into a stronger one", () => {
    expect(wheelFactor(-100, false)).toBeCloseTo(Math.exp(0.2), 5);
    expect(wheelFactor(100, false)).toBeCloseTo(Math.exp(-0.2), 5);
    expect(wheelFactor(-10, true)).toBeCloseTo(Math.exp(0.1), 5);
    // One huge step (a free-spinning wheel) is held back.
    expect(wheelFactor(-5000, false)).toBeCloseTo(Math.exp(0.48), 5);
  });
});
