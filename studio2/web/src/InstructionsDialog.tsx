// Project menu → Instructions…: what the person always wants, in this project or in every project.

import { useId, useRef, useState } from "react";
import { api, errorMessage } from "./api";
import { Icon, Spinner } from "./icons";
import {
  DISCARD_INSTRUCTIONS,
  EVERYWHERE_EXAMPLES,
  INSTRUCTIONS_EXAMPLES,
  INSTRUCTIONS_TITLE,
  PART_LABEL,
  PART_QUESTION,
  instructionsChanged,
  instructionsCount,
  instructionsNote,
  instructionsProblem,
  tidyInstructions,
  type InstructionsPart,
} from "./instructions";
import { Modal } from "./Modal";
import type { Project } from "./types";

interface InstructionsDialogProps {
  projectId: string;
  /** What the project has now. */
  saved: string;
  /** The text for every project, or null when this studio offers none (shared, or an older server). */
  everywhere: string | null;
  /** A request is running: it keeps the earlier text. */
  running: boolean;
  onSaved: (project: Project, text: string) => void;
  onSavedEverywhere: (text: string) => void;
  onClose: () => void;
  /** Which part to open on (tests draw the second one with it). */
  startWith?: InstructionsPart;
}

export function InstructionsDialog({
  projectId,
  saved,
  everywhere,
  running,
  onSaved,
  onSavedEverywhere,
  onClose,
  startWith = "project",
}: InstructionsDialogProps) {
  const [part, setPart] = useState<InstructionsPart>(everywhere === null ? "project" : startWith);
  const kept = part === "everywhere" ? (everywhere ?? "") : saved;
  const [text, setText] = useState(kept);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const box = useRef<HTMLTextAreaElement>(null);
  const ids = useId();

  const changed = instructionsChanged(kept, text);
  const problem = instructionsProblem(text);
  const count = instructionsCount(text);

  const close = () => {
    if (!changed || window.confirm(DISCARD_INSTRUCTIONS)) onClose();
  };

  const show = (next: InstructionsPart) => {
    if (next === part || saving) return;
    if (changed && !window.confirm(DISCARD_INSTRUCTIONS)) return;
    setPart(next);
    setText(next === "everywhere" ? (everywhere ?? "") : saved);
    setError(null);
  };

  const save = async () => {
    if (!changed || problem) return;
    const next = tidyInstructions(text);
    setSaving(true);
    setError(null);
    try {
      if (part === "everywhere") onSavedEverywhere((await api.saveEverywhereInstructions(next)).text);
      else onSaved(await api.patchProject(projectId, { instructions: next }), next);
    } catch (cause) {
      setError(errorMessage(cause));
      setSaving(false);
    }
  };

  return (
    <Modal title={INSTRUCTIONS_TITLE} onClose={close} busy={saving} wide>
      <form
        className="ds-form instructions-form"
        onSubmit={(event) => {
          event.preventDefault();
          void save();
        }}
      >
        {everywhere !== null && (
          <div className="instructions-parts" role="group" aria-label="Which instructions">
            {(["project", "everywhere"] as const).map((each) => (
              <button
                key={each}
                type="button"
                className={`instructions-part${each === part ? " is-on" : ""}`}
                aria-pressed={each === part}
                onClick={() => show(each)}
              >
                {PART_LABEL[each]}
                {(each === "everywhere" ? (everywhere ?? "") : saved).trim() !== "" && <span className="instructions-part-dot" aria-label="has text" />}
              </button>
            ))}
          </div>
        )}
        <label className="field" htmlFor={`${ids}-text`}>
          <span>{PART_QUESTION[part]}</span>
          <textarea
            id={`${ids}-text`}
            ref={box}
            data-autofocus
            className="field-textarea instructions-text"
            rows={11}
            value={text}
            placeholder={part === "everywhere" ? EVERYWHERE_EXAMPLES : INSTRUCTIONS_EXAMPLES}
            aria-invalid={problem ? true : undefined}
            aria-describedby={`${ids}-note`}
            onChange={(event) => {
              setText(event.target.value);
              setError(null);
            }}
          />
        </label>
        <div className="instructions-under">
          <p className="field-hint" id={`${ids}-note`}>
            {instructionsNote(running, part)} What you ask for in a message still comes first.
          </p>
          <span className={`instructions-count${count.over ? " is-over" : ""}`}>{count.text}</span>
        </div>
        {problem && (
          <p className="ds-field-error" role="alert">
            {problem}
          </p>
        )}
        {error && (
          <p className="ds-error" role="alert">
            <Icon name="alert" size={14} /> {error}
          </p>
        )}
        <div className="dialog-actions">
          {text.trim() !== "" && (
            <button
              type="button"
              className="btn instructions-clear"
              disabled={saving}
              onClick={() => {
                setText("");
                box.current?.focus();
              }}
            >
              Clear
            </button>
          )}
          <button type="button" className="btn" disabled={saving} onClick={close}>
            Cancel
          </button>
          <button type="submit" className="btn btn-primary" disabled={saving || !changed || Boolean(problem)}>
            {saving && <Spinner size={13} />}
            Save
          </button>
        </div>
      </form>
    </Modal>
  );
}
