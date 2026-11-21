import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import {
  INSTRUCTIONS_MAX,
  hasInstructions,
  instructionsButton,
  instructionsChanged,
  instructionsCount,
  instructionsEntries,
  instructionsKept,
  instructionsLength,
  instructionsNote,
  instructionsProblem,
  instructionsSavedMessage,
  tidyInstructions,
} from "./instructions";
import { InstructionsDialog } from "./InstructionsDialog";

describe("instructions for a project", () => {
  it("tidies the text the way the server stores it", () => {
    expect(tidyInstructions("  Always rupees.  \r\n\r\nEnd with sources.\t\n\n")).toBe("Always rupees.\n\nEnd with sources.");
    expect(tidyInstructions(" \n ")).toBe("");
  });

  it("counts characters as the server does", () => {
    expect(instructionsLength("  abc  ")).toBe(3);
    expect(instructionsLength("😀😀")).toBe(2);
    expect(instructionsCount("abc")).toEqual({ text: "3 / 4,000", over: false });
    expect(instructionsCount("x".repeat(INSTRUCTIONS_MAX + 1))).toEqual({ text: "4,001 / 4,000", over: true });
  });

  it("refuses only text over the limit", () => {
    expect(instructionsProblem("x".repeat(INSTRUCTIONS_MAX))).toBeNull();
    expect(instructionsProblem(`${"x".repeat(INSTRUCTIONS_MAX)}   \n`)).toBeNull();
    expect(instructionsProblem("x".repeat(INSTRUCTIONS_MAX + 1))).toBe(
      "Instructions can be 4,000 characters at most; this is 4,001.",
    );
  });

  it("knows when there is something to save", () => {
    expect(instructionsChanged("Always rupees.", "Always rupees.  \n")).toBe(false);
    expect(instructionsChanged(undefined, "")).toBe(false);
    expect(instructionsChanged("Always rupees.", "")).toBe(true);
    expect(instructionsChanged("", "Plain tone.")).toBe(true);
  });

  it("marks the menu row and the button when the project has some", () => {
    expect(hasInstructions({ instructions: " \n" })).toBe(false);
    expect(hasInstructions(null)).toBe(false);
    const [off] = instructionsEntries({ instructions: "" }, () => {});
    const [on] = instructionsEntries({ instructions: "Plain tone." }, () => {});
    expect(off).toMatchObject({ label: "Instructions…", icon: "note" });
    expect("hint" in off ? off.hint : null).toBeUndefined();
    expect("hint" in on ? on.hint : null).toBe("On");
    expect(instructionsButton({ instructions: "Plain tone." })).toMatchObject({ on: true, label: "Instructions (on)" });
    expect(instructionsButton(null).on).toBe(false);
    // The text for every project marks the button too, and the tip says which is set.
    expect(instructionsButton({ instructions: "" }, "We work in India.")).toMatchObject({ on: true });
    expect(instructionsButton({ instructions: "" }, "We work in India.").tip).toContain("every project");
    expect(instructionsButton({ instructions: "Plain tone." }, "We work in India.").tip).toContain("for every project and for this one");
    expect(instructionsButton({ instructions: "" }, "   ").on).toBe(false);
    expect(instructionsButton({ instructions: "" }, null).on).toBe(false);
  });

  it("is not offered by a server that does not keep them", () => {
    expect(instructionsKept({ instructions: "" })).toBe(true);
    expect(instructionsKept({})).toBe(false);
    expect(instructionsKept(null)).toBe(false);
    expect(instructionsEntries({}, () => {})).toEqual([]);
    expect(instructionsEntries(null, () => {})).toEqual([]);
  });

  it("says when a change applies", () => {
    expect(instructionsNote(false)).toContain("A change applies from the next request.");
    expect(instructionsNote(true)).toContain("The request running now keeps the earlier text");
    expect(instructionsSavedMessage("Plain tone.")).toContain("from the next request");
    expect(instructionsSavedMessage("  ")).toContain("cleared");
  });

  it("shows the saved text, the count and nothing to save yet", () => {
    const html = renderToStaticMarkup(
      <InstructionsDialog projectId="p_1" saved={"Always rupees.\nPlain tone."} everywhere={null} running={false} onSaved={() => {}} onSavedEverywhere={() => {}} onClose={() => {}} />,
    );
    expect(html).toContain("What should the agent always do here?");
    expect(html).not.toContain("Every project"); // a studio that offers none (shared, or an older server)
    expect(html).toContain("Always rupees.\nPlain tone.");
    expect(html).toContain("26 / 4,000");
    expect(html).toContain("only in this project");
    expect(html).toContain(">Clear<");
    expect(html).toMatch(/<button type="submit"[^>]*disabled=""/);
  });

  it("offers examples and no Clear when there is nothing", () => {
    const html = renderToStaticMarkup(
      <InstructionsDialog projectId="p_1" saved="" everywhere={null} running onSaved={() => {}} onSavedEverywhere={() => {}} onClose={() => {}} />,
    );
    expect(html).toContain("placeholder=\"Always use Indian number format");
    expect(html).not.toContain(">Clear<");
    expect(html).toContain("The request running now keeps the earlier text");
  });

  it("holds a second text for every project, with its own box and words", () => {
    const both = (startWith: "project" | "everywhere") =>
      renderToStaticMarkup(
        <InstructionsDialog
          projectId="p_1"
          saved="Plain tone."
          everywhere={"My solar businesses are in Maharashtra, India.\nUse rupees."}
          running={false}
          onSaved={() => {}}
          onSavedEverywhere={() => {}}
          onClose={() => {}}
          startWith={startWith}
        />,
      );
    const here = both("project");
    expect(here).toMatch(/aria-pressed="true"[^>]*>This project/);
    expect(here).toMatch(/aria-pressed="false"[^>]*>Every project/);
    expect(here).toContain("Plain tone.");
    expect(here).not.toContain("Maharashtra");
    const all = both("everywhere");
    expect(all).toMatch(/aria-pressed="true"[^>]*>Every project/);
    expect(all).toContain("My solar businesses are in Maharashtra, India.");
    expect(all).toContain("What holds for all your work?");
    expect(all).toContain("with every request in every project");
    expect(all).toContain("A project&#x27;s own instructions come first");
    expect(instructionsSavedMessage("x", "everywhere")).toBe("Instructions for every project saved. The agent follows them from the next request.");
  });
});
