import { afterEach, describe, expect, it, vi } from "vitest";
import { holdUnload, mayLeave, setLeaveGuard } from "./router";

describe("leaving a page that holds unsaved edits", () => {
  afterEach(() => setLeaveGuard(null));

  it("asks the page's question for any move to another address", () => {
    const ask = vi.fn(() => false);
    setLeaveGuard(ask);
    expect(mayLeave("/p/p_1", "/")).toBe(false);
    expect(mayLeave("/p/p_1", "/library#data-sources")).toBe(false);
    expect(mayLeave("/p/p_1", "/setup")).toBe(false);
    expect(ask).toHaveBeenCalledTimes(3);
    ask.mockReturnValue(true);
    expect(mayLeave("/p/p_1", "/")).toBe(true);
  });

  it("does not ask when staying on the same page, or when nothing is held", () => {
    const ask = vi.fn(() => false);
    setLeaveGuard(ask);
    expect(mayLeave("/p/p_1", "/p/p_1#files")).toBe(true);
    expect(ask).not.toHaveBeenCalled();
    setLeaveGuard(null);
    expect(mayLeave("/p/p_1", "/")).toBe(true);
  });

  it("a page that goes away takes only its own question with it", () => {
    const first = () => false;
    const clear = setLeaveGuard(first);
    const second = () => true;
    setLeaveGuard(second);
    clear(); // the first page's late clean-up must not drop the second page's question
    expect(mayLeave("/p/a", "/")).toBe(true);
    setLeaveGuard(first);
    expect(mayLeave("/p/a", "/")).toBe(false);
  });

  it("closing or reloading the tab is held only while there are edits", () => {
    const event = { preventDefault: vi.fn(), returnValue: undefined as unknown };
    holdUnload(event, false);
    expect(event.preventDefault).not.toHaveBeenCalled();
    holdUnload(event, true);
    expect(event.preventDefault).toHaveBeenCalledTimes(1);
    expect(event.returnValue).toBe("");
  });
});
