import { describe, expect, it } from "vitest";
import { chatStartsOpen, isCompactWidth, keyboardInset } from "./media";

describe("layout", () => {
  it("uses the compact layout on phones and iPad portrait, desktop from iPad landscape up", () => {
    for (const width of [360, 390, 430, 834, 1089]) expect(isCompactWidth(width)).toBe(true);
    for (const width of [1090, 1194, 1440, 2560]) expect(isCompactWidth(width)).toBe(false);
  });
});

describe("keyboardInset", () => {
  it("measures the keyboard from the visual viewport", () => {
    expect(keyboardInset(844, 508, 0)).toBe(336);
    expect(keyboardInset(844, 508, 20)).toBe(316);
  });

  it("ignores small differences from browser chrome", () => {
    expect(keyboardInset(844, 800, 0)).toBe(0);
    expect(keyboardInset(844, 844, 0)).toBe(0);
  });
});

describe("whether a project's chat starts open", () => {
  it("follows what was remembered for the project", () => {
    expect(chatStartsOpen("1", 390)).toBe(true);
    expect(chatStartsOpen("0", 1440)).toBe(false);
  });

  it("with nothing remembered, opens on a desktop and stays a button on a phone or iPad", () => {
    expect(chatStartsOpen(null, 1440)).toBe(true);
    expect(chatStartsOpen(null, 1194)).toBe(true);
    expect(chatStartsOpen(null, 820)).toBe(false);
    expect(chatStartsOpen(null, 390)).toBe(false);
  });
});
