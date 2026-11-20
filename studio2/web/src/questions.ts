/** The agent's cards (CONTRACT.md, "How much to involve the person"): the scale, the card's item and its answers. */

export const ANSWER_MAX = 2000;

/** The five stops of "Involve me". `line` is what the menu says under the slider. */
export const INVOLVE_STOPS: { value: number; name: string; line: string }[] = [
  { value: 0, name: "On its own", line: "Never asks. It decides what you did not say and gets on with it." },
  { value: 25, name: "Only if stuck", line: "Works on its own and asks only when it cannot sensibly go on without you." },
  { value: 50, name: "Ask first", line: "Asks before it starts when your answer would change the result, then builds on its own." },
  { value: 75, name: "Agree the plan", line: "Asks first, then shows you its plan and waits for your go-ahead before it builds." },
  { value: 100, name: "Step by step", line: "Plan first, then a check-in after each part: what it made, what it found and its sources." },
];

/** The stop a value stands for; anything odd reads as the first. */
export function involveStop(value: unknown): (typeof INVOLVE_STOPS)[number] {
  return INVOLVE_STOPS.find((stop) => stop.value === value) ?? INVOLVE_STOPS[0];
}

export type CardKind = "questions" | "plan" | "checkpoint";

/** What a plan or a part rests on: an address, a file in the project, or only words. */
export interface Reference {
  label: string;
  note: string;
  url?: string;
  path?: string;
}

export interface QuestionChoice {
  label: string;
  description: string;
}

export interface Question {
  id: string;
  question: string;
  multiple: boolean;
  choices: QuestionChoice[];
}

export interface QuestionAnswer {
  id: string;
  chosen: string[];
  text: string;
}

export type QuestionStatus = "waiting" | "answered" | "skipped";

/** The agent asked the person something (`question_request` … `question_resolved`). */
export interface QuestionItem {
  type: "question";
  key: string;
  questionId: string;
  turnId: string;
  kind: CardKind;
  /** A check-in's part ("Sales by region"). */
  title: string;
  why: string;
  /** The plan, or what was made and found, as Markdown. */
  summary: string;
  references: Reference[];
  questions: Question[];
  status: QuestionStatus;
  /** Who closed it: `user`, or `turn_end` when the request ended first. Null while waiting. */
  by: "user" | "turn_end" | null;
  answers: QuestionAnswer[];
  seq: number;
}

/** What the person has filled in on a waiting card, by question id. */
export type Draft = Record<string, { chosen: string[]; text: string }>;

/** The card's side of an answer while it is being sent. */
export interface AnswerState {
  pending: "send" | "skip" | null;
  error: string | null;
}

const text = (value: unknown): string => (typeof value === "string" ? value : "");
const rows = (value: unknown): Record<string, unknown>[] =>
  (Array.isArray(value) ? value : []).filter((r): r is Record<string, unknown> => !!r && typeof r === "object");

/** The `questions` of a `question_request` event, anything odd left out. */
export function questionsFrom(raw: unknown): Question[] {
  return rows(raw)
    .map((q, index) => ({
      id: text(q.id) || `q${index + 1}`,
      question: text(q.question),
      multiple: q.multiple === true,
      choices: rows(q.choices)
        .map((c) => ({ label: text(c.label), description: text(c.description) }))
        .filter((c) => c.label),
    }))
    .filter((q) => q.question);
}

/** The `answers` of a `question_resolved` event. */
export function answersFrom(raw: unknown): QuestionAnswer[] {
  return rows(raw).map((a) => ({
    id: text(a.id),
    chosen: (Array.isArray(a.chosen) ? a.chosen : []).filter((c): c is string => typeof c === "string" && c !== ""),
    text: text(a.text),
  }));
}

/** A choice was tapped: one at a time, or on and off when the question takes several. */
export function toggleChoice(draft: Draft, question: Question, label: string): Draft {
  const now = draft[question.id] ?? { chosen: [], text: "" };
  const has = now.chosen.includes(label);
  const chosen = question.multiple
    ? question.choices.map((c) => c.label).filter((l) => (l === label ? !has : now.chosen.includes(l)))
    : has
      ? []
      : [label];
  return { ...draft, [question.id]: { ...now, chosen } };
}

export function setText(draft: Draft, question: Question, value: string): Draft {
  const now = draft[question.id] ?? { chosen: [], text: "" };
  return { ...draft, [question.id]: { ...now, text: value } };
}

function isAnswered(entry: { chosen: string[]; text: string } | undefined): boolean {
  return !!entry && (entry.chosen.length > 0 || entry.text.trim() !== "");
}

/** How many of the card's questions have an answer so far. */
export function answeredCount(item: QuestionItem, draft: Draft): number {
  return item.questions.filter((q) => isAnswered(draft[q.id])).length;
}

/** Why Send is off, or null when the answers can go. */
export function sendProblem(item: QuestionItem, draft: Draft): string | null {
  const long = item.questions.find((q) => Array.from((draft[q.id]?.text ?? "").trim()).length > ANSWER_MAX);
  if (long) return `An answer can be ${ANSWER_MAX.toLocaleString("en-US")} characters at most.`;
  if (answeredCount(item, draft) === 0) return "Answer at least one question, or skip them.";
  return null;
}

/** The body of the answer request: only the questions that were answered. */
export function answersBody(item: QuestionItem, draft: Draft): { answers: QuestionAnswer[] } {
  return {
    answers: item.questions
      .filter((q) => isAnswered(draft[q.id]))
      .map((q) => ({ id: q.id, chosen: draft[q.id].chosen, text: draft[q.id].text.trim() })),
  };
}

/** The button under a waiting card: "Send answer", "Send 2 answers", or how many are still open. */
export function sendLabel(item: QuestionItem, draft: Draft): string {
  const count = answeredCount(item, draft);
  if (item.questions.length === 1) return "Send answer";
  return count > 0 && count < item.questions.length
    ? `Send ${count} of ${item.questions.length} answers`
    : "Send answers";
}

/** One line under the questions while they wait. */
export function waitingHint(item: QuestionItem, draft: Draft): string {
  const open = item.questions.length - answeredCount(item, draft);
  if (item.questions.length > 1 && open > 0 && open < item.questions.length)
    return `The agent is waiting for you. It decides the ${open === 1 ? "one" : open} you leave empty.`;
  return "The agent is waiting for you.";
}

/** The `references` of a `question_request` event: an address only when it is http(s). */
export function referencesFrom(raw: unknown): Reference[] {
  return rows(raw)
    .map((r) => {
      const url = text(r.url);
      const safe = /^https?:\/\//i.test(url) ? url : "";
      const path = safe ? "" : text(r.path);
      return { label: text(r.label), note: text(r.note), ...(safe ? { url: safe } : {}), ...(path ? { path } : {}) };
    })
    .filter((r) => r.label);
}

export function cardKind(raw: unknown): CardKind {
  return raw === "plan" || raw === "checkpoint" ? raw : "questions";
}

/** A waiting card's heading. */
export function cardHeading(item: QuestionItem): string {
  if (item.kind === "plan") return "The plan, before I build";
  if (item.kind === "checkpoint") return item.title ? `Check-in: ${item.title}` : "Check-in";
  return item.questions.length === 1 ? "A question for you" : "A few questions for you";
}

/** The second button: it sends the skip, which ends the asking for the rest of the request. */
export function skipLabel(item: QuestionItem): string {
  if (item.kind === "plan") return "Go ahead, no more questions";
  if (item.kind === "checkpoint") return "Finish it without me";
  return "Skip, decide for me";
}

/** What the summary is called on a card that has become a record. */
export function summaryLabel(item: QuestionItem): string {
  if (item.kind === "plan") return "The plan";
  if (item.kind === "checkpoint") return item.title || "The part";
  return "What the agent said";
}

/** A resolved card's first line. */
export function recordHeadline(item: QuestionItem): string {
  if (item.status === "skipped") {
    if (item.by === "turn_end") return "Not answered: the request ended first";
    if (item.kind === "plan") return "You told the agent to go ahead by itself";
    if (item.kind === "checkpoint") return "You told the agent to finish without you";
    return `You skipped ${item.questions.length === 1 ? "the question" : "the questions"}: the agent decided itself`;
  }
  const given = item.answers.filter((a) => a.chosen.length > 0 || a.text).length;
  const what = item.kind === "plan" ? " on the plan" : item.kind === "checkpoint" ? ` on ${item.title || "this part"}` : "";
  return item.questions.length === 1 ? `You answered${what}` : `You answered ${given} of ${item.questions.length} questions${what}`;
}

/** A resolved card's rows: each question with what was answered ("No answer" for one left empty). */
export function recordRows(item: QuestionItem): { id: string; question: string; answer: string; empty: boolean }[] {
  if (item.status !== "answered") return [];
  return item.questions.map((q) => {
    const answer = item.answers.find((a) => a.id === q.id);
    const parts = [...(answer?.chosen ?? []), ...(answer?.text ? [answer.text] : [])];
    return { id: q.id, question: q.question, answer: parts.join(" · ") || "No answer", empty: parts.length === 0 };
  });
}
