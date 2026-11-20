import { describe, expect, it } from "vitest";
import { CHAT_MIN, formatChatSize, parseChatSize, resized, sizeToggle } from "./chatSize";

const start = { x: 1000, y: 300, w: 420, h: 500 };
const room = { w: 1200, h: 800 };

describe("resizing the desktop chat panel", () => {
  it("grows to the left and upward, since it sits at the bottom right", () => {
    expect(resized(start, { x: 900, y: 300 }, "left", room, null)).toEqual({ w: 520, h: null });
    expect(resized(start, { x: 1000, y: 200 }, "top", room, null)).toEqual({ w: 420, h: 600 });
    expect(resized(start, { x: 950, y: 250 }, "corner", room, null)).toEqual({ w: 470, h: 550 });
    expect(resized(start, { x: 1100, y: 400 }, "corner", room, null)).toEqual({ w: CHAT_MIN.w, h: 400 });
  });

  it("stays between its smallest size and the room it has", () => {
    expect(resized(start, { x: 2000, y: 2000 }, "corner", room, null)).toEqual({ w: CHAT_MIN.w, h: CHAT_MIN.h });
    expect(resized(start, { x: -5000, y: -5000 }, "corner", room, null)).toEqual({ w: 1200, h: 800 });
    // A window smaller than the smallest size: the smallest size, never less.
    expect(resized(start, { x: 0, y: 0 }, "corner", { w: 200, h: 200 }, null)).toEqual({ w: CHAT_MIN.w, h: CHAT_MIN.h });
  });

  it("keeps the side that is not being dragged", () => {
    expect(resized(start, { x: 900, y: 0 }, "left", room, { w: 420, h: 640 })).toEqual({ w: 520, h: 640 });
    expect(resized(start, { x: 0, y: 200 }, "top", room, { w: 500, h: null })).toEqual({ w: 500, h: 600 });
  });

  it("is remembered as text and read back only when it makes sense", () => {
    expect(formatChatSize({ w: 520, h: 640 })).toBe("520x640");
    expect(formatChatSize({ w: 520, h: null })).toBe("520");
    expect(formatChatSize(null)).toBeNull();
    expect(parseChatSize("520x640")).toEqual({ w: 520, h: 640 });
    expect(parseChatSize("520")).toEqual({ w: 520, h: null });
    for (const bad of [null, "", "wide", "20x20", "520x", "100x900", "520x100", "99999x500"]) expect(parseChatSize(bad)).toBeNull();
  });

  it("names the header button by what it does next", () => {
    expect(sizeToggle(false)).toEqual({ label: "Make the chat larger", icon: "expand" });
    expect(sizeToggle(true)).toEqual({ label: "Make the chat smaller", icon: "shrink" });
  });
});
