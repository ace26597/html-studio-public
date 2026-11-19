import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import { api, ApiError, request } from "./api";
import { ItemRow } from "./LibraryIndexView";
import { RowActions } from "./LibraryView";
import { NewProjectDialog, newPageSources } from "./NewProjectDialog";
import { AskForm, CopyForm, SkillReader, type FileState } from "./SkillOpenDialog";
import {
  askBody,
  askProblem,
  copiedId,
  copyPlan,
  copyReducer,
  CUT_NOTE,
  defaultCopyName,
  frontEntries,
  newCopyState,
  otherFileNote,
  skillFactsLine,
  skillFileRows,
  skillOffers,
  skillRowOffers,
  skillWhere,
  type CopyAction,
  type CopyState,
} from "./skillReader";
import type { LibraryItem, SkillFull } from "./types";

const noop = () => {};
const NOW = Date.parse("2026-11-19T12:00:00Z");

const skill: SkillFull = {
  id: "studio:deep-research",
  name: "deep-research",
  label: "Deep research",
  description: "Use when the person asks for a researched report.",
  source: "studio",
  origin: null,
  body: "# Deep research\n\nRead every source.",
  body_cut: false,
  front: { "allowed-tools": "Read, WebSearch", license: "MIT" },
  files: [
    { path: "reference/notes.md", size: 900, kind: "text", script: false },
    { path: "SKILL.md", size: 2048, kind: "text", script: false },
    { path: "scripts/fetch.py", size: 300, kind: "text", script: true },
    { path: "assets/flow.png", size: 4000, kind: "picture", script: false },
    { path: "reference/guide.pdf", size: 90_000, kind: "other", script: false },
  ],
  scripts: ["scripts/fetch.py"],
  size: 97_248,
  yours: false,
  picked: 5,
  used: 3,
  last_used: "2026-11-17T12:00:00Z",
};

const read = (props: Partial<Parameters<typeof SkillReader>[0]> = {}) =>
  renderToStaticMarkup(
    <SkillReader
      skill={skill}
      selected="SKILL.md"
      file={null}
      pictureUrl={(path) => api.skillFileRawUrl(skill.id, path)}
      onSelect={noop}
      now={NOW}
      {...props}
    />,
  );

const run = (start: CopyState, actions: CopyAction[]) => actions.reduce(copyReducer, start);
const refused = (code: string, details?: unknown): CopyAction => ({
  type: "refused",
  error: new ApiError(409, code, `refused: ${code}`, details),
});

describe("the facts line", () => {
  it("counts files and scripts, gives the size, and says it was never picked", () => {
    const line = skillFactsLine({ files: [skill.files[1]], scripts: [], size: 2048, picked: 0, used: 0, last_used: null }, NOW);
    expect(line).toBe("1 file · no scripts · 2.0 KB · never picked yet");
  });

  it("uses plurals and says when it was last used", () => {
    expect(skillFactsLine(skill, NOW)).toBe("5 files · 1 script · 95 KB · picked 5 times, used 3 · last used 2 days ago");
  });

  it("picked once and never used: no 'last used'", () => {
    const line = skillFactsLine({ ...skill, scripts: ["a.sh", "b.sh"], picked: 1, used: 0, last_used: null }, NOW);
    expect(line).toContain("2 scripts");
    expect(line).toContain("picked 1 time, used 0");
    expect(line).not.toContain("last used");
  });

  it("says where a skill comes from, preferring the index's origin", () => {
    expect(skillWhere({ source: "studio", origin: null })).toBe("Comes with the studio");
    expect(skillWhere({ source: "plugin", origin: "Plugin pdf@anthropic" })).toBe("Plugin pdf@anthropic");
    expect(skillWhere({ source: "elsewhere", origin: null })).toBe("In the library");
  });
});

describe("the file list", () => {
  it("puts the instructions first, then every file by path, and marks scripts", () => {
    const rows = skillFileRows(skill.files);
    expect(rows.map((row) => row.label)).toEqual([
      "Instructions",
      "assets/flow.png",
      "reference/guide.pdf",
      "reference/notes.md",
      "scripts/fetch.py",
    ]);
    expect(rows.filter((row) => row.script).map((row) => row.path)).toEqual(["scripts/fetch.py"]);
    expect(rows[0].instructions).toBe(true);
  });

  it("lists the front matter's extras, leaving out empty ones", () => {
    expect(frontEntries({ license: "MIT", empty: "  " })).toEqual([["license", "MIT"]]);
    expect(frontEntries(undefined)).toEqual([]);
  });
});

describe("what a skill offers", () => {
  it("someone else's: explain, export, a copy", () => {
    expect(skillOffers({ yours: false }, false)).toEqual({
      explain: true,
      export: true,
      copy: true,
      edit: false,
      askChange: false,
      delete: false,
    });
  });

  it("one's own: explain, export, edit, ask for a change, delete", () => {
    expect(skillOffers({ yours: true }, false)).toEqual({
      explain: true,
      export: true,
      copy: false,
      edit: true,
      askChange: true,
      delete: true,
    });
  });

  it("on a shared studio only explain and export, whoever's it is", () => {
    for (const yours of [true, false]) {
      expect(skillOffers({ yours }, true)).toEqual({
        explain: true,
        export: true,
        copy: false,
        edit: false,
        askChange: false,
        delete: false,
      });
    }
  });

  it("a row in the list: Open and Export for any skill in the index, Delete for one's own", () => {
    expect(skillRowOffers({ exportId: "studio:x" }, false)).toEqual({ open: true, export: true, delete: false });
    expect(skillRowOffers({ exportId: "yours:x", action: { name: "x" } }, false)).toEqual({ open: true, export: true, delete: true });
    expect(skillRowOffers({ exportId: "yours:x", action: { name: "x" } }, true)).toEqual({ open: true, export: true, delete: false });
    // A built-in skill is not in the index: nothing to open.
    expect(skillRowOffers({}, false)).toEqual({ open: false, export: false, delete: false });
  });

  it("draws Open, Export and Delete on a row, and no Delete on a shared studio", () => {
    const row = { key: "k", title: "mine", detail: null, status: null, note: null, exportId: "yours:mine", action: { kind: "delete-skill" as const, name: "mine" } };
    const html = renderToStaticMarkup(<RowActions row={row} shared={false} onOpen={noop} onDelete={noop} />);
    expect(html).toContain('aria-label="Open the skill mine"');
    expect(html).toContain("/api/library/skills/export?id=yours%3Amine");
    expect(html).toContain('aria-label="Delete the skill mine"');
    const sharedHtml = renderToStaticMarkup(<RowActions row={row} shared onOpen={noop} onDelete={noop} />);
    expect(sharedHtml).toContain("Open");
    expect(sharedHtml).not.toContain("Delete the skill");
  });

  it("gives every skill in the index Open beside Export", () => {
    const item: LibraryItem = { id: "machine:pdf", name: "pdf", label: "pdf", description: "PDFs", source: "machine" };
    const html = renderToStaticMarkup(<ul><ItemRow item={item} kind="skills" onDecide={noop} onOpen={noop} /></ul>);
    expect(html).toContain('aria-label="Open the skill pdf"');
    expect(html).toContain("Export");
  });
});

describe("the reader", () => {
  it("shows where it comes from, the facts, when it is used and the extras", () => {
    const html = read();
    expect(html).toContain("Comes with the studio");
    expect(html).toContain("5 files · 1 script");
    expect(html).toContain("When it is used");
    expect(html).toContain("Also says");
    expect(html).toContain("<dt>allowed-tools</dt><dd>Read, WebSearch</dd>");
  });

  it("leaves out 'Also says' when the front matter says nothing else", () => {
    expect(read({ skill: { ...skill, front: {} } })).not.toContain("Also says");
  });

  it("lists the instructions first and marks the script", () => {
    const html = read();
    expect(html.indexOf("Instructions")).toBeLessThan(html.indexOf("assets/flow.png"));
    expect(html).toMatch(/scripts\/fetch\.py<\/span><span class="skill-file-meta"><span class="badge badge-script">script<\/span>/);
    expect(html.match(/badge-script/g)).toHaveLength(1);
  });

  it("shows the instructions as plain text, and a note when they were cut", () => {
    const html = read({ skill: { ...skill, body: "<b>bold</b> **not bold**", body_cut: true } });
    expect(html).toContain("&lt;b&gt;bold&lt;/b&gt; **not bold**");
    expect(html).not.toContain("<b>bold</b>");
    expect(html).toContain(CUT_NOTE);
    expect(read()).not.toContain(CUT_NOTE);
  });

  it("shows a text file as text, with the cut note, and says when it is a script", () => {
    const file: FileState = {
      status: "ready",
      content: { path: "scripts/fetch.py", size: 300, kind: "text", script: true, text: "print('<hi>')", cut: true },
    };
    const html = read({ selected: "scripts/fetch.py", file });
    expect(html).toContain("<pre");
    expect(html).toContain("print(&#x27;&lt;hi&gt;&#x27;)");
    expect(html).toContain(CUT_NOTE);
    expect(html).toContain("The agent may run this file");
  });

  it("says when a file is loading or could not be read", () => {
    expect(read({ selected: "reference/notes.md", file: { status: "loading" } })).toContain("Opening the file…");
    expect(read({ selected: "reference/notes.md", file: { status: "error", message: "It is gone." } })).toContain("It is gone.");
  });

  it("shows a picture as a picture, from the raw address", () => {
    const html = read({ selected: "assets/flow.png" });
    expect(html).toContain('<img src="/api/library/skills/file?id=studio%3Adeep-research&amp;path=assets%2Fflow.png&amp;raw=1"');
  });

  it("names a file of another kind and points at Export", () => {
    const html = read({ selected: "reference/guide.pdf" });
    expect(html).toContain(otherFileNote("reference/guide.pdf").replace(/“/g, "“"));
    expect(html).toContain("Export the skill to open it.");
    expect(html).not.toContain("<img");
    expect(html).not.toContain("<pre");
  });
});

describe("making one's own copy", () => {
  it("starts from the skill's name with -mine, kept within 60 characters", () => {
    expect(newCopyState("deep-research").name).toBe("deep-research-mine");
    const long = "a".repeat(60);
    expect(defaultCopyName(long)).toHaveLength(60);
    expect(defaultCopyName(long).endsWith("-mine")).toBe(true);
    expect(copiedId("deep-research-mine")).toBe("yours:deep-research-mine");
  });

  it("checks the name as the contract does", () => {
    const at = (name: string) => copyPlan({ ...skill, scripts: [] }, run(newCopyState("x"), [{ type: "name", name }]));
    expect(at("").nameIssue).toBe("Give it a name.");
    expect(at("Deep Research").nameIssue).toBe("Use lower-case letters, digits, “-” and “_” only.");
    expect(at("a".repeat(61)).nameIssue).toBe("The name can be at most 60 characters.");
    expect(at("ok_name-2").nameIssue).toBeNull();
    expect(at("ok_name-2").ready).toBe(true);
    expect(at("ok_name-2").request).toEqual({ from: skill.id, name: "ok_name-2" });
  });

  it("needs the scripts box ticked before it works, and then allows scripts", () => {
    const start = newCopyState("deep-research");
    const before = copyPlan(skill, start);
    expect(before.ready).toBe(false);
    expect(before.waiting).toBe("Tick the box to accept its scripts.");
    const after = copyPlan(skill, run(start, [{ type: "tick", on: true }]));
    expect(after.ready).toBe(true);
    expect(after.request).toEqual({ from: skill.id, name: "deep-research-mine", allow_scripts: true });
  });

  it("name_taken: offers Replace it, which sends replace; a new name forgets it", () => {
    const start = run(newCopyState("deep-research"), [{ type: "tick", on: true }, { type: "submit" }, refused("name_taken")]);
    expect(start.busy).toBe(false);
    const asked = copyPlan(skill, start);
    expect(asked.askReplace).toBe(true);
    expect(asked.ready).toBe(false);
    const replacing = run(start, [{ type: "replace" }]);
    const plan = copyPlan(skill, replacing);
    expect(plan.ready).toBe(true);
    expect(plan.request.replace).toBe(true);
    expect(plan.label).toBe("Replace my skill with this copy");
    const renamed = run(replacing, [{ type: "name", name: "other-name" }]);
    expect(copyPlan(skill, renamed).request.replace).toBeUndefined();
    expect(copyPlan(skill, renamed).askReplace).toBe(false);
    expect(copyPlan(skill, renamed).ready).toBe(true);
  });

  it("name_taken_by_studio: asks for another name until it changes", () => {
    const state = run(newCopyState("deep-research"), [{ type: "tick", on: true }, refused("name_taken_by_studio")]);
    const plan = copyPlan(skill, state);
    expect(plan.nameIssue).toContain("studio’s own skills");
    expect(plan.ready).toBe(false);
    expect(copyPlan(skill, run(state, [{ type: "name", name: "fresh" }])).ready).toBe(true);
  });

  it("scripts_need_confirmation: unticks, adds the scripts the server named, and waits for the tick", () => {
    const state = run(newCopyState("deep-research"), [
      { type: "tick", on: true },
      refused("scripts_need_confirmation", { scripts: ["scripts/fetch.py", "run.sh"] }),
    ]);
    const plan = copyPlan(skill, state);
    expect(plan.scripts).toEqual(["scripts/fetch.py", "run.sh"]);
    expect(plan.ready).toBe(false);
    expect(state.refusal?.code).toBe("scripts_need_confirmation");
    const ticked = run(state, [{ type: "tick", on: true }]);
    expect(ticked.refusal).toBeNull();
    expect(copyPlan(skill, ticked).request.allow_scripts).toBe(true);
  });

  it("any other refusal is a sentence", () => {
    const state = run(newCopyState("x"), [{ type: "refused", error: new ApiError(413, "too_large", "The skill is too large.") }]);
    expect(state.error).toBe("The skill is too large.");
    expect(state.refusal).toBeNull();
  });

  it("draws the scripts and their box, with the button off until it is ticked", () => {
    const html = renderToStaticMarkup(
      <CopyForm skill={skill} state={newCopyState("deep-research")} dispatch={noop} onSubmit={noop} onCancel={noop} />,
    );
    expect(html).toContain('value="deep-research-mine"');
    expect(html).toContain("This skill brings 1 script the agent may run");
    expect(html).toMatch(/<button type="submit" class="btn btn-primary" disabled="">/);
  });

  it("draws Replace it after name_taken", () => {
    const state = run(newCopyState("deep-research"), [{ type: "tick", on: true }, refused("name_taken")]);
    const html = renderToStaticMarkup(<CopyForm skill={skill} state={state} dispatch={noop} onSubmit={noop} onCancel={noop} />);
    expect(html).toContain("You already have a skill called “deep-research-mine”.");
    expect(html).toContain("Replace it");
  });
});

describe("asking the agent", () => {
  it("explain may be empty; change needs words", () => {
    expect(askProblem("explain", "  ")).toBeNull();
    expect(askProblem("change", "  ")).toBe("Say what should change.");
    expect(askProblem("explain", "x".repeat(2001))).toContain("2,000");
  });

  it("sends text only when something was written", () => {
    expect(askBody("studio:x", "explain", " ")).toEqual({ id: "studio:x", about: "explain" });
    expect(askBody("yours:x", "change", " Shorter. ")).toEqual({ id: "yours:x", about: "change", text: "Shorter." });
  });

  it("draws the optional question box for explain and the required one for change", () => {
    const explain = renderToStaticMarkup(
      <AskForm about="explain" text="" busy={false} problem={null} error={null} onText={noop} onSubmit={noop} onCancel={noop} />,
    );
    expect(explain).toContain("Anything in particular you want to know? (optional)");
    const change = renderToStaticMarkup(
      <AskForm about="change" text="" busy={false} problem="Say what should change." error={null} onText={noop} onSubmit={noop} onCancel={noop} />,
    );
    expect(change).toContain("What should change?");
    expect(change).toContain("Say what should change.");
  });
});

describe("the API", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("builds the file addresses", () => {
    expect(api.skillFileRawUrl("plugin:a b", "x/y.png")).toBe("/api/library/skills/file?id=plugin%3Aa%20b&path=x%2Fy.png&raw=1");
  });

  it("keeps error.details on a refusal", async () => {
    const details = { scripts: ["run.sh"] };
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        new Response(JSON.stringify({ error: { code: "scripts_need_confirmation", message: "Scripts.", details } }), { status: 409 }),
      ),
    );
    const error = (await request("POST", "/skills/copy", {}).catch((cause: unknown) => cause)) as ApiError;
    expect(error).toBeInstanceOf(ApiError);
    expect(error.code).toBe("scripts_need_confirmation");
    expect(error.details).toEqual(details);
  });
});

describe("the New page dialog's third choice", () => {
  const props = { creating: false, createError: null, onCreate: noop, onAttach: noop, onClose: noop };

  it("offers A skill beside a template and a folder, unless it cannot", () => {
    expect(newPageSources(true, false).map((item) => item.label)).toEqual([
      "A template or a blank page",
      "An app on this computer",
      "A skill",
    ]);
    expect(newPageSources(false).map((item) => item.value)).toEqual(["template", "folder"]);
    expect(renderToStaticMarkup(<NewProjectDialog {...props} onSkill={noop} />)).toContain(">A skill</button>");
    expect(renderToStaticMarkup(<NewProjectDialog {...props} />)).not.toContain(">A skill</button>");
  });

  it("says what a skill is and offers Describe it", () => {
    const html = renderToStaticMarkup(<NewProjectDialog {...props} onSkill={noop} initialSource="skill" />);
    expect(html).toContain("A skill is a set of instructions the agent follows for one kind of job");
    expect(html).toContain("Describe it");
    expect(html).not.toContain(">Create<");
  });
});
