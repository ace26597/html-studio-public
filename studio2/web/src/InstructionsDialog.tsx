// Project menu → Instructions…: what the person always wants in this project.

import { useId, useRef, useState } from "react";
import { api, errorMessage } from "./api";
import { Icon, Spinner } from "./icons";
import {
  DISCARD_INSTRUCTIONS,
  INSTRUCTIONS_EXAMPLES,
  INSTRUCTIONS_TITLE,
  instructionsChanged,
  instructionsCount,
  instructionsNote,
  instructionsProblem,
  tidyInstructions,
} from "./instructions";
import { Modal } from "./Modal";
import type { Project } from "./types";

interface InstructionsDialogProps {
  projectId: string;
  /** What the project has now. */
  saved: string;
  /** A request is running: it keeps the earlier text. */
  running: boolean;
  onSaved: (project: Project, text: string) => void;
  onClose: () => void;
}

export function InstructionsDialog({ projectId, saved, running, onSaved, onClose }: InstructionsDialogProps) {
  const [text, setText] = useState(saved);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const box = useRef<HTMLTextAreaElement>(null);
  const ids = useId();

  const changed = instructionsChanged(saved, text);
  const problem = instructionsProblem(text);
  const count = instructionsCount(text);

  const close = () => {
    if (!changed || window.confirm(DISCARD_INSTRUCTIONS)) onClose();
  };

  const save = async () => {
    if (!changed || problem) return;
    const next = tidyInstructions(text);
    setSaving(true);
    setError(null);
    try {
      onSaved(await api.patchProject(projectId, { instructions: next }), next);
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
        <label className="field" htmlFor={`${ids}-text`}>
          <span>What should the agent always do here? Write it as you would say it.</span>
          <textarea
            id={`${ids}-text`}
            ref={box}
            data-autofocus
            className="field-textarea instructions-text"
            rows={11}
            value={text}
            placeholder={INSTRUCTIONS_EXAMPLES}
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
            {instructionsNote(running)} What you ask for in a message still comes first.
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
