import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { skillImportSearch } from "./api";
import { makeSkillEntry, MakeSkillDialog } from "./MakeSkillDialog";
import { ImportPreview } from "./NewSkillDialog";
import projectSource from "./Project.tsx?raw";
import {
  DESCRIBE_LEAD,
  describeRequest,
  FROM_WORK_LEAD,
  fromWorkRequest,
  importPlan,
  isSaveSkill,
  newDescribeProgress,
  parseSkillApproval,
  scriptsLine,
  skillProjectName,
  startDescribedSkill,
  type DescribeCalls,
  approvalFileRows,
  keptScriptsSentence,
  keptSentence,
  scriptFacts,
} from "./skillMaking";
import { skillEditProblem } from "./SkillOpenDialog";
import type { ApprovalItem } from "./thread";
import { ApprovalCard, SkillApprovalDetail } from "./ThreadView";
import type { Project, Selection, SkillApprovalInfo, SkillImportPreview } from "./types";

const preview: SkillImportPreview = {
  name: "weekly-report",
  description: "Use for the weekly sales report.",
  body_start: "# Weekly report\n\nFirst get the orders.",
  files: [{ path: "SKILL.md", size: 2048 }, { path: "scripts/total.py", size: 512 }],
  scripts: [],
  size: 2560,
  exists: false,
  taken_by_studio: false,
};
const none = { scriptsAccepted: false, nameChoice: null, newName: "" };

describe("the requests say what the contract says", () => {
  it("pins the lead sentences", () => {
    expect(DESCRIBE_LEAD).toBe("Make a new skill for my library from this description (use the make-a-skill skill):");
    expect(FROM_WORK_LEAD).toBe(
      "Make a skill from how this was made (use the make-a-skill skill), so I can get the same kind of result again for a different subject.",
    );
  });

  it("puts a blank line between the lead and the description", () => {
    expect(describeRequest("  Total the orders.\n")).toBe(`${DESCRIBE_LEAD}\n\nTotal the orders.`);
  });

  it("adds the note only when there is one", () => {
    expect(fromWorkRequest("  ")).toBe(FROM_WORK_LEAD);
    expect(fromWorkRequest(" the company and the quarter ")).toBe(
      `${FROM_WORK_LEAD}\n\nWhat changes each time: the company and the quarter`,
    );
  });
});

describe("the project a description starts", () => {
  it("is named after the first words, cut at a word", () => {
    const name = skillProjectName("Every Monday, get last week’s orders from the sales database and total them");
    expect(name).toBe("Skill: Every Monday, get last week’s orders");
    expect(name.length - "Skill: ".length).toBeLessThanOrEqual(40);
  });

  it("keeps a short description whole, on one line, without a dangling mark", () => {
    expect(skillProjectName("  Total the\n\norders, ")).toBe("Skill: Total the orders");
  });

  it("keeps a word that ends exactly at the limit", () => {
    expect(skillProjectName(`${"a".repeat(19)} ${"b".repeat(20)} more`)).toBe(`Skill: ${"a".repeat(19)} ${"b".repeat(20)}`);
  });

  it("cuts one very long word rather than leaving the name empty", () => {
    expect(skillProjectName("x".repeat(90))).toBe(`Skill: ${"x".repeat(40)}`);
  });

  const project = { id: "p1", connection: "claude", model: "opus" } as Project;
  const calls = (over: Partial<DescribeCalls> = {}): DescribeCalls => ({
    createProject: vi.fn(async () => project),
    upload: vi.fn(async (_id: string, file: File) => ({ path: `attachments/${file.name}` })),
    startTurn: vi.fn(async () => ({ turn_id: "t1" })),
    ...over,
  });

  it("uploads the examples and sends the one request with the project's connection and model", async () => {
    const made = calls();
    const file = new File(["x"], "sample.png");
    await expect(startDescribedSkill(made, "Total the orders", [file], newDescribeProgress())).resolves.toBe("p1");
    expect(made.createProject).toHaveBeenCalledWith({ name: "Skill: Total the orders" });
    expect(made.upload).toHaveBeenCalledWith("p1", file, "attachments");
    expect(made.startTurn).toHaveBeenCalledWith("p1", {
      text: `${DESCRIBE_LEAD}\n\nTotal the orders`,
      connection: "claude",
      model: "opus",
      attachments: ["attachments/sample.png"],
    });
  });

  it("sends nothing when an example fails, and Start again does not repeat what worked", async () => {
    const good = new File(["x"], "good.png");
    const bad = new File(["y"], "bad.png");
    let refuse = true;
    const made = calls({
      upload: vi.fn(async (_id: string, file: File) => {
        if (file === bad && refuse) throw new Error("Uploads are limited to 25 MB.");
        return { path: `attachments/${file.name}` };
      }),
    });
    const progress = newDescribeProgress();
    await expect(startDescribedSkill(made, "Total", [good, bad], progress)).rejects.toThrow(
      "“bad.png” wasn’t added, so nothing was sent: Uploads are limited to 25 MB.",
    );
    expect(made.startTurn).not.toHaveBeenCalled();
    refuse = false;
    await startDescribedSkill(made, "Total", [good, bad], progress);
    expect(made.createProject).toHaveBeenCalledTimes(1);
    expect(made.upload).toHaveBeenCalledTimes(3);
    expect(made.startTurn).toHaveBeenCalledTimes(1);
  });
});

describe("what an import still needs", () => {
  it("is ready at once for a plain skill with a free name", () => {
    expect(importPlan(preview, none)).toMatchObject({ nameAsk: "none", ready: true, waiting: null, options: {} });
  });

  it("needs the tick when the file holds scripts", () => {
    const scripts = { ...preview, scripts: ["scripts/total.py"] };
    expect(importPlan(scripts, none)).toMatchObject({ ready: false, waiting: "Tick the box to accept its scripts." });
    expect(importPlan(scripts, { ...none, scriptsAccepted: true })).toMatchObject({ ready: true, options: { allowScripts: true } });
  });

  it("asks about a name that is already in the library", () => {
    const exists = { ...preview, exists: true };
    expect(importPlan(exists, none)).toMatchObject({ nameAsk: "replace", ready: false, renaming: false });
    expect(importPlan(exists, { ...none, nameChoice: "replace" })).toMatchObject({ ready: true, options: { replace: true } });
    expect(importPlan(exists, { ...none, nameChoice: "rename" })).toMatchObject({ renaming: true, ready: false });
    expect(importPlan(exists, { ...none, nameChoice: "rename", newName: "weekly-report" }).nameIssue).toContain("already");
    expect(importPlan(exists, { ...none, nameChoice: "rename", newName: "weekly-2" })).toMatchObject({
      ready: true,
      options: { name: "weekly-2" },
    });
  });

  it("needs another name when one of the studio's own skills has it, and never offers to replace that", () => {
    const taken = { ...preview, taken_by_studio: true, exists: true };
    expect(importPlan(taken, { ...none, nameChoice: "replace" })).toMatchObject({ nameAsk: "rename", renaming: true, ready: false, options: { name: "" } });
    expect(importPlan(taken, { ...none, newName: "Bad Name" }).nameIssue).toContain("lower-case");
    expect(importPlan(taken, { ...none, newName: "weekly-report" }).nameIssue).toContain("studio");
    const fine = importPlan(taken, { ...none, newName: "my-weekly" });
    expect(fine).toMatchObject({ ready: true, options: { name: "my-weekly" } });
    expect(fine.options.replace).toBeUndefined();
  });

  it("builds the query the import route takes", () => {
    expect(skillImportSearch({ dryRun: true })).toBe("?dry_run=1");
    expect(skillImportSearch({ replace: true, allowScripts: true, name: "a-b" })).toBe("?replace=1&allow_scripts=1&name=a-b");
    expect(skillImportSearch({})).toBe("");
  });

  it("shows the preview, and keeps Add to my library off until the scripts are accepted", () => {
    const html = renderToStaticMarkup(
      <ImportPreview preview={{ ...preview, scripts: ["scripts/total.py", "run.sh"] }} busy={false} onBack={() => {}} onAdd={() => {}} />,
    );
    expect(html).toContain("weekly-report");
    expect(html).toContain("How the instructions begin");
    expect(html).toContain("2.0 KB");
    expect(html).toContain("run.sh");
    expect(html).toContain("This skill brings 2 scripts the agent may run");
    expect(html).toMatch(/<button[^>]*disabled=""[^>]*>Add to my library/);
    expect(scriptsLine(1)).toBe("This skill brings 1 script the agent may run");
  });

  it("says why a file was refused next to the button", () => {
    const html = renderToStaticMarkup(
      <ImportPreview preview={preview} busy={false} error="You already have a skill called weekly-2." onBack={() => {}} onAdd={() => {}} />,
    );
    expect(html.indexOf("You already have a skill called weekly-2.")).toBeGreaterThan(html.indexOf("Files (2)"));
    expect(html.indexOf("You already have a skill called weekly-2.")).toBeLessThan(html.indexOf("Add to my library"));
  });
});

describe("the approval card for a skill", () => {
  const body = `# Weekly report\n\n${"Total the orders by region.\n\n".repeat(400)}## Check\n\nCompare every total.`;
  // As `ownskills.plan` writes it.
  const detail = `When it is used: Use for the weekly sales report.\n\nInstructions:\n${body}\n\nFiles:\nreference/layout.html (3.1 KB)\nreference/logo.png (1 MB)`;
  const item: ApprovalItem = {
    type: "approval", key: "a", approvalId: "a1", turnId: "t1", tool: "save_skill",
    title: "Save the skill “weekly-report” to your library", detail,
    reason: "A skill in your library is given to the agent in every project from now on.",
    status: "waiting", by: null, seq: 1,
  };
  const actions = { decisions: {}, onDecide: () => {} } as unknown as Parameters<typeof ApprovalCard>[0]["actions"];

  it("recognises the tool", () => {
    expect(isSaveSkill("save_skill")).toBe(true);
    expect(isSaveSkill("mcp__studio__save_skill")).toBe(true);
    expect(isSaveSkill("Bash")).toBe(false);
  });

  it("splits the description, the instructions and the files", () => {
    expect(parseSkillApproval(detail)).toEqual({
      description: "Use for the weekly sales report.",
      body: body.trim(),
      files: [
        { path: "reference/layout.html", size: "3.1 KB" },
        { path: "reference/logo.png", size: "1 MB" },
      ],
      kept: null,
    });
  });

  it("reads a skill with no files, and the files a replace keeps", () => {
    const kept = "Files kept from the version it replaces: reference/old.md, reference/map.png";
    expect(parseSkillApproval(`When it is used: When.\n\nInstructions:\nStep one.\n\n${kept}`)).toEqual({
      description: "When.",
      body: "Step one.",
      files: [],
      kept,
    });
  });

  it("keeps a Files heading of the skill's own in the instructions", () => {
    const own = "When it is used: When.\n\nInstructions:\nStep one.\n\nFiles:\nKeep them tidy.";
    expect(parseSkillApproval(own).body).toBe("Step one.\n\nFiles:\nKeep them tidy.");
  });

  it("leaves nothing out when the text has another shape", () => {
    const odd = "# Only instructions\n\nStep one.\n\nFiles:\nreference/a.md (1 KB)";
    expect(parseSkillApproval(odd)).toEqual({ description: "", body: odd, files: [], kept: null });
  });

  it("draws a long skill whole, in a box of its own, with the reason and both answers", () => {
    const html = renderToStaticMarkup(<ApprovalCard item={item} actions={actions} />);
    expect(html).toContain("When it is used");
    expect(html).toContain('class="skill-text"');
    expect(html.match(/Total the orders by region\./g)?.length).toBe(400);
    expect(html).toContain("Compare every total.");
    expect(html).toContain("reference/logo.png");
    expect(html).toContain(item.reason);
    expect(html).toContain(">Allow<");
    expect(html).toContain(">Deny<");
    expect(html).not.toContain("approval-detail");
  });

  it("shows a skill's text as written, so markup in it cannot hide anything", () => {
    const html = renderToStaticMarkup(
      <SkillApprovalDetail detail={"When it is used: When.\n\nInstructions:\n<img src=x onerror=alert(1)> **bold**"} />,
    );
    expect(html).toContain("&lt;img src=x onerror=alert(1)&gt; **bold**");
  });

  it("keeps the record of an answered card to one line", () => {
    const html = renderToStaticMarkup(<ApprovalCard item={{ ...item, status: "allowed", by: "user" }} actions={actions} />);
    expect(html).toContain(item.title);
    expect(html).not.toContain("Total the orders");
  });

  it("does not change the card of any other tool", () => {
    const html = renderToStaticMarkup(<ApprovalCard item={{ ...item, tool: "Bash", detail: "rm -rf build" }} actions={actions} />);
    expect(html).toContain('class="approval-detail"');
  });
});

describe("Make a skill from this…", () => {
  const selection = { path: "index.html", selector: "h1", tag: "h1", text: "Sales", html: "<h1>Sales</h1>" } as Selection;
  const draw = (over: Partial<Parameters<typeof MakeSkillDialog>[0]> = {}) =>
    renderToStaticMarkup(
      <MakeSkillDialog hasBackEnd={false} selection={null} sending={false} onStart={async () => {}} onClose={() => {}} {...over} />,
    );

  it("is one entry, used by both of the project's menus and open while the agent works", () => {
    const entry = makeSkillEntry(() => {});
    expect(entry).toMatchObject({ label: "Make a skill from this…" });
    expect("disabled" in entry).toBe(false);
    expect(projectSource.match(/makeSkillEntry\(\(\) => setMakingSkill\(true\)\)/g)?.length).toBe(2);
  });

  it("offers the whole page, and the selected part only when something is selected", () => {
    const plain = draw();
    expect(plain).toContain("The whole page");
    expect(plain).not.toContain("The part I selected");
    expect(plain).toContain("the company and the quarter");
  });

  it("says app for a project with a back end", () => {
    expect(draw({ hasBackEnd: true })).toContain("The whole app");
  });

  it("preselects the selected part", () => {
    const html = draw({ selection });
    expect(html).toMatch(/<input[^>]*checked=""[^>]*\/><span>The part I selected/);
  });
});

describe("editing a skill", () => {
  it("says plainly what is missing or too long", () => {
    expect(skillEditProblem("", "Body")).toContain("when the agent should use it");
    expect(skillEditProblem("x".repeat(601), "Body")).toContain("600");
    expect(skillEditProblem("When", " ")).toBe("Write the instructions.");
    expect(skillEditProblem("When", "x".repeat(12_001))).toContain("12,000");
    expect(skillEditProblem("When", "Body")).toBeNull();
  });
});

describe("the approval card from the event's structured skill", () => {
  const script = "#!/bin/sh\n" + Array.from({ length: 500 }, (_, i) => `echo line ${i} '<b>'`).join("\n");
  const info: SkillApprovalInfo = {
    description: "Use for the weekly report.",
    body: "Run scripts/total.sh, then write the page.",
    files: [
      { path: "scripts/total.sh", size: 2048, script: true, replaces: true },
      { path: "reference/notes.md", size: 300, script: false, replaces: false },
      { path: "scripts/new.py", size: 40, script: true, replaces: false },
    ],
    scripts: [
      { path: "scripts/total.sh", text: script, lines: 501, replaces: true },
      { path: "scripts/new.py", text: "print(1)", lines: 1, replaces: false },
    ],
    removed: ["reference/old.md"],
    kept: Array.from({ length: 15 }, (_, i) => `reference/k${i}.md`),
    kept_scripts: ["scripts/keep.sh"],
  };

  it("marks each written file and gives its size from bytes", () => {
    expect(approvalFileRows(info.files)).toEqual([
      { path: "scripts/total.sh", size: "2.0 KB", marks: ["script", "replaces the one in the skill"] },
      { path: "reference/notes.md", size: "300 B", marks: [] },
      { path: "scripts/new.py", size: "40 B", marks: ["script"] },
    ]);
  });

  it("names the first twelve kept files, then a count, and every kept script", () => {
    expect(keptSentence(info.kept)).toBe(
      `Files kept from the version it replaces: ${info.kept.slice(0, 12).join(", ")} and 3 more.`,
    );
    expect(keptSentence(["a.md"])).toBe("Files kept from the version it replaces: a.md.");
    expect(keptSentence([])).toBeNull();
    expect(keptScriptsSentence(["scripts/keep.sh"])).toBe("This script is already in the skill and stays as it is: scripts/keep.sh.");
    expect(keptScriptsSentence(["a.sh", "b.sh"])).toBe("These scripts are already in the skill and stay as they are: a.sh, b.sh.");
    expect(keptScriptsSentence([])).toBeNull();
  });

  it("says whether a script is new and how long it is", () => {
    expect(scriptFacts({ replaces: false, lines: 1 })).toBe("new · 1 line");
    expect(scriptFacts({ replaces: true, lines: 501 })).toBe("replaces the one in the skill · 501 lines");
  });

  it("draws every part, and every script whole as text", () => {
    const html = renderToStaticMarkup(<SkillApprovalDetail detail="When it is used: ignored" skill={info} />);
    expect(html).toContain("Use for the weekly report.");
    expect(html).not.toContain("ignored");
    expect(html).toContain("Run scripts/total.sh, then write the page.");
    expect(html).toContain("<h4>Removed</h4>");
    expect(html).toContain("reference/old.md");
    expect(html).toContain("and 3 more.");
    expect(html).toContain("scripts/keep.sh");
    expect(html).toContain("Scripts the agent may run");
    expect(html).toContain('aria-label="The script scripts/total.sh"');
    expect(html).toContain('aria-label="The script scripts/new.py"');
    expect(html.match(/echo line \d+ &#x27;&lt;b&gt;&#x27;/g)).toHaveLength(500);
    expect(html).toContain("echo line 499");
    expect(html).toContain("replaces the one in the skill · 501 lines");
    expect(html).toContain("new · 1 line");
    expect(html).not.toContain("<b>");
  });

  it("leaves out Removed and the scripts part when there are none", () => {
    const html = renderToStaticMarkup(
      <SkillApprovalDetail detail="" skill={{ ...info, removed: [], scripts: [], kept: [], kept_scripts: [], files: [] }} />,
    );
    expect(html).not.toContain("Removed");
    expect(html).not.toContain("Scripts the agent may run");
    expect(html).not.toContain("<h4>Files</h4>");
  });

  it("an event without skill keeps today's parsed card", () => {
    const html = renderToStaticMarkup(
      <SkillApprovalDetail detail={"When it is used: When.\n\nInstructions:\nDo it.\n\nFiles:\nreference/a.md (2 KB)"} skill={null} />,
    );
    expect(html).toContain("When.");
    expect(html).toContain("reference/a.md");
    expect(html).not.toContain("Scripts the agent may run");
  });
});
