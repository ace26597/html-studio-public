import { describe, expect, it } from "vitest";
import {
  addBlocker,
  addContextReducer,
  addContextRequest,
  canAdd,
  canCheck,
  chipList,
  chipTip,
  contextIdsKey,
  contextTabLabel,
  contextWhere,
  dropContextItem,
  editContextPatch,
  emptyAddContext,
  enterAction,
  foundSummary,
  isChecked,
  kindIcon,
  labelNoteProblem,
  libraryButtonText,
  putContextItem,
  sameContext,
  topLine,
  type AddContextAction,
  type AddContextState,
} from "./context";
import type { ContextInspection, ContextItem } from "./types";

const folderFound = {
  git: { branch: "main", head: "abc1234", subject: "Fix" },
  framework: { id: "next", label: "Next.js" },
  files: 2310,
  bytes: 48 * 1024 * 1024,
  partial: false,
  key_files: [".env"],
};

const item = (id: string, over: Partial<ContextItem> = {}): ContextItem => ({
  id,
  kind: "folder",
  path: `/Users/me/dev/${id}`,
  address: null,
  label: id,
  note: "",
  added_at: "2026-11-19T10:00:00Z",
  found: folderFound,
  ...over,
});

const inspection = (over: Partial<ContextInspection> = {}): ContextInspection => ({
  kind: "folder",
  path: "/Users/me/dev/crm",
  address: null,
  label: "crm",
  refused: null,
  found: { ...folderFound, top: ["api/", "src/", "README.md"], top_more: 0 },
  notes: [],
  ...over,
});

const run = (actions: AddContextAction[], from: AddContextState = emptyAddContext) =>
  actions.reduce(addContextReducer, from);

describe("what was found, in one line", () => {
  it("says the kind of app, the branch, the count and the size of a folder", () => {
    expect(foundSummary("folder", folderFound)).toBe("Next.js · git branch main · 2,310 files · 48 MB");
  });

  it("leaves out what is not there, and says when the count stopped", () => {
    expect(foundSummary("folder", { ...folderFound, git: null, framework: null, files: 1, bytes: 10 })).toBe("1 file · 10 B");
    expect(foundSummary("folder", { ...folderFound, partial: true, files: 50000 })).toContain("more than 50,000 files");
    expect(foundSummary("folder", { ...folderFound, git: { branch: null, head: "abc", subject: null } })).toContain(
      "git, no branch checked out",
    );
  });

  it("says plainly when a folder could not be counted in time, with no made-up numbers", () => {
    const line = foundSummary("folder", { ...folderFound, framework: null, git: null, files: null, bytes: null, partial: true });
    expect(line).toBe("Not counted (it took too long)");
    expect(line).not.toMatch(/\d/);
  });

  it("gives a file's size", () => {
    expect(foundSummary("file", { bytes: 1234 })).toBe("1.2 KB");
  });

  it("says whether an address answered, with its title", () => {
    expect(foundSummary("address", { reachable: true, status: 200, title: "CRM", this_computer: true })).toBe("Answers (200) · “CRM”");
    expect(foundSummary("address", { reachable: true, status: 404, title: null, this_computer: true })).toBe("Answers (404)");
    expect(
      foundSummary("address", { reachable: false, status: null, title: null, this_computer: false, error: "Nothing answered" }),
    ).toBe("Did not answer");
  });

  it("is empty when nothing was found", () => {
    expect(foundSummary("folder", null)).toBe("");
    expect(foundSummary("address", undefined)).toBe("");
  });

  it("lists the names at the top of a folder, with how many more", () => {
    expect(topLine({ ...folderFound, top: ["api/", "README.md"], top_more: 3 })).toBe("api/  README.md  and 3 more");
    expect(topLine({ ...folderFound, top: [], top_more: 0 })).toBe("");
    expect(topLine({ bytes: 3 })).toBe("");
  });
});

describe("chips, tips and counts", () => {
  it("shows the first three and counts the rest", () => {
    const items = ["a", "b", "c", "d", "e"].map((id) => item(id));
    const { shown, more } = chipList(items);
    expect(shown.map((entry) => entry.id)).toEqual(["a", "b", "c"]);
    expect(more).toBe(2);
    expect(chipList(items.slice(0, 3)).more).toBe(0);
    expect(chipList([]).shown).toEqual([]);
  });

  it("puts the full path or address in the tip", () => {
    expect(chipTip(item("crm"))).toBe("Folder: /Users/me/dev/crm");
    const address = item("app", { kind: "address", path: null, address: "http://localhost:3000" });
    expect(chipTip(address)).toBe("Address: http://localhost:3000");
    expect(contextWhere(address)).toBe("http://localhost:3000");
  });

  it("gives each kind its icon", () => {
    expect(kindIcon("folder")).toBe("folder");
    expect(kindIcon("file")).toBe("file");
    expect(kindIcon("address")).toBe("globe");
  });

  it("counts on the tab only when there are items", () => {
    expect(contextTabLabel(0)).toBe("Context");
    expect(contextTabLabel(3)).toBe("Context · 3");
  });

  it("counts data sources and context items together on the library button", () => {
    expect(libraryButtonText(0, 0)).toEqual({
      count: 0,
      label: "Context, library and data sources",
      tip: "Context, library and data sources",
    });
    const both = libraryButtonText(1, 2);
    expect(both.count).toBe(3);
    expect(both.tip).toBe("Library · 2 context items, 1 data source attached");
    expect(libraryButtonText(2, 0).tip).toBe("Library · 2 data sources attached");
    expect(libraryButtonText(0, 1).label).toBe("Context, library and data sources (1 context item)");
  });
});

describe("keeping the project's list current", () => {
  it("puts a new item at the end and an edited one in its place, without its state", () => {
    const list = [item("a"), item("b")];
    expect(putContextItem(list, item("c")).map((entry) => entry.id)).toEqual(["a", "b", "c"]);
    const edited = putContextItem(list, { ...item("a", { label: "Renamed" }), state: { ok: true, problem: null } } as ContextItem);
    expect(edited.map((entry) => entry.label)).toEqual(["Renamed", "b"]);
    expect("state" in edited[0]).toBe(false);
  });

  it("drops a removed item", () => {
    expect(dropContextItem([item("a"), item("b")], "a").map((entry) => entry.id)).toEqual(["b"]);
  });

  it("changes the key only when items come or go", () => {
    expect(contextIdsKey([item("a"), item("b")])).toBe("a,b");
    expect(contextIdsKey([item("a", { label: "x" }), item("b")])).toBe("a,b");
    expect(contextIdsKey(undefined)).toBe("");
  });

  it("sees a list with the same names and notes as unchanged", () => {
    expect(sameContext([item("a")], [item("a")])).toBe(true);
    expect(sameContext([item("a")], [item("a", { note: "new" })])).toBe(false);
    expect(sameContext(undefined, [])).toBe(false);
    expect(sameContext([item("a")], [item("a"), item("b")])).toBe(false);
  });
});

describe("the name and the note", () => {
  it("needs a name of at most 60 characters", () => {
    expect(labelNoteProblem("  ", "", { labelRequired: true })).toBe("Give it a name.");
    expect(labelNoteProblem("x".repeat(61), "", { labelRequired: true })).toContain("60");
    expect(labelNoteProblem("crm", "", { labelRequired: true })).toBeNull();
  });

  it("takes a note of at most 500 characters", () => {
    expect(labelNoteProblem("crm", "y".repeat(501), { labelRequired: true })).toContain("500");
    expect(labelNoteProblem("crm", "y".repeat(500), { labelRequired: true })).toBeNull();
  });

  it("sends only what changed in an edit", () => {
    const current = item("crm", { note: "Our CRM" });
    expect(editContextPatch(current, "crm", "Our CRM")).toEqual({});
    expect(editContextPatch(current, " CRM app ", "Our CRM")).toEqual({ label: "CRM app" });
    expect(editContextPatch(current, "crm", "")).toEqual({ note: "" });
  });
});

describe("the Add dialog's flow", () => {
  it("checks what is typed, then adds it", () => {
    let state = run([{ type: "value", value: " ~/dev/crm " }]);
    expect(canCheck(state)).toBe(true);
    expect(enterAction(state)).toBe("check");
    expect(addBlocker(state)).toBe("Check it first.");

    state = run([{ type: "check-start" }], state);
    expect(state.checking).toBe(true);
    expect(canCheck(state)).toBe(false);

    state = run([{ type: "check-done", value: "~/dev/crm", inspection: inspection() }], state);
    expect(isChecked(state)).toBe(true);
    expect(state.label).toBe("crm");
    expect(canAdd(state)).toBe(true);
    expect(enterAction(state)).toBe("add");
    expect(addContextRequest(state)).toEqual({ value: "~/dev/crm" });

    state = run([{ type: "label", value: "Our CRM" }, { type: "note", value: " The API is in api/. " }], state);
    expect(addContextRequest(state)).toEqual({ value: "~/dev/crm", label: "Our CRM", note: "The API is in api/." });
  });

  it("clears the result when the field changes after a check", () => {
    const checked = run([
      { type: "value", value: "~/dev/crm" },
      { type: "check-done", value: "~/dev/crm", inspection: inspection() },
      { type: "label", value: "Mine" },
    ]);
    const edited = run([{ type: "value", value: "~/dev/crm2" }], checked);
    expect(edited.inspection).toBeNull();
    expect(edited.label).toBe("");
    expect(isChecked(edited)).toBe(false);
    expect(canAdd(edited)).toBe(false);
    // Only spaces around it: still the same value, the result stays.
    expect(run([{ type: "value", value: "~/dev/crm " }], checked).inspection).not.toBeNull();
  });

  it("drops a check's answer for a value that is no longer in the field", () => {
    const state = run([
      { type: "value", value: "~/dev/a" },
      { type: "check-start" },
      { type: "value", value: "~/dev/b" },
      { type: "check-done", value: "~/dev/a", inspection: inspection() },
    ]);
    expect(state.inspection).toBeNull();
    expect(state.checking).toBe(false);
  });

  it("cannot add a refused value, and says why", () => {
    const refused = inspection({ refused: "This is the home folder.", found: null });
    const state = run([
      { type: "value", value: "~" },
      { type: "check-done", value: "~", inspection: refused },
    ]);
    expect(canAdd(state)).toBe(false);
    expect(addBlocker(state)).toBe("This is the home folder.");
    expect(enterAction(state)).toBeNull();
  });

  it("keeps the server's message for a failed check or add", () => {
    const failed = run([
      { type: "value", value: "hello" },
      { type: "check-start" },
      { type: "check-failed", message: "Type a folder's full path." },
    ]);
    expect(failed.error).toBe("Type a folder's full path.");
    expect(failed.checking).toBe(false);
    expect(run([{ type: "value", value: "~/x" }], failed).error).toBeNull();

    const added = run([
      { type: "value", value: "~/dev/crm" },
      { type: "check-done", value: "~/dev/crm", inspection: inspection() },
      { type: "add-start" },
    ]);
    expect(canAdd(added)).toBe(false);
    const conflict = run([{ type: "add-failed", message: "This project already has that as context." }], added);
    expect(conflict.adding).toBe(false);
    expect(conflict.error).toBe("This project already has that as context.");
    expect(canAdd(conflict)).toBe(true);
  });

  it("needs a name before adding", () => {
    const state = run([
      { type: "value", value: "~/dev/crm" },
      { type: "check-done", value: "~/dev/crm", inspection: inspection() },
      { type: "label", value: "  " },
    ]);
    expect(addBlocker(state)).toBe("Give it a name.");
    expect(canAdd(state)).toBe(false);
  });

  it("has nothing to check in an empty field", () => {
    expect(canCheck(emptyAddContext)).toBe(false);
    expect(enterAction(emptyAddContext)).toBeNull();
  });
});
