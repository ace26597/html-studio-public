import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { ContextChips } from "./Chat";
import { AddContextView } from "./ContextDialog";
import { addContextReducer, emptyAddContext, type AddContextAction, type AddContextState } from "./context";
import type { ContextInspection, ContextItem } from "./types";

const noop = () => {};
const run = (actions: AddContextAction[]) => actions.reduce(addContextReducer, emptyAddContext);
const draw = (state: AddContextState, full = false) =>
  renderToStaticMarkup(
    <AddContextView state={state} dispatch={noop} full={full} onCheck={noop} onAdd={noop} onCancel={noop} />,
  );

const folder: ContextInspection = {
  kind: "folder",
  path: "/Users/me/dev/crm",
  address: null,
  label: "crm",
  refused: null,
  found: {
    git: { branch: "main", head: "abc", subject: "Fix" },
    framework: { id: "next", label: "Next.js" },
    files: 2310,
    bytes: 48 * 1024 * 1024,
    partial: false,
    key_files: [".env"],
    top: ["api/", "src/", "README.md"],
    top_more: 0,
  },
  notes: ["Key files at its top (.env): the agent is told not to open them."],
};

/** The Add button's opening tag. */
const addButton = (html: string) => /<button type="submit"[^>]*>/.exec(html)?.[0] ?? "";

describe("the Add dialog", () => {
  it("starts with one field, its example, and Add switched off", () => {
    const html = draw(emptyAddContext);
    expect(html).toContain("A folder, a file or an address");
    expect(html).toContain('placeholder="~/dev/my-app or http://localhost:3000"');
    expect(html).toContain(">Check</button>");
    expect(addButton(html)).toContain("disabled");
    expect(html).not.toContain("What is it?");
  });

  it("after a check shows what was found, the notes, the name and the note, and lets it be added", () => {
    const html = draw(run([{ type: "value", value: "~/dev/crm" }, { type: "check-done", value: "~/dev/crm", inspection: folder }]));
    expect(html).toContain("/Users/me/dev/crm");
    expect(html).toContain("Next.js · git branch main · 2,310 files · 48 MB");
    expect(html).toContain("api/  src/  README.md");
    expect(html).toContain("the agent is told not to open them");
    expect(html).toContain('value="crm"');
    expect(html).toContain("What is it?");
    expect(addButton(html)).not.toContain("disabled");
  });

  it("shows a refused value's reason and cannot add it", () => {
    const refused = { ...folder, path: "/Users/me", label: "me", refused: "This is the home folder. Choose a folder inside it.", found: null };
    const html = draw(run([{ type: "value", value: "~" }, { type: "check-done", value: "~", inspection: refused }]));
    expect(html).toContain('role="alert"');
    expect(html).toContain("This is the home folder. Choose a folder inside it.");
    expect(addButton(html)).toContain("disabled");
    expect(html).not.toContain("What is it?");
  });

  it("forgets the result once the field says something else", () => {
    const checked = run([{ type: "value", value: "~/dev/crm" }, { type: "check-done", value: "~/dev/crm", inspection: folder }]);
    const html = draw(addContextReducer(checked, { type: "value", value: "~/dev/other" }));
    expect(html).not.toContain("Next.js");
    expect(addButton(html)).toContain("disabled");
  });

  it("shows the server's message, such as a 409 already_added", () => {
    const state = run([
      { type: "value", value: "~/dev/crm" },
      { type: "check-done", value: "~/dev/crm", inspection: folder },
      { type: "add-start" },
      { type: "add-failed", message: "This project already has that as context." },
    ]);
    const html = draw(state);
    expect(html).toContain("This project already has that as context.");
    expect(html).toContain('role="alert"');
  });

  it("says so, and does not offer Add, when the project is full", () => {
    const html = draw(run([{ type: "value", value: "~/dev/crm" }, { type: "check-done", value: "~/dev/crm", inspection: folder }]), true);
    expect(html).toContain("at most 12 items");
    expect(addButton(html)).toContain("disabled");
  });
});

const item = (id: string, kind: ContextItem["kind"] = "folder"): ContextItem => ({
  id,
  kind,
  path: kind === "address" ? null : `/Users/me/dev/${id}`,
  address: kind === "address" ? `http://localhost:3000/${id}` : null,
  label: `Label ${id}`,
  note: "",
  added_at: "2026-11-19T10:00:00Z",
  found: null,
});

describe("the context chips above the message box", () => {
  it("are not drawn without items", () => {
    expect(renderToStaticMarkup(<ContextChips items={[]} />)).toBe("");
  });

  it("show “Context”, the first three with their full path or address as the tip, and +N", () => {
    const html = renderToStaticMarkup(
      <ContextChips items={[item("a"), item("b", "address"), item("c", "file"), item("d"), item("e")]} onOpen={noop} />,
    );
    expect(html).toContain(">Context</span>");
    expect(html).toContain("Label a");
    expect(html).toContain("Label b");
    expect(html).toContain("Label c");
    expect(html).not.toContain(">Label d<");
    expect(html).toContain('title="Folder: /Users/me/dev/a"');
    expect(html).toContain('title="Address: http://localhost:3000/b"');
    expect(html).toContain(">+2</button>");
    expect(html).toContain('title="Label d, Label e"');
    expect(html.match(/<button/g)).toHaveLength(4);
  });

  it("have no +N with three items or fewer", () => {
    const html = renderToStaticMarkup(<ContextChips items={[item("a"), item("b"), item("c")]} />);
    expect(html.match(/<button/g)).toHaveLength(3);
    expect(html).not.toContain("+");
  });
});
