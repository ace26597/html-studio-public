// Any skill in the library, read in full: when it is used, where it comes from, every file (the
// instructions first, scripts marked), and what can be done with it: have the agent explain it,
// export it, make one's own copy, or (for one's own) edit it, ask for a change, delete it.
// CONTRACT.md, "Reading any skill, explaining it, and starting from one".

import { useEffect, useId, useReducer, useState } from "react";
import { api, errorMessage } from "./api";
import { formatBytes } from "./format";
import { Icon, Spinner } from "./icons";
import { Modal } from "./Modal";
import { scriptsLine } from "./skillMaking";
import {
  askBody,
  askProblem,
  copiedId,
  copyPlan,
  copyReducer,
  CUT_NOTE,
  emptyCopyState,
  frontEntries,
  INSTRUCTIONS_PATH,
  MAX_ASK_TEXT,
  MAX_SKILL_NAME,
  otherFileNote,
  ownName,
  skillFactsLine,
  skillFileRows,
  skillOffers,
  skillWhere,
  type CopyAction,
  type CopyState,
  type SkillFileRow,
} from "./skillReader";
import type { SkillFileContent, SkillFull } from "./types";
import { useConfirm, useToast } from "./ui";

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

// ---- the reader (pure: rendered as static markup in tests) ---------------------------------

/** One file's contents as the dialog has them. */
export type FileState =
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "ready"; content: SkillFileContent };

export function SkillReader({
  skill,
  selected,
  file,
  pictureUrl,
  onSelect,
  onPictureError,
  now,
}: {
  skill: SkillFull;
  selected: string;
  /** The chosen file's contents (unused for the instructions, a picture, or a file of another kind). */
  file: FileState | null;
  /** Where a picture is fetched from. */
  pictureUrl: (path: string) => string;
  onSelect: (path: string) => void;
  onPictureError?: (path: string) => void;
  now?: number;
}) {
  const rows = skillFileRows(skill.files);
  const extras = frontEntries(skill.front);
  const chosen = rows.find((row) => row.path === selected) ?? rows[0] ?? null;
  return (
    <div className="skill-reader">
      <p className="skill-reader-where">
        {skillWhere(skill)}
        {skill.name && <code className="lix-id">{skill.name}</code>}
      </p>
      <p className="skill-reader-facts">{skillFactsLine(skill, now)}</p>
      <section className="skill-reader-part">
        <h3>When it is used</h3>
        <p>{skill.description || "It does not say."}</p>
      </section>
      {extras.length > 0 && (
        <section className="skill-reader-part">
          <h3>Also says</h3>
          <dl className="skill-also">
            {extras.map(([key, value]) => (
              <div key={key} className="skill-also-row">
                <dt>{key}</dt>
                <dd>{value}</dd>
              </div>
            ))}
          </dl>
        </section>
      )}
      <div className="skill-panes">
        <nav className="skill-file-list" aria-label="The skill’s files">
          <ul>
            {rows.map((row) => (
              <li key={row.path}>
                <button
                  type="button"
                  className={`skill-file-pick${chosen?.path === row.path ? " is-on" : ""}`}
                  aria-current={chosen?.path === row.path ? "true" : undefined}
                  onClick={() => onSelect(row.path)}
                >
                  <span className={`skill-file-name${row.instructions ? " is-instructions" : ""}`}>{row.label}</span>
                  <span className="skill-file-meta">
                    {row.script && <span className="badge badge-script">script</span>}
                    <span className="skill-file-size">{formatBytes(row.size)}</span>
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </nav>
        <div className="skill-file-shown">
          {chosen && <SkillFileShown skill={skill} row={chosen} file={file} pictureUrl={pictureUrl} onPictureError={onPictureError} />}
        </div>
      </div>
    </div>
  );
}

function SkillFileShown({
  skill,
  row,
  file,
  pictureUrl,
  onPictureError,
}: {
  skill: SkillFull;
  row: SkillFileRow;
  file: FileState | null;
  pictureUrl: (path: string) => string;
  onPictureError?: (path: string) => void;
}) {
  const heading = (
    <p className="skill-file-head">
      <span className="skill-file-path">{row.label}</span>
      {row.script && (
        <span className="skill-file-script-note">The agent may run this file when it uses the skill.</span>
      )}
    </p>
  );
  if (row.instructions) {
    return (
      <>
        {heading}
        <pre className="skill-text skill-file-text" tabIndex={0} aria-label="The skill’s instructions">
          {skill.body}
        </pre>
        {skill.body_cut && <p className="field-hint skill-cut">{CUT_NOTE}</p>}
      </>
    );
  }
  if (row.kind === "picture") {
    if (file?.status === "error") {
      return (
        <>
          {heading}
          <p className="ds-error" role="alert">
            <Icon name="alert" size={14} /> {file.message}
          </p>
        </>
      );
    }
    return (
      <>
        {heading}
        <div className="skill-picture">
          <img src={pictureUrl(row.path)} alt={row.path} onError={() => onPictureError?.(row.path)} />
        </div>
      </>
    );
  }
  if (row.kind !== "text") {
    return (
      <>
        {heading}
        <p className="skill-other">{otherFileNote(row.path)}</p>
      </>
    );
  }
  if (!file || file.status === "loading") {
    return (
      <>
        {heading}
        <p className="dialog-body" role="status">
          <Spinner /> Opening the file…
        </p>
      </>
    );
  }
  if (file.status === "error") {
    return (
      <>
        {heading}
        <p className="ds-error" role="alert">
          <Icon name="alert" size={14} /> {file.message}
        </p>
      </>
    );
  }
  return (
    <>
      {heading}
      <pre className="skill-text skill-file-text is-mono" tabIndex={0} aria-label={`The file ${row.path}`}>
        {file.content.text ?? ""}
      </pre>
      {file.content.cut && <p className="field-hint skill-cut">{CUT_NOTE}</p>}
    </>
  );
}

// ---- the copy form (pure) ------------------------------------------------------------------

export function CopyForm({
  skill,
  state,
  dispatch,
  onSubmit,
  onCancel,
}: {
  skill: SkillFull;
  state: CopyState;
  dispatch: (action: CopyAction) => void;
  onSubmit: () => void;
  onCancel: () => void;
}) {
  const ids = useId();
  const plan = copyPlan(skill, state);
  const count = plan.scripts.length;
  const refusal = state.refusal;
  return (
    <form
      className="ds-form skill-copy"
      onSubmit={(event) => {
        event.preventDefault();
        if (plan.ready) onSubmit();
      }}
    >
      <h3 className="skill-form-title">Make my own copy</h3>
      <p className="field-hint">
        The copy joins your library with every file of this skill. You can then change it; this one stays as it is.
      </p>
      <label className="field" htmlFor={`${ids}-name`}>
        <span>Name of your copy</span>
        <input
          id={`${ids}-name`}
          data-autofocus
          value={state.name}
          maxLength={MAX_SKILL_NAME}
          spellCheck={false}
          autoComplete="off"
          aria-invalid={plan.nameIssue ? true : undefined}
          disabled={state.busy}
          onChange={(event) => dispatch({ type: "name", name: event.target.value })}
        />
        {plan.nameIssue && state.name.trim() !== "" && <span className="ds-field-error">{plan.nameIssue}</span>}
      </label>
      <p className="field-hint">Lower-case letters, digits, “-” and “_”, at most {MAX_SKILL_NAME} characters.</p>

      {plan.askReplace && refusal?.code === "name_taken" && (
        <div className="skill-name-taken" role="alert">
          <p>You already have a skill called “{refusal.name}”.</p>
          <div className="skill-name-taken-actions">
            <button type="button" className="btn btn-small" disabled={state.busy} onClick={() => dispatch({ type: "replace" })}>
              Replace it
            </button>
            <span className="field-hint">or change the name above.</span>
          </div>
        </div>
      )}
      {state.replace && (
        <p className="ds-note">Your skill “{state.name.trim()}” will be replaced. The version it replaces is kept in the studio’s history.</p>
      )}

      {count > 0 && (
        <div className="skill-scripts">
          <p className="skill-scripts-lead">
            <Icon name="warning" size={15} /> The agent can run these on this computer when it uses the skill, under the
            project’s approval rules.
          </p>
          <ul className="skill-files">
            {plan.scripts.map((path) => (
              <li key={path}>
                <span className="skill-file-path">{path}</span>
              </li>
            ))}
          </ul>
          <label className="skill-tick">
            <input
              type="checkbox"
              checked={state.scriptsAccepted}
              disabled={state.busy}
              onChange={(event) => dispatch({ type: "tick", on: event.target.checked })}
            />
            <span>{scriptsLine(count)}</span>
          </label>
        </div>
      )}

      {plan.waiting && <p className="field-hint">{plan.waiting}</p>}
      {refusal?.code === "scripts_need_confirmation" && (
        <p className="ds-error" role="alert">
          <Icon name="alert" size={14} /> {refusal.message}
        </p>
      )}
      {state.error && (
        <p className="ds-error" role="alert">
          <Icon name="alert" size={14} /> {state.error}
        </p>
      )}
      <div className="dialog-actions">
        <button type="button" className="btn" disabled={state.busy} onClick={onCancel}>
          Cancel
        </button>
        <button type="submit" className="btn btn-primary" disabled={!plan.ready}>
          {state.busy && <Spinner size={13} />}
          {plan.label}
        </button>
      </div>
    </form>
  );
}

// ---- asking the agent (explain, or change one's own) ---------------------------------------

export function AskForm({
  about,
  text,
  busy,
  problem,
  error,
  onText,
  onSubmit,
  onCancel,
}: {
  about: "explain" | "change";
  text: string;
  busy: boolean;
  problem: string | null;
  error: string | null;
  onText: (text: string) => void;
  onSubmit: () => void;
  onCancel: () => void;
}) {
  const ids = useId();
  const explain = about === "explain";
  return (
    <form
      className="ds-form skill-ask"
      onSubmit={(event) => {
        event.preventDefault();
        onSubmit();
      }}
    >
      <h3 className="skill-form-title">{explain ? "Explain this skill" : "Ask for a change"}</h3>
      <label className="field" htmlFor={`${ids}-text`}>
        <span>{explain ? "Anything in particular you want to know? (optional)" : "What should change?"}</span>
        <textarea
          id={`${ids}-text`}
          data-autofocus
          className="field-textarea"
          rows={explain ? 3 : 5}
          value={text}
          maxLength={explain ? MAX_ASK_TEXT : undefined}
          placeholder={explain ? "For example: what does it need to be installed?" : "For example: also check the totals against last month."}
          aria-invalid={problem ? true : undefined}
          disabled={busy}
          onChange={(event) => onText(event.target.value)}
        />
      </label>
      <p className="field-hint">
        {explain
          ? "A new project opens where the agent reads every file of the skill and explains it on the page. Nothing is run or changed."
          : "A new project opens where the agent reads the skill and drafts the change. Nothing is saved until you allow it."}
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
        <button type="button" className="btn" disabled={busy} onClick={onCancel}>
          Cancel
        </button>
        <button type="submit" className="btn btn-primary" disabled={busy}>
          {busy && <Spinner size={13} />}
          {explain ? "Explain" : "Ask for the change"}
        </button>
      </div>
    </form>
  );
}

// ---- the dialog ----------------------------------------------------------------------------

type Mode = "read" | "edit" | "copy" | "explain" | "change";

export function SkillOpenDialog({
  id: openedId,
  shared = false,
  onClose,
  onChanged,
  onOpenProject,
}: {
  /** The skill's id in the library (`studio:…`, `yours:…`, `machine:…`, `plugin:…`). */
  id: string;
  /** A shared studio: only Explain and Export. */
  shared?: boolean;
  onClose: () => void;
  /** The library changed (a copy, an edit, a delete): lists reload. */
  onChanged: () => void;
  onOpenProject: (projectId: string) => void;
}) {
  const toast = useToast();
  const confirm = useConfirm();
  const [id, setId] = useState(openedId);
  const [attempt, setAttempt] = useState(0);
  const [skill, setSkill] = useState<SkillFull | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [selected, setSelected] = useState(INSTRUCTIONS_PATH);
  const [file, setFile] = useState<FileState | null>(null);
  const [mode, setMode] = useState<Mode>("read");
  const [busy, setBusy] = useState(false);
  // Asking
  const [askText, setAskText] = useState("");
  const [askIssue, setAskIssue] = useState<string | null>(null);
  const [askError, setAskError] = useState<string | null>(null);
  // Copying
  const [copy, dispatchCopy] = useReducer(copyReducer, emptyCopyState);
  // Editing
  const [description, setDescription] = useState("");
  const [body, setBody] = useState("");
  const [original, setOriginal] = useState<{ description: string; body: string } | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const ids = useId();

  useEffect(() => {
    const controller = new AbortController();
    setLoadError(null);
    api.skillDetail(id, controller.signal).then(
      (detail) => {
        setSkill(detail);
        setSelected((current) => (detail.files.some((item) => item.path === current) ? current : INSTRUCTIONS_PATH));
      },
      (cause) => {
        if ((cause as Error)?.name !== "AbortError") setLoadError(errorMessage(cause));
      },
    );
    return () => controller.abort();
  }, [id, attempt]);

  // A text file other than the instructions is fetched when it is chosen.
  const row = skill ? (skillFileRows(skill.files).find((item) => item.path === selected) ?? null) : null;
  const needsText = Boolean(skill && row && !row.instructions && row.kind === "text");
  useEffect(() => {
    setFile(null);
    if (!needsText) return;
    const controller = new AbortController();
    setFile({ status: "loading" });
    api.skillFile(id, selected, controller.signal).then(
      (content) => setFile({ status: "ready", content }),
      (cause) => {
        if ((cause as Error)?.name !== "AbortError") setFile({ status: "error", message: errorMessage(cause) });
      },
    );
    return () => controller.abort();
  }, [id, selected, needsText, attempt]);

  const toRead = () => {
    setMode("read");
    setAskIssue(null);
    setAskError(null);
  };

  const ask = async (current: SkillFull, about: "explain" | "change") => {
    const issue = askProblem(about, askText);
    if (issue) {
      setAskIssue(issue);
      return;
    }
    setBusy(true);
    setAskError(null);
    try {
      const accepted = await api.askAboutSkill(askBody(current.id, about, askText));
      onOpenProject(accepted.project_id);
    } catch (cause) {
      setAskError(errorMessage(cause));
      setBusy(false);
    }
  };

  const makeCopy = async (current: SkillFull) => {
    const plan = copyPlan(current, copy);
    if (!plan.ready) return;
    dispatchCopy({ type: "submit" });
    setBusy(true);
    try {
      const made = await api.copySkill(plan.request);
      const name = made?.name || plan.request.name;
      dispatchCopy({ type: "done" });
      toast({ message: `Added “${name}” to your library`, kind: "success" });
      onChanged();
      setMode("read");
      setSkill(null);
      setFile(null);
      setSelected(INSTRUCTIONS_PATH);
      setId(made?.id || copiedId(name));
    } catch (cause) {
      dispatchCopy({ type: "refused", error: cause });
    } finally {
      setBusy(false);
    }
  };

  const startEditing = async (current: SkillFull) => {
    // The person's own skill, whole: the reader's copy of the instructions may be cut.
    setBusy(true);
    setError(null);
    try {
      const whole = await api.getSkill(ownName(current));
      setDescription(whole.description);
      setBody(whole.body);
      setOriginal({ description: whole.description, body: whole.body });
      setProblem(null);
      setMode("edit");
    } catch (cause) {
      toast({ message: errorMessage(cause), kind: "error" });
    } finally {
      setBusy(false);
    }
  };

  const save = async (current: SkillFull) => {
    const issue = skillEditProblem(description, body);
    if (issue) {
      setProblem(issue);
      return;
    }
    const was = original ?? { description: current.description, body: current.body };
    const change: { description?: string; body?: string } = {};
    if (description.trim() !== was.description) change.description = description.trim();
    if (body !== was.body) change.body = body;
    if (!change.description && change.body === undefined) {
      setMode("read");
      return;
    }
    const name = ownName(current);
    setBusy(true);
    setError(null);
    try {
      await api.updateSkill(name, change);
      setMode("read");
      setAttempt((n) => n + 1);
      toast({ message: `Saved the skill “${name}”`, kind: "success" });
      onChanged();
    } catch (cause) {
      setError(errorMessage(cause));
    } finally {
      setBusy(false);
    }
  };

  const remove = async (current: SkillFull) => {
    const name = ownName(current);
    const ok = await confirm({
      title: `Delete the skill “${name}”?`,
      body: "It is removed from the studio and from every project that had it on.",
      confirmLabel: "Delete",
      danger: true,
    });
    if (!ok) return;
    try {
      await api.deleteSkill(name);
      toast({ message: `Deleted “${name}”` });
      onChanged();
      onClose();
    } catch (cause) {
      toast({ message: errorMessage(cause), kind: "error" });
    }
  };

  const offers = skill ? skillOffers(skill, shared) : null;
  const title = skill ? skill.label || skill.name || id : "Skill";

  return (
    <Modal title={title} onClose={onClose} busy={busy} wide className="skill-reader-dialog">
      {loadError && (
        <div className="skill-pane-note">
          <p className="ds-error" role="alert">
            <Icon name="alert" size={14} /> {loadError}
          </p>
          <div className="dialog-actions">
            <button type="button" className="btn" onClick={onClose}>
              Close
            </button>
            <button type="button" className="btn btn-primary" onClick={() => setAttempt((n) => n + 1)}>
              Try again
            </button>
          </div>
        </div>
      )}
      {!skill && !loadError && (
        <p className="dialog-body" role="status">
          <Spinner /> Opening the skill…
        </p>
      )}
      {skill && offers && mode !== "edit" && (
        <>
          <SkillReader
            skill={skill}
            selected={selected}
            file={file}
            pictureUrl={(path) => api.skillFileRawUrl(skill.id, path)}
            onSelect={setSelected}
            onPictureError={() => setFile({ status: "error", message: "The picture could not be shown." })}
          />
          {mode === "read" && (
            <div className="dialog-actions skill-reader-actions">
              {offers.delete && (
                <button type="button" className="btn skill-delete" disabled={busy} onClick={() => void remove(skill)}>
                  <Icon name="trash" size={13} /> Delete
                </button>
              )}
              <a className="btn" href={api.skillExportUrl(skill.id)} download>
                <Icon name="download" size={13} /> Export
              </a>
              {offers.edit && (
                <button type="button" className="btn" disabled={busy} onClick={() => void startEditing(skill)}>
                  {busy ? <Spinner size={13} /> : <Icon name="pencil" size={13} />} Edit
                </button>
              )}
              {offers.askChange && (
                <button
                  type="button"
                  className="btn"
                  disabled={busy}
                  onClick={() => {
                    setAskText("");
                    setMode("change");
                  }}
                >
                  Ask for a change
                </button>
              )}
              <button
                type="button"
                className={`btn${offers.copy ? "" : " btn-primary"}`}
                disabled={busy}
                onClick={() => {
                  setAskText("");
                  setMode("explain");
                }}
              >
                Explain this skill
              </button>
              {offers.copy && (
                <button
                  type="button"
                  className="btn btn-primary"
                  disabled={busy}
                  onClick={() => {
                    dispatchCopy({ type: "reset", skillName: ownName(skill) });
                    setMode("copy");
                  }}
                >
                  Make my own copy
                </button>
              )}
            </div>
          )}
          {(mode === "explain" || mode === "change") && (
            <AskForm
              about={mode}
              text={askText}
              busy={busy}
              problem={askIssue}
              error={askError}
              onText={(text) => {
                setAskText(text);
                setAskIssue(null);
              }}
              onSubmit={() => void ask(skill, mode)}
              onCancel={toRead}
            />
          )}
          {mode === "copy" && (
            <CopyForm skill={skill} state={copy} dispatch={dispatchCopy} onSubmit={() => void makeCopy(skill)} onCancel={toRead} />
          )}
        </>
      )}
      {skill && mode === "edit" && (
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
            <button type="button" className="btn" disabled={busy} onClick={() => setMode("read")}>
              Cancel
            </button>
            <button type="submit" className="btn btn-primary" disabled={busy}>
              {busy && <Spinner size={13} />}
              Save
            </button>
          </div>
        </form>
      )}
    </Modal>
  );
}
