import { describe, expect, it, vi } from "vitest";
import projectSource from "./Project.tsx?raw";
import { SIGNED_IN_LABEL, signedInEntries, signedInLooks } from "./signedInLook";

describe("Let the agent use my sign-in", () => {
  it("follows the server's value, defaults off for a brought-in folder, and is never on for a shared studio", () => {
    expect(signedInLooks({ signed_in_looks: true }, false)).toBe(true);
    expect(signedInLooks({ signed_in_looks: false }, false)).toBe(false);
    expect(signedInLooks({}, false)).toBe(true);
    expect(signedInLooks({ attach: { mode: "copy" } as never }, false)).toBe(false);
    expect(signedInLooks({ signed_in_looks: true }, true)).toBe(false);
    expect(signedInLooks(null, false)).toBe(false);
  });

  it("says what it means, is a tick, and switches the other way", () => {
    const onChange = vi.fn();
    const [section, tick] = signedInEntries({ signed_in_looks: true }, false, onChange);
    expect(section).toMatchObject({ type: "section" });
    expect("note" in section && section.note).toContain("click and type in the app as you");
    expect(tick).toMatchObject({ label: SIGNED_IN_LABEL, checked: true, toggle: true, disabled: false });
    if ("onSelect" in tick) tick.onSelect();
    expect(onChange).toHaveBeenCalledWith(false);
  });

  it("cannot be switched on a shared studio", () => {
    const [section, tick] = signedInEntries({ signed_in_looks: true }, true, () => {});
    expect(tick).toMatchObject({ checked: false, disabled: true });
    expect("note" in section && section.note).toContain("shared studio");
  });

  it("is in both of the project's menus", () => {
    expect(projectSource.match(/\.\.\.signedInEntries\(project, Boolean\(health\.shared\), setSignedInLooks\)/g)?.length).toBe(2);
  });
});
