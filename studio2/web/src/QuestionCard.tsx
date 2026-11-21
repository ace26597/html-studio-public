import { useState } from "react";
import { Icon } from "./icons";
import { Markdown } from "./markdown";
import {
  ANSWER_MAX,
  answersBody,
  cardHeading,
  recordHeadline,
  recordRows,
  sendLabel,
  sendProblem,
  setText,
  skipLabel,
  summaryLabel,
  toggleChoice,
  waitingHint,
  type AnswerState,
  type Draft,
  type Question,
  type QuestionAnswer,
  type QuestionItem,
  type Reference,
} from "./questions";

export interface QuestionActions {
  onAnswer: (item: QuestionItem, body: { answers: QuestionAnswer[] } | { skip: true }) => void;
  answering: Record<string, AnswerState>;
  /** Open a file of the project in the Files panel (a reference that names one). */
  onOpenFile?: (path: string) => void;
}

/** What a plan or a part rests on: an address opens in a new tab, a project file in the Files panel. */
export function References({ items, onOpenFile }: { items: Reference[]; onOpenFile?: (path: string) => void }) {
  if (!items.length) return null;
  return (
    <ul className="question-refs" aria-label="What this rests on">
      {items.map((item, index) => (
        <li key={index} className="question-ref">
          <Icon name={item.url ? "external" : "note"} size={12} className="question-ref-icon" />
          <span className="question-ref-text">
          {item.url ? (
            <a href={item.url} target="_blank" rel="noopener noreferrer">
              {item.label}
            </a>
          ) : item.path && onOpenFile ? (
            <button type="button" className="question-ref-file" onClick={() => onOpenFile(item.path!)}>
              {item.label}
            </button>
          ) : (
            <span>{item.label}</span>
          )}
          {item.note && <span className="question-ref-note"> {item.note}</span>}
          </span>
        </li>
      ))}
    </ul>
  );
}

function QuestionField({
  item,
  question,
  number,
  draft,
  disabled,
  onChange,
}: {
  item: QuestionItem;
  question: Question;
  number: number;
  draft: Draft;
  disabled: boolean;
  onChange: (next: Draft) => void;
}) {
  const now = draft[question.id] ?? { chosen: [], text: "" };
  const name = `${item.questionId}-${question.id}`;
  const many = item.questions.length > 1;
  return (
    <fieldset className="question" disabled={disabled}>
      <legend className="question-text">
        {many && <span className="question-number">{number}.</span>} {question.question}
        {question.multiple && <span className="question-many"> Choose any that apply.</span>}
      </legend>
      {question.choices.length > 0 && (
        <div className="question-choices">
          {question.choices.map((choice) => {
            const on = now.chosen.includes(choice.label);
            return (
              <label key={choice.label} className={`question-choice${on ? " is-on" : ""}`}>
                <input
                  type={question.multiple ? "checkbox" : "radio"}
                  name={name}
                  checked={on}
                  // A plain radio cannot be unticked; a second tap takes the choice back.
                  onClick={() => !question.multiple && on && onChange(toggleChoice(draft, question, choice.label))}
                  onChange={() => (question.multiple || !on) && onChange(toggleChoice(draft, question, choice.label))}
                />
                <span className="question-choice-body">
                  <span className="question-choice-label">
                    {choice.label}
                    {choice.pick && <span className="question-choice-pick">Suggested</span>}
                  </span>
                  {choice.description && <span className="question-choice-note">{choice.description}</span>}
                </span>
              </label>
            );
          })}
        </div>
      )}
      <textarea
        className="field-textarea question-own"
        rows={question.choices.length > 0 ? 1 : 2}
        maxLength={ANSWER_MAX}
        value={now.text}
        aria-label={`${question.question} Your own answer`}
        placeholder={question.choices.length > 0 ? "Or say it in your own words" : "Your answer"}
        onChange={(event) => onChange(setText(draft, question, event.target.value))}
      />
    </fieldset>
  );
}

/** The record a card becomes once it is answered, skipped, or the request has ended. */
export function QuestionRecord({ item, onOpenFile }: { item: QuestionItem; onOpenFile?: (path: string) => void }) {
  const rows = recordRows(item);
  return (
    <div className={`question-record is-${item.status}`}>
      <p className="question-record-head">
        <Icon name={item.status === "answered" ? "check" : "x"} size={13} strokeWidth={2.2} className="question-record-icon" />
        <span>{recordHeadline(item)}</span>
      </p>
      {(item.summary || item.references.length > 0) && (
        <details className="question-record-summary">
          <summary>{summaryLabel(item)}</summary>
          {item.summary && <Markdown text={item.summary} />}
          <References items={item.references} onOpenFile={onOpenFile} />
        </details>
      )}
      {rows.length > 0 && (
        <dl className="question-record-rows">
          {rows.map((row) => (
            <div key={row.id} className="question-record-row">
              <dt>{row.question}</dt>
              <dd className={row.empty ? "is-empty" : undefined}>{row.answer}</dd>
            </div>
          ))}
        </dl>
      )}
    </div>
  );
}

/** The agent asked before starting: choices and a box for the person's own words, then Send or Skip. */
export function QuestionCard({
  item,
  actions,
  startWith,
}: {
  item: QuestionItem;
  actions: QuestionActions;
  /** Answers already filled in (tests draw a part-answered card with it). */
  startWith?: Draft;
}) {
  const [draft, setDraft] = useState<Draft>(startWith ?? {});
  if (item.status !== "waiting") return <QuestionRecord item={item} onOpenFile={actions.onOpenFile} />;
  const state = actions.answering[item.questionId];
  const busy = state?.pending ?? null;
  const problem = sendProblem(item, draft);
  return (
    <section className={`approval question-card is-${item.kind}`} aria-label={`The agent is waiting for you: ${cardHeading(item)}`}>
      <div className="approval-head">
        <Icon name="help" size={15} className="approval-icon" />
        <strong>{cardHeading(item)}</strong>
      </div>
      {item.why && <p className="question-why">{item.why}</p>}
      {item.summary && (
        <div className="question-summary">
          <Markdown text={item.summary} />
        </div>
      )}
      <References items={item.references} onOpenFile={actions.onOpenFile} />
      {item.questions.map((question, index) => (
        <QuestionField
          key={question.id}
          item={item}
          question={question}
          number={index + 1}
          draft={draft}
          disabled={busy !== null}
          onChange={setDraft}
        />
      ))}
      {state?.error && (
        <p className="approval-error" role="alert">
          {state.error}
        </p>
      )}
      <div className="approval-actions">
        <span className="approval-hint">{waitingHint(item, draft)}</span>
        <button
          type="button"
          className="btn btn-small"
          disabled={busy !== null}
          onClick={() => actions.onAnswer(item, { skip: true })}
        >
          {busy === "skip" ? "Telling the agent…" : skipLabel(item)}
        </button>
        <button
          type="button"
          className="btn btn-small btn-primary"
          disabled={busy !== null || problem !== null}
          title={problem ?? undefined}
          onClick={() => actions.onAnswer(item, answersBody(item, draft))}
        >
          {busy === "send" ? "Sending…" : sendLabel(item, draft)}
        </button>
      </div>
    </section>
  );
}
