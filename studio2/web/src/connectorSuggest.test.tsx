import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import {
  ageWords,
  answerIsEmpty,
  checkCaution,
  checkLine,
  formFromSuggestion,
  leftOutLine,
  SIGN_IN_NOTE,
  signInCommands,
  suggestedFormNote,
  suggestedHeading,
  suggestionTarget,
  suggestProblem,
} from "./connectorSuggest";
import { DescribeStep, McpServerDialog } from "./McpServersView";
import { emptyMcpForm, mcpInputFromForm, secretRow, validateMcpForm } from "./mcpServerForm";
import { NewProjectDialog, newPageSources } from "./NewProjectDialog";
import type { McpSuggestAnswer, McpSuggestion, PackageCheck } from "./types";

const noop = () => {};
const NOW = Date.parse("2026-11-19T12:00:00Z");
const daysAgo = (n: number) => new Date(NOW - n * 86_400_000).toISOString();

const npm: PackageCheck = {
  kind: "npm",
  package: "@scope/pkg",
  found: true,
  version: "1.4.2",
  description: null,
  published: daysAgo(21),
  first_published: daysAgo(900),
  downloads_week: 41_200,
  repository: null,
};

const packageSuggestion: McpSuggestion = {
  name: "notion",
  label: "Notion",
  description: "Reads Notion pages.",
  transport: "stdio",
  url: null,
  command: "npx",
  args: ["-y", "@scope/pkg"],
  secrets: [{ name: "NOTION_TOKEN", where: "Notion → Settings → Integrations." }],
  why: "You asked for Notion.",
  sign_in: false,
  check: npm,
};

const signInSuggestion: McpSuggestion = {
  name: "linear",
  label: "Linear",
  description: "Linear issues.",
  transport: "http",
  url: "https://mcp.linear.app/mcp",
  command: null,
  args: [],
  secrets: [],
  why: "It signs in with your account.",
  sign_in: true,
  check: { kind: "address", found: true, status: 401 },
};

const answer: McpSuggestAnswer = {
  have: [
    {
      id: "machine:notes",
      name: "notes",
      label: "My notes",
      source: "machine",
      origin: null,
      description: "Already here.",
      state: "needs-auth",
      fix: "claude mcp login notes",
    },
  ],
  suggestions: [packageSuggestion, signInSuggestion],
  left_out: [{ label: "notion-tools", reason: "not on npm" }],
  asked: { connection: "subscription", model: "claude-haiku" },
  note: null,
};

describe("what Suggest needs", () => {
  it("needs words, at most 2,000 characters", () => {
    expect(suggestProblem("  ")).toBe("Say what you want the agent to reach or do.");
    expect(suggestProblem("x".repeat(2001))).toContain("2,000");
    expect(suggestProblem("Read my Notion pages")).toBeNull();
  });
});

describe("a suggestion fills the form", () => {
  it("a package: command, one argument per line, one empty row per key with where it comes from", () => {
    const form = formFromSuggestion(packageSuggestion);
    expect(form).toMatchObject({
      name: "notion",
      label: "Notion",
      transport: "stdio",
      url: "",
      command: "npx",
      args: "-y\n@scope/pkg",
      description: "Reads Notion pages.",
    });
    expect(form.secrets.map((row) => [row.name, row.value, row.stored, row.hint])).toEqual([
      ["NOTION_TOKEN", "", false, "Notion → Settings → Integrations."],
    ]);
  });

  it("an address: the url, no command", () => {
    const form = formFromSuggestion({ ...signInSuggestion, sign_in: false, secrets: [{ name: "Authorization", where: "" }] });
    expect(form.transport).toBe("http");
    expect(form.url).toBe("https://mcp.linear.app/mcp");
    expect(form.command).toBe("");
    expect(form.secrets[0].hint).toBeUndefined();
  });

  it("keeps the rule that a named key needs a value, and sends the form as before", () => {
    const form = formFromSuggestion(packageSuggestion);
    expect(validateMcpForm(form)?.message).toBe("Type a value for “NOTION_TOKEN”, or remove that row.");
    form.secrets[0].value = "secret";
    expect(validateMcpForm(form)).toBeNull();
    expect(mcpInputFromForm(form)).toEqual({
      name: "notion",
      label: "Notion",
      description: "Reads Notion pages.",
      transport: "stdio",
      command: "npx",
      args: ["-y", "@scope/pkg"],
      env: { NOTION_TOKEN: "secret" },
    });
  });

  it("a row made without a hint has none (the existing form is unchanged)", () => {
    expect("hint" in secretRow("A")).toBe(false);
    expect(emptyMcpForm().secrets).toEqual([]);
  });

  it("names its address or command in one line", () => {
    expect(suggestionTarget(packageSuggestion)).toBe("npx -y @scope/pkg");
    expect(suggestionTarget(signInSuggestion)).toBe("https://mcp.linear.app/mcp");
  });
});

describe("what was checked, in one line", () => {
  it("npm: package, version, age and downloads", () => {
    expect(checkLine(npm, NOW)).toBe("On npm: @scope/pkg 1.4.2, published 3 weeks ago, 41,200 downloads last week");
  });

  it("PyPI: no download count", () => {
    expect(checkLine({ ...npm, kind: "pypi", package: "pkg", version: "0.9.0", published: daysAgo(65), downloads_week: null }, NOW)).toBe(
      "On PyPI: pkg 0.9.0, published 2 months ago",
    );
  });

  it("leaves out any part the registry did not give", () => {
    const bare = { ...npm, version: null, description: null, published: null, first_published: null, downloads_week: null, repository: null };
    expect(checkLine(bare, NOW)).toBe("On npm: @scope/pkg");
    expect(checkLine({ ...bare, version: "2.0.0" }, NOW)).toBe("On npm: @scope/pkg 2.0.0");
    expect(checkLine({ ...bare, downloads_week: 0 }, NOW)).toBe("On npm: @scope/pkg, 0 downloads last week");
    expect(checkLine(bare, NOW)).not.toContain("null");
  });

  it("a registry that could not be reached", () => {
    expect(checkLine({ ...npm, found: null }, NOW)).toBe("npm could not be reached, so this package was not checked");
  });

  it("an address", () => {
    expect(checkLine({ kind: "address", found: true, status: 401 }, NOW)).toBe(
      "The address answered and wants a key or a sign-in (HTTP 401)",
    );
    expect(checkLine({ kind: "address", found: true, status: 200 }, NOW)).toBe("The address answered (HTTP 200)");
    expect(checkLine({ kind: "address", found: null, status: null }, NOW)).toBe("Not checked");
    expect(checkLine(null, NOW)).toBe("Not checked");
  });

  it("says ages in days, weeks, months and years", () => {
    expect(ageWords(daysAgo(2), NOW)).toBe("2 days ago");
    expect(ageWords(daysAgo(7), NOW)).toBe("1 week ago");
    expect(ageWords(daysAgo(200), NOW)).toBe("6 months ago");
    expect(ageWords(daysAgo(800), NOW)).toBe("2 years ago");
  });
});

describe("cautions", () => {
  it("none for a well-used package that is not new", () => {
    expect(checkCaution(npm, NOW)).toBeNull();
    expect(checkCaution({ kind: "address", found: true, status: 200 }, NOW)).toBeNull();
  });

  it("few downloads", () => {
    expect(checkCaution({ ...npm, published: daysAgo(400), downloads_week: 312 }, NOW)).toBe(
      "Few people use this package (312 downloads last week). Read its page before you add it.",
    );
  });

  it("anything that could not be checked", () => {
    const words = "This could not be checked. Make sure it is the real one before you add it.";
    expect(checkCaution({ ...npm, found: null }, NOW)).toBe(words);
    expect(checkCaution({ kind: "address", found: null, status: null }, NOW)).toBe(words);
    expect(checkCaution(null, NOW)).toBe(words);
  });

  it("a package whose first version is under 30 days old, whatever its latest version", () => {
    expect(checkCaution({ ...npm, first_published: daysAgo(5), downloads_week: 50_000 }, NOW)).toBe(
      "This package is new: its first version was published 5 days ago. A name a model remembers can belong to someone else, so read its page before you add it.",
    );
    expect(checkCaution({ ...npm, first_published: daysAgo(5), downloads_week: 40 }, NOW)).toContain(
      "few people use it yet (40 downloads last week)",
    );
    // A new latest version of an old package is not "new".
    expect(checkCaution({ ...npm, published: daysAgo(2), first_published: daysAgo(900) }, NOW)).toBeNull();
    // No first date: no "new" caution.
    expect(checkCaution({ ...npm, first_published: null }, NOW)).toBeNull();
  });
});

describe("signing in through a browser", () => {
  it("gives the two commands", () => {
    expect(signInCommands(signInSuggestion)).toEqual([
      "claude mcp add --scope user --transport http linear https://mcp.linear.app/mcp",
      "claude mcp login linear",
    ]);
  });
});

describe("the answer", () => {
  it("headings, left out, the note above the form", () => {
    expect(suggestedHeading(answer.asked)).toBe("Suggested by claude-haiku, from memory");
    expect(leftOutLine(answer.left_out)).toBe("Left out: notion-tools (not on npm)");
    expect(leftOutLine([])).toBeNull();
    expect(suggestedFormNote(packageSuggestion, "claude-haiku", NOW)).toBe(
      "Suggested by claude-haiku from memory. On npm: @scope/pkg 1.4.2, published 3 weeks ago, 41,200 downloads last week. Nothing is added until you press Add.",
    );
    expect(answerIsEmpty({ ...answer, have: [], suggestions: [] })).toBe(true);
    expect(answerIsEmpty(answer)).toBe(false);
  });

  const draw = (shown: McpSuggestAnswer | null, extra: Partial<Parameters<typeof DescribeStep>[0]> = {}) =>
    renderToStaticMarkup(
      <DescribeStep
        text=""
        problem={null}
        asking={false}
        error={null}
        answer={shown}
        now={NOW}
        onText={noop}
        onSuggest={noop}
        onForm={noop}
        onUse={noop}
        {...extra}
      />,
    );

  it("starts with the text box, its example, Suggest and the form", () => {
    const html = draw(null);
    expect(html).toContain("Describe what you need");
    expect(html).toContain("For example: Read and search the pages in my Notion workspace");
    expect(html).toContain(">Suggest</button>");
    expect(html).toContain("Fill in the form yourself");
  });

  it("says it is asking", () => {
    expect(draw(null, { asking: true })).toContain("Asking the model…");
  });

  it("shows the library's own first, then the suggestions with their checks", () => {
    const html = draw(answer);
    expect(html.indexOf("Already in your library")).toBeLessThan(html.indexOf("Suggested by claude-haiku, from memory"));
    expect(html).toContain("My notes");
    expect(html).toContain("On this computer");
    expect(html).toContain("claude mcp login notes");
    expect(html).toContain("npx -y @scope/pkg");
    expect(html).toContain("On npm: @scope/pkg 1.4.2");
    expect(html).toContain("NOTION_TOKEN");
    expect(html).toContain("Notion → Settings → Integrations.");
    expect(html).toContain("Left out: notion-tools (not on npm)");
  });

  it("a connector that signs in shows the commands instead of Use this", () => {
    const html = draw({ ...answer, suggestions: [signInSuggestion] });
    expect(html).toContain(SIGN_IN_NOTE);
    expect(html).toContain("claude mcp add --scope user --transport http linear https://mcp.linear.app/mcp");
    expect(html).not.toContain("Use this");
    expect(draw({ ...answer, suggestions: [packageSuggestion] })).toContain("Use this");
  });

  it("shows the note, and says so plainly when there is nothing", () => {
    const html = draw({ have: [], suggestions: [], left_out: [], asked: null, note: "This connection has no small model." });
    expect(html).toContain("This connection has no small model.");
    expect(html).toContain("Nothing was suggested, and nothing in your library matches.");
  });

  it("a caution is shown on the card", () => {
    const html = draw({ ...answer, suggestions: [{ ...packageSuggestion, check: { ...npm, downloads_week: 12, published: daysAgo(300) } }] });
    expect(html).toContain("Few people use this package (12 downloads last week).");
  });

  it("adding opens at the describe step; editing opens the form as before", () => {
    expect(renderToStaticMarkup(<McpServerDialog server={null} onSaved={noop} onClose={noop} />)).toContain("Describe what you need");
    const server = {
      id: "mcp_1",
      name: "docs",
      label: null,
      transport: "http",
      url: "https://example.com/mcp",
      command: null,
      args: [],
      secret_names: [],
      source: "yours",
      description: null,
      last_test: null,
    };
    const edit = renderToStaticMarkup(<McpServerDialog server={server} onSaved={noop} onClose={noop} />);
    expect(edit).not.toContain("Describe what you need");
    expect(edit).toContain("Web address");
  });
});

describe("the New page dialog's fourth choice", () => {
  const props = { creating: false, createError: null, onCreate: noop, onAttach: noop, onClose: noop };

  it("offers A connector after A skill, unless it cannot", () => {
    expect(newPageSources(true, true).map((item) => item.label)).toEqual([
      "A template or a blank page",
      "An app on this computer",
      "A skill",
      "A connector",
    ]);
    expect(newPageSources(false, false).map((item) => item.value)).toEqual(["template", "folder"]);
    expect(renderToStaticMarkup(<NewProjectDialog {...props} onSkill={noop} onConnector={noop} />)).toContain(">A connector</button>");
    expect(renderToStaticMarkup(<NewProjectDialog {...props} onSkill={noop} />)).not.toContain(">A connector</button>");
  });

  it("says what a connector is and offers Describe what you need", () => {
    const html = renderToStaticMarkup(<NewProjectDialog {...props} onConnector={noop} initialSource="connector" />);
    expect(html).toContain("A connector lets the agent reach a service or tool outside the project");
    expect(html).toContain("a model suggests which connector to add");
    expect(html).toContain("Describe what you need");
    expect(html).not.toContain(">Create<");
  });
});
