// Save as template: the whole page / app, or one of the project's Office files.

import { useEffect, useId, useState } from "react";
import { api, errorMessage } from "./api";
import { Icon, Spinner } from "./icons";
import { Modal } from "./Modal";
import { saveChoices, saveTemplateBody, type SaveChoice } from "./templates";
import type { Template } from "./types";

interface SaveTemplateDialogProps {
  projectId: string;
  projectName: string;
  hasBackEnd: boolean;
  onSaved: (template: Template) => void;
  onClose: () => void;
}

export function SaveTemplateDialog({ projectId, projectName, hasBackEnd, onSaved, onClose }: SaveTemplateDialogProps) {
  const [name, setName] = useState(projectName === "Untitled" ? "" : projectName);
  const [description, setDescription] = useState("");
  const [choices, setChoices] = useState<SaveChoice[]>(() => saveChoices([], hasBackEnd));
  const [choice, setChoice] = useState("whole");
  const [listError, setListError] = useState<string | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const ids = useId();

  useEffect(() => {
    let cancelled = false;
    api.listFiles(projectId).then(
      (files) => {
        if (!cancelled) setChoices(saveChoices(files, hasBackEnd));
      },
      (cause) => {
        if (!cancelled) setListError(errorMessage(cause));
      },
    );
    return () => {
      cancelled = true;
    };
  }, [projectId, hasBackEnd]);

  const save = async () => {
    if (!name.trim()) {
      setProblem("Give the template a name.");
      return;
    }
    const picked = choices.find((item) => item.value === choice) ?? choices[0];
    setSaving(true);
    setError(null);
    try {
      onSaved(await api.saveTemplate(projectId, saveTemplateBody(name, description, picked)));
    } catch (cause) {
      setError(errorMessage(cause));
      setSaving(false);
    }
  };

  return (
    <Modal title="Save as template" onClose={onClose} busy={saving} wide>
      <form
        className="ds-form"
        onSubmit={(event) => {
          event.preventDefault();
          void save();
        }}
      >
        <label className="field" htmlFor={`${ids}-name`}>
          <span>Name</span>
          <input
            id={`${ids}-name`}
            data-autofocus
            value={name}
            maxLength={80}
            aria-invalid={problem ? true : undefined}
            onChange={(event) => {
              setName(event.target.value);
              setProblem(null);
            }}
          />
        </label>
        <label className="field" htmlFor={`${ids}-description`}>
          <span>What should stay the same when this is reused?</span>
          <textarea
            id={`${ids}-description`}
            className="field-textarea"
            rows={3}
            value={description}
            placeholder="The layout, the colours, the order of the sections…"
            onChange={(event) => setDescription(event.target.value)}
          />
        </label>
        <fieldset className="field save-what">
          <legend>What to save</legend>
          {choices.map((item) => (
            <label key={item.value} className={`save-choice${choice === item.value ? " is-on" : ""}`}>
              <input
                type="radio"
                name={`${ids}-what`}
                value={item.value}
                checked={choice === item.value}
                onChange={() => setChoice(item.value)}
              />
              <span>{item.label}</span>
            </label>
          ))}
          {listError && <p className="ds-note">The project’s files couldn’t be listed, so only the whole project can be saved: {listError}</p>}
        </fieldset>
        <p className="field-hint">
          A copy is saved in the studio. Pages and Office files are used as a pattern to follow; an app is copied as it is.
        </p>
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
          <button type="button" className="btn" disabled={saving} onClick={onClose}>
            Cancel
          </button>
          <button type="submit" className="btn btn-primary" disabled={saving}>
            {saving && <Spinner size={13} />}
            Save
          </button>
        </div>
      </form>
    </Modal>
  );
}
