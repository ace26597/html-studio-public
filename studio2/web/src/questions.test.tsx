import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { projectOptions } from "./effort";
import { effortSummary } from "./effortRow";
import { QuestionCard } from "./QuestionCard";
import {
  answeredCount,
  answersBody,
  answersFrom,
  questionsFrom,
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
    expect(empty).toContain("A few questions before I start");
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
    expect(busy).toContain("Skipping…");
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
    expect(html).toContain("A question before I start");
    expect(html).toContain('placeholder="Your answer"');
  });
});

describe("the switch", () => {
  it("is offered only when the server sends `ask` with the project", () => {
    // The options this page sends carry `ask` only then: an older server refuses an option it does not know.
    expect(projectOptions({ depth: "deep", verify: true })).toEqual({ depth: "deep", verify: true });
    expect(projectOptions({ depth: "deep", verify: true, ask: true })).toEqual({ depth: "deep", verify: true, ask: true });
    expect(projectOptions({ ask: false })).toEqual({ depth: "standard", verify: false, ask: false });
    expect(projectOptions({ ask: "yes" })).toEqual({ depth: "standard", verify: false });
  });

  it("is said by the depth button", () => {
    expect(effortSummary({ depth: "standard", verify: false, ask: true })).toBe("How much to do: Standard, and the agent asks you first");
    expect(effortSummary({ depth: "deep", verify: true, ask: false })).toBe("How much to do: Deep, and its content is verified");
  });
});
