import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { projectOptions } from "./effort";
import { DepthPicker, InvolvePicker, InvolveSlider, PageChecks, VerifyToggle } from "./EffortControl";
import { QuestionCard } from "./QuestionCard";
import {
  INVOLVE_STOPS,
  answeredCount,
  answersBody,
  answersFrom,
  cardHeading,
  involveStop,
  questionsFrom,
  referencesFrom,
  skipLabel,
  recordHeadline,
  recordRows,
  sendLabel,
  sendProblem,
  setText,
  toggleChoice,
  waitingHint,
  type Draft,
  type QuestionItem,
} from "./questions";
import { initialThread, latestActivity, reduceEvents, waitingOnYou, waitingQuestions } from "./thread";
import type { StudioEvent } from "./types";

type Loose = { type: StudioEvent["type"]; turn_id?: string | null } & Record<string, unknown>;
const log = (...events: Loose[]): StudioEvent[] =>
  events.map(
    (event, index) =>
      ({ seq: index + 1, ts: new Date(Date.UTC(2026, 10, 20, 9, 0, index)).toISOString(), turn_id: null, ...event }) as unknown as StudioEvent,
  );

const T = "t_a";
const ASKED = {
  type: "question_request" as const,
  turn_id: T,
  question_id: "k_1",
  why: "The request does not say who reads it.",
  questions: [
    { id: "q1", question: "Who is this for?", multiple: false, choices: [{ label: "The board", description: "Short, figures first." }, { label: "The team", description: "" }] },
    { id: "q2", question: "Which regions?", multiple: true, choices: [{ label: "North" }, { label: "South" }, { label: "East" }] },
    { id: "q3", question: "Anything it must include?", multiple: false, choices: [] },
  ],
};
const started = { type: "turn_started" as const, turn_id: T, text: "Make a report", selection: null };

function waitingItem(): QuestionItem {
  return waitingQuestions(reduceEvents(initialThread, log(started, ASKED)))[0];
}

describe("a question from the agent in the thread", () => {
  it("waits on the person and says so", () => {
    const state = reduceEvents(initialThread, log(started, ASKED));
    const item = waitingQuestions(state)[0];
    expect(item.status).toBe("waiting");
    expect(item.questions.map((q) => [q.id, q.multiple, q.choices.length])).toEqual([["q1", false, 2], ["q2", true, 3], ["q3", false, 0]]);
    expect(item.why).toBe("The request does not say who reads it.");
    expect(waitingOnYou(state)).toBe(1);
    expect(latestActivity(state)).toBe("Waiting for your answer");
    // The same event twice (a replay) is one card.
    expect(waitingQuestions(reduceEvents(state, log(ASKED))).length).toBe(1);
  });

  it("becomes a record when it is answered, skipped, or the request ends first", () => {
    const answers = [{ id: "q1", chosen: ["The board"], text: "" }, { id: "q2", chosen: ["North", "East"], text: "and the islands" }, { id: "q3", chosen: [], text: "" }];
    const answered = reduceEvents(initialThread, log(started, ASKED, { type: "question_resolved", turn_id: T, question_id: "k_1", by: "user", skipped: false, answers }));
    expect(waitingOnYou(answered)).toBe(0);
    const item = (answered.entries[0] as { items: QuestionItem[] }).items.find((i) => i.type === "question")!;
    expect(recordHeadline(item)).toBe("You answered 2 of 3 questions");
    expect(recordRows(item)).toEqual([
      { id: "q1", question: "Who is this for?", answer: "The board", empty: false },
      { id: "q2", question: "Which regions?", answer: "North · East · and the islands", empty: false },
      { id: "q3", question: "Anything it must include?", answer: "No answer", empty: true },
    ]);
    const html = renderToStaticMarkup(<QuestionCard item={item} actions={{ onAnswer: () => {}, answering: {} }} />);
    expect(html).toContain("You answered 2 of 3 questions");
    expect(html).toContain("No answer");
    expect(html).not.toContain("<textarea");

    const skipped = reduceEvents(initialThread, log(started, ASKED, { type: "question_resolved", turn_id: T, question_id: "k_1", by: "user", skipped: true, answers: [] }));
    const skippedItem = (skipped.entries[0] as { items: QuestionItem[] }).items.find((i) => i.type === "question")!;
    expect(recordHeadline(skippedItem)).toBe("You skipped the questions: the agent decided itself");
    expect(recordRows(skippedItem)).toEqual([]);

    // No `question_resolved` in the log (an older or cut log): the end of the request closes the card.
    const ended = reduceEvents(initialThread, log(started, ASKED, { type: "turn_finished", turn_id: T, status: "stopped", usage: {} }));
    const endedItem = (ended.entries[0] as { items: QuestionItem[] }).items.find((i) => i.type === "question")!;
    expect([endedItem.status, endedItem.by]).toEqual(["skipped", "turn_end"]);
    expect(recordHeadline(endedItem)).toBe("Not answered: the request ended first");
  });
});

describe("answering on the card", () => {
  it("takes one choice, or several where the question allows it, and the person's own words", () => {
    const item = waitingItem();
    const [q1, q2, q3] = item.questions;
    let draft: Draft = {};
    expect(sendProblem(item, draft)).toBe("Answer at least one question, or skip them.");
    draft = toggleChoice(draft, q1, "The board");
    draft = toggleChoice(draft, q1, "The team"); // one at a time
    expect(draft.q1.chosen).toEqual(["The team"]);
    expect(toggleChoice(draft, q1, "The team").q1.chosen).toEqual([]); // a second tap takes it back
    draft = toggleChoice(toggleChoice(draft, q2, "East"), q2, "North");
    expect(draft.q2.chosen).toEqual(["North", "East"]); // the card's order
    expect(toggleChoice(draft, q2, "East").q2.chosen).toEqual(["North"]);
    expect(answeredCount(item, draft)).toBe(2);
    expect(sendLabel(item, draft)).toBe("Send 2 of 3 answers");
    expect(waitingHint(item, draft)).toBe("The agent is waiting for you. It decides the one you leave empty.");
    draft = setText(draft, q3, "   ");
    expect(answeredCount(item, draft)).toBe(2); // spaces are no answer
    draft = setText(draft, q3, "  A summary table ");
    expect(sendLabel(item, draft)).toBe("Send answers");
    expect(sendProblem(item, draft)).toBeNull();
    expect(answersBody(item, draft)).toEqual({
      answers: [
        { id: "q1", chosen: ["The team"], text: "" },
        { id: "q2", chosen: ["North", "East"], text: "" },
        { id: "q3", chosen: [], text: "A summary table" },
      ],
    });
    expect(sendProblem(item, setText(draft, q3, "x".repeat(2001)))).toContain("2,000");
    // Only what was answered is sent.
    expect(answersBody(item, toggleChoice({}, q1, "The board")).answers).toEqual([{ id: "q1", chosen: ["The board"], text: "" }]);
  });

  it("draws the choices, a box for the person's own words, and Send off until something is answered", () => {
    const item = waitingItem();
    const empty = renderToStaticMarkup(<QuestionCard item={item} actions={{ onAnswer: () => {}, answering: {} }} />);
    expect(empty).toContain("A few questions for you");
    expect(empty).toContain("The request does not say who reads it.");
    expect(empty).toContain("Short, figures first.");
    expect(empty.match(/type="radio"/g)?.length).toBe(2);
    expect(empty.match(/type="checkbox"/g)?.length).toBe(3);
    expect(empty.match(/<textarea/g)?.length).toBe(3);
    expect(empty).toContain("Choose any that apply.");
    expect(empty).toContain("Or say it in your own words");
    expect(empty).toContain("Skip, decide for me");
    expect(empty).toMatch(/<button[^>]*btn-primary[^>]*disabled=""[^>]*>Send answers<\/button>/);

    const filled = renderToStaticMarkup(
      <QuestionCard item={item} actions={{ onAnswer: () => {}, answering: {} }} startWith={toggleChoice({}, item.questions[0], "The board")} />,
    );
    expect(filled).toContain("Send 1 of 3 answers");
    expect(filled).not.toMatch(/<button[^>]*btn-primary[^>]*disabled=""/);
    expect(filled).toContain('class="question-choice is-on"');

    const busy = renderToStaticMarkup(
      <QuestionCard item={item} actions={{ onAnswer: () => {}, answering: { k_1: { pending: "skip", error: null } } }} />,
    );
    expect(busy).toContain("Telling the agent…");
    const failed = renderToStaticMarkup(
      <QuestionCard item={item} actions={{ onAnswer: () => {}, answering: { k_1: { pending: null, error: "That question is no longer waiting for an answer." } } }} />,
    );
    expect(failed).toContain('role="alert"');
  });

  it("reads odd events without failing", () => {
    expect(questionsFrom(null)).toEqual([]);
    expect(questionsFrom([{ question: "Only text" }, { id: "q9", question: "" }, "x"])).toEqual([
      { id: "q1", question: "Only text", multiple: false, choices: [] },
    ]);
    expect(answersFrom([{ id: "q1", chosen: ["A", 3, ""], text: 5 }, null])).toEqual([{ id: "q1", chosen: ["A"], text: "" }]);
    const one: QuestionItem = { ...waitingItem(), questions: [waitingItem().questions[2]] };
    expect(sendLabel(one, {})).toBe("Send answer");
    const html = renderToStaticMarkup(<QuestionCard item={one} actions={{ onAnswer: () => {}, answering: {} }} />);
    expect(html).toContain("A question for you");
    expect(html).toContain('placeholder="Your answer"');
  });
});

describe("a plan and a check-in", () => {
  const PLAN = {
    type: "question_request" as const,
    turn_id: T,
    question_id: "k_2",
    kind: "plan",
    title: "",
    why: "So the page is built once.",
    summary: "1. **Headline**: the total\n2. **By region**: a bar chart",
    references: [
      { label: "Q3 figures", path: "data/q3.csv", note: "the four regions" },
      { label: "A bulletin", url: "https://example.org/b", note: "" },
      { label: "A script", url: "javascript:alert(1)", note: "" },
      { label: "", url: "https://example.org/none" },
    ],
    questions: [{ id: "q1", question: "Go ahead with this plan?", multiple: false, choices: [{ label: "Go ahead" }, { label: "Change something" }] }],
  };
  const item = (extra: Record<string, unknown> = {}) =>
    waitingQuestions(reduceEvents(initialThread, log(started, { ...PLAN, ...extra })))[0];

  it("shows the plan, what it rests on, and a button that says what going on alone means", () => {
    const plan = item();
    expect([plan.kind, cardHeading(plan), skipLabel(plan)]).toEqual(["plan", "The plan, before I build", "Go ahead, no more questions"]);
    expect(plan.references).toEqual([
      { label: "Q3 figures", note: "the four regions", path: "data/q3.csv" },
      { label: "A bulletin", note: "", url: "https://example.org/b" },
      { label: "A script", note: "" }, // an address that is not http(s) is only words
    ]);
    const opened: string[] = [];
    const html = renderToStaticMarkup(
      <QuestionCard item={plan} actions={{ onAnswer: () => {}, answering: {}, onOpenFile: (path) => opened.push(path) }} />,
    );
    expect(html).toContain("The plan, before I build");
    expect(html).toContain("<strong>Headline</strong>");
    expect(html).toContain('href="https://example.org/b"');
    expect(html).toContain('rel="noopener noreferrer"');
    expect(html).toMatch(/<button[^>]*question-ref-file[^>]*>Q3 figures<\/button>/);
    expect(html).not.toContain("javascript:");
    expect(html).toContain("Go ahead, no more questions");
    // Without a Files panel to open it in, a project file is named and not a button.
    const plain = renderToStaticMarkup(<QuestionCard item={plan} actions={{ onAnswer: () => {}, answering: {} }} />);
    expect(plain).not.toContain("question-ref-file");
    expect(plain).toContain("Q3 figures");
  });

  it("names the part of a check-in, and keeps the summary on the record", () => {
    const part = item({ kind: "checkpoint", title: "Sales by region" });
    expect([cardHeading(part), skipLabel(part)]).toEqual(["Check-in: Sales by region", "Finish it without me"]);
    expect(cardHeading(item({ kind: "checkpoint", title: "" }))).toBe("Check-in");
    expect(item({ kind: "poll" }).kind).toBe("questions");
    const resolved = (extra: Record<string, unknown>, end: Record<string, unknown>) => {
      const state = reduceEvents(initialThread, log(started, { ...PLAN, ...extra }, { type: "question_resolved", turn_id: T, question_id: "k_2", ...end }));
      return (state.entries[0] as { items: QuestionItem[] }).items.find((i) => i.type === "question")!;
    };
    const agreed = resolved({}, { by: "user", skipped: false, answers: [{ id: "q1", chosen: ["Go ahead"], text: "" }] });
    expect(recordHeadline(agreed)).toBe("You answered on the plan");
    const html = renderToStaticMarkup(<QuestionCard item={agreed} actions={{ onAnswer: () => {}, answering: {} }} />);
    expect(html).toContain("<summary>The plan</summary>");
    expect(html).toContain("<strong>By region</strong>");
    expect(recordHeadline(resolved({}, { by: "user", skipped: true, answers: [] }))).toBe("You told the agent to go ahead by itself");
    expect(recordHeadline(resolved({ kind: "checkpoint", title: "Sales by region" }, { by: "user", skipped: true, answers: [] }))).toBe(
      "You told the agent to finish without you",
    );
    expect(recordHeadline(resolved({ kind: "checkpoint", title: "Sales by region" }, { by: "user", skipped: false, answers: [{ id: "q1", chosen: ["Go ahead"], text: "" }] }))).toBe(
      "You answered on Sales by region",
    );
    expect(referencesFrom("nope")).toEqual([]);
  });
});

describe("the scale", () => {
  it("has five stops, each with a name and a line saying what the agent does", () => {
    expect(INVOLVE_STOPS.map((stop) => stop.value)).toEqual([0, 25, 50, 75, 100]);
    expect(INVOLVE_STOPS.map((stop) => stop.name)).toEqual(["On its own", "Only if stuck", "Ask first", "Agree the plan", "Step by step"]);
    expect(INVOLVE_STOPS.every((stop) => stop.line.length > 30)).toBe(true);
    expect(involveStop(75).name).toBe("Agree the plan");
    expect(involveStop(60).value).toBe(0); // not a stop
    expect(involveStop(undefined).value).toBe(0);
    const html = renderToStaticMarkup(<InvolveSlider value={75} onChange={() => {}} />);
    expect(html).toContain("Involve me");
    expect(html).toContain("Agree the plan · 75%");
    expect(html).toMatch(/type="range"[^>]*min="0"[^>]*max="100"[^>]*step="25"/);
    expect(html).toContain('aria-valuetext="Agree the plan, 75 percent"');
    expect(html).toContain("waits for your go-ahead");
  });

  it("is offered only when the server sends `involve` with the project", () => {
    // The options this page sends carry `involve` only then: an older server refuses an option it does not know.
    expect(projectOptions({ depth: "deep", verify: true })).toEqual({ depth: "deep", verify: true });
    expect(projectOptions({ depth: "deep", verify: true, involve: 75 })).toEqual({ depth: "deep", verify: true, involve: 75 });
    expect(projectOptions({ involve: 0 })).toEqual({ depth: "standard", verify: false, involve: 0 });
    expect(projectOptions({ involve: 60 })).toEqual({ depth: "standard", verify: false });
    expect(projectOptions({ involve: "50" })).toEqual({ depth: "standard", verify: false });
    expect(projectOptions({ ask: true })).toEqual({ depth: "standard", verify: false });
  });

  it("is one named setting in the strip under the composer, beside depth and verify", () => {
    const strip = (options: Parameters<typeof projectOptions>[0]) => {
      const kept = projectOptions(options);
      return renderToStaticMarkup(
        <>
          <DepthPicker options={kept} onChange={() => {}} />
          <VerifyToggle options={kept} onChange={() => {}} />
          <InvolvePicker options={kept} onChange={() => {}} />
        </>,
      );
    };
    const html = strip({ depth: "deep", verify: true, involve: 75 });
    expect(html).toContain('aria-label="Depth: Deep"');
    expect(html).toMatch(/aria-label="Verify the content after each request"[^>]*aria-pressed="true"/);
    expect(html).toContain('aria-label="Involve: 75%"');
    expect(html).toContain('data-tip="Agree the plan: Asks first');
    expect(html).toContain('<span class="opt-k">Involve</span><span class="opt-v">75%</span>');
    expect(html).toContain('class="opt-btn is-on"');
    // At 0 it is still there, and not marked.
    const off = strip({ involve: 0 });
    expect(off).toContain('aria-label="Involve: 0%"');
    expect(off).toMatch(/aria-pressed="false"/);
    expect(off).not.toContain("is-on");
    // A server that does not know the scale: no such setting.
    expect(strip({ depth: "standard", verify: false })).not.toContain("Involve");
  });

  it("leaves the two things to do now beside the other actions, not among the settings", () => {
    const html = renderToStaticMarkup(<PageChecks onVerifyPage={() => {}} onPolishPage={() => {}} />);
    expect(html).toContain('aria-label="Check this page now"');
    expect(html).toContain('aria-haspopup="menu"');
  });
});
