// One of the person's own skills: read it, and change when it is used and what it says.

import { useEffect, useId, useState } from "react";
import { api, errorMessage } from "./api";
import { formatBytes } from "./format";
import { Icon, Spinner } from "./icons";
import { Modal } from "./Modal";
import type { SkillDetail } from "./types";
import { useToast } from "./ui";

/** The contract's limits for `PUT /api/skills/{name}`. */
const MAX_DESCRIPTION = 600;
const MAX_BODY = 12_000;

export function skillEditProblem(description: string, body: string): string | null {
  if (!description.trim()) return "Say in one line when the agent should use it.";
  if (description.trim().length > MAX_DESCRIPTION) return `“When it is used” can be at most ${MAX_DESCRIPTION} characters.`;
  if (!body.trim()) return "Write the instructions.";
  if (body.length > MAX_BODY) return `The instructions can be at most ${MAX_BODY.toLocaleString("en-US")} characters.`;
  return null;
}

/** The skill as the dialog shows it when it is not being edited. */
export function SkillReading({ skill }: { skill: SkillDetail }) {
  const files = skill.files.filter((file) => file.path !== "SKILL.md");
  return (
    <div className="skill-reading">
      <section>
        <h3>When it is used</h3>
        <p>{skill.description}</p>
      </section>
      <section>
        <h3>Instructions</h3>
        <pre className="skill-text" tabIndex={0} aria-label="The skill’s instructions">
          {skill.body}
        </pre>
      </section>
      {files.length > 0 && (
        <section>
          <h3>Files</h3>
          <ul className="skill-files">
            {files.map((file) => (
              <li key={file.path}>
                <span className="skill-file-path">{file.path}</span>
                <span className="skill-file-size">{formatBytes(file.size)}</span>
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}

export function SkillOpenDialog({
  name,
  onClose,
  onChanged,
}: {
  name: string;
  onClose: () => void;
  onChanged: () => void;
}) {
  const toast = useToast();
  const [skill, setSkill] = useState<SkillDetail | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [editing, setEditing] = useState(false);
  const [description, setDescription] = useState("");
  const [body, setBody] = useState("");
  const [problem, setProblem] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const ids = useId();

  useEffect(() => {
    const controller = new AbortController();
    api.getSkill(name, controller.signal).then(setSkill, (cause) => {
      if ((cause as Error)?.name !== "AbortError") setLoadError(errorMessage(cause));
    });
    return () => controller.abort();
  }, [name]);

  const startEditing = (current: SkillDetail) => {
    setDescription(current.description);
    setBody(current.body);
    setProblem(null);
    setError(null);
    setEditing(true);
  };

  const save = async (current: SkillDetail) => {
    const issue = skillEditProblem(description, body);
    if (issue) {
      setProblem(issue);
      return;
    }
    const change: { description?: string; body?: string } = {};
    if (description.trim() !== current.description) change.description = description.trim();
    if (body !== current.body) change.body = body;
    if (!change.description && change.body === undefined) {
      setEditing(false);
      return;
    }
    setSaving(true);
    setError(null);
    try {
      setSkill(await api.updateSkill(name, change));
      setEditing(false);
      toast({ message: `Saved the skill “${name}”`, kind: "success" });
      onChanged();
    } catch (cause) {
      setError(errorMessage(cause));
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal title={skill?.label || name} onClose={onClose} busy={saving} wide>
      {loadError && (
        <p className="ds-error skill-pane-note" role="alert">
          <Icon name="alert" size={14} /> {loadError}
        </p>
      )}
      {!skill && !loadError && (
        <p className="dialog-body">
          <Spinner /> Opening the skill…
        </p>
      )}
      {skill && !editing && (
        <>
          <SkillReading skill={skill} />
          <div className="dialog-actions">
            <button type="button" className="btn" onClick={onClose}>
              Close
            </button>
            <button type="button" className="btn btn-primary" onClick={() => startEditing(skill)}>
              <Icon name="pencil" size={13} /> Edit
            </button>
          </div>
        </>
      )}
      {skill && editing && (
        <form
          className="ds-form"
          onSubmit={(event) => {
            event.preventDefault();
            void save(skill);
          }}
        >
          <label className="field" htmlFor={`${ids}-description`}>
            <span>When it is used</span>
            <textarea
              id={`${ids}-description`}
              data-autofocus
              className="field-textarea"
              rows={3}
              value={description}
              onChange={(event) => {
                setDescription(event.target.value);
                setProblem(null);
              }}
            />
          </label>
          <label className="field" htmlFor={`${ids}-body`}>
            <span>Instructions</span>
            <textarea
              id={`${ids}-body`}
              className="field-textarea is-mono"
              rows={14}
              value={body}
              onChange={(event) => {
                setBody(event.target.value);
                setProblem(null);
              }}
            />
          </label>
          <p className="field-hint">The version you are changing is kept in the studio’s history.</p>
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
            <button type="button" className="btn" disabled={saving} onClick={() => setEditing(false)}>
              Cancel
            </button>
            <button type="submit" className="btn btn-primary" disabled={saving}>
              {saving && <Spinner size={13} />}
              Save
            </button>
          </div>
        </form>
      )}
    </Modal>
  );
}
