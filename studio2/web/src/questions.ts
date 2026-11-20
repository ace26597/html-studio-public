/** Questions the agent asks before it works (CONTRACT.md, "Questions before the work"): the card's item and its answers. */

export const ANSWER_MAX = 2000;
/** The switch's row in the depth menu: its name says what it does. */
export const ASK_LABEL = "Ask me first when unsure";
export const ASK_HINT_ON = "on";
export const ASK_HINT_OFF = "off";

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
  why: string;
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

/** A resolved card's first line. */
export function recordHeadline(item: QuestionItem): string {
  if (item.status === "skipped")
    return item.by === "turn_end"
      ? "Not answered: the request ended first"
      : `You skipped ${item.questions.length === 1 ? "the question" : "the questions"}: the agent decided itself`;
  const given = item.answers.filter((a) => a.chosen.length > 0 || a.text).length;
  return item.questions.length === 1
    ? "You answered"
    : `You answered ${given} of ${item.questions.length} questions`;
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
