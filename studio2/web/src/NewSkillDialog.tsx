// Library → New skill: describe it, write it yourself, or import a file.

import { useEffect, useId, useRef, useState } from "react";
import { api, errorMessage } from "./api";
import { formatBytes } from "./format";
import { Icon, Spinner, type IconName } from "./icons";
import { Modal } from "./Modal";
import {
  DESCRIBE_EXAMPLE,
  importPlan,
  newDescribeProgress,
  scriptsLine,
  startDescribedSkill,
  type NameChoice,
} from "./skillMaking";
import { loadString, saveString } from "./storage";
import type { SkillImportPreview } from "./types";

export type Way = "describe" | "write" | "import";

const WAYS: { way: Way; icon: IconName; title: string; what: string }[] = [
  { way: "describe", icon: "chat", title: "Describe it", what: "Say what should happen in your own words. The agent writes the skill with you." },
  { way: "write", icon: "pencil", title: "Write it yourself", what: "Give it a name and type the instructions." },
  { way: "import", icon: "upload", title: "Import a file", what: "Add a skill someone shared with you as a .zip file." },
];

const TITLES: Record<Way, string> = { describe: "Describe the skill", write: "Write a skill", import: "Import a skill" };

const DRAFT_KEY = "skill-description-draft";

/** Opens the system's file picker. The input is never in the page, so Tab cannot land on it. */
function pickFiles(options: { accept?: string; multiple?: boolean }, onPicked: (files: File[]) => void) {
  const input = document.createElement("input");
  input.type = "file";
  input.multiple = options.multiple ?? false;
  if (options.accept) input.accept = options.accept;
  input.onchange = () => {
    if (input.files?.length) onPicked(Array.from(input.files));
  };
  input.click();
}

interface WriteDraft {
  name: string;
  description: string;
  body: string;
}

interface NewSkillDialogProps {
  /** Open at one of the ways instead of the choice ("A skill" in the New page dialog opens "Describe it"). */
  initialWay?: Way | null;
  onClose: () => void;
  /** A skill was added to the library, written here or imported. */
  onAdded: (name: string) => void;
  onOpenProject: (id: string) => void;
}

export function NewSkillDialog({ initialWay = null, onClose, onAdded, onOpenProject }: NewSkillDialogProps) {
  const [way, setWay] = useState<Way | null>(initialWay);
  const [busy, setBusy] = useState(false);
  // Kept here so that Back does not lose what was typed or chosen.
  const [description, setDescriptionState] = useState(() => loadString(DRAFT_KEY) ?? "");
  const [examples, setExamples] = useState<File[]>([]);
  const [written, setWritten] = useState<WriteDraft>({ name: "", description: "", body: "" });
  const pane = useRef<HTMLDivElement>(null);
  const moved = useRef(false);

  const setDescription = (text: string) => {
    setDescriptionState(text);
    saveString(DRAFT_KEY, text || null);
  };

  // The control that was pressed is gone: focus goes to the first thing in what replaced it.
  useEffect(() => {
    if (!moved.current) {
      moved.current = true;
      return;
    }
    pane.current?.querySelector<HTMLElement>("[data-autofocus], button")?.focus();
  }, [way]);

  const back = () => setWay(null);

  return (
    <Modal title={way ? TITLES[way] : "New skill"} onClose={onClose} busy={busy} wide>
      <div ref={pane}>
        {way === null && <WayChooser onChoose={setWay} />}
        {way === "describe" && (
          <DescribePane
            description={description}
            onDescription={setDescription}
            files={examples}
            onFiles={setExamples}
            busy={busy}
            onBusy={setBusy}
            onBack={back}
            onStarted={(id) => {
              saveString(DRAFT_KEY, null);
              onOpenProject(id);
            }}
          />
        )}
        {way === "write" && (
          <WritePane draft={written} onDraft={setWritten} busy={busy} onBusy={setBusy} onBack={back} onAdded={onAdded} />
        )}
        {way === "import" && <ImportPane busy={busy} onBusy={setBusy} onBack={back} onAdded={onAdded} />}
      </div>
    </Modal>
  );
}

function WayChooser({ onChoose }: { onChoose: (way: Way) => void }) {
  return (
    <ul className="skill-ways">
      {WAYS.map((item) => (
        <li key={item.way}>
          <button type="button" className="skill-way" onClick={() => onChoose(item.way)}>
            <span className="skill-way-icon">
              <Icon name={item.icon} size={18} />
            </span>
            <span className="skill-way-text">
              <strong>{item.title}</strong>
              <span>{item.what}</span>
            </span>
            <Icon name="chevron-right" size={16} className="skill-way-go" />
          </button>
        </li>
      ))}
    </ul>
  );
}

function BackButton({ disabled, onBack }: { disabled: boolean; onBack: () => void }) {
  return (
    <button type="button" className="btn skill-back" disabled={disabled} onClick={onBack}>
      <Icon name="arrow-left" size={14} /> Back
    </button>
  );
}

// ---- describe it ---------------------------------------------------------------------------

function DescribePane({
  description,
  onDescription,
  files,
  onFiles,
  busy,
  onBusy,
  onBack,
  onStarted,
}: {
  description: string;
  onDescription: (text: string) => void;
  files: File[];
  onFiles: (files: File[]) => void;
  busy: boolean;
  onBusy: (busy: boolean) => void;
  onBack: () => void;
  onStarted: (projectId: string) => void;
}) {
  const [problem, setProblem] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  // A Start that failed part of the way carries on from there.
  const progress = useRef(newDescribeProgress());
  const ids = useId();

  const addFiles = (picked: File[]) => {
    const same = (a: File, b: File) => a.name === b.name && a.size === b.size && a.lastModified === b.lastModified;
    onFiles([...files, ...picked.filter((file) => !files.some((have) => same(have, file)))]);
  };

  const start = async () => {
    if (!description.trim()) {
      setProblem("Say what the skill should do.");
      return;
    }
    onBusy(true);
    setError(null);
    try {
      onStarted(
        await startDescribedSkill(
          {
            createProject: (options) => api.createProject(options),
            upload: (id, file, dir) => api.upload(id, file, dir),
            startTurn: (id, input) => api.startTurn(id, input),
          },
          description,
          files,
          progress.current,
        ),
      );
    } catch (cause) {
      setError(errorMessage(cause));
      onBusy(false);
    }
  };

  return (
    <form
      className="ds-form"
      onSubmit={(event) => {
        event.preventDefault();
        void start();
      }}
    >
      <label className="field" htmlFor={`${ids}-text`}>
        <span>What should happen, step by step?</span>
        <textarea
          id={`${ids}-text`}
          data-autofocus
          className="field-textarea skill-describe"
          rows={9}
          value={description}
          placeholder={`For example: ${DESCRIBE_EXAMPLE}`}
          aria-invalid={problem ? true : undefined}
          onChange={(event) => {
            onDescription(event.target.value);
            setProblem(null);
          }}
        />
      </label>
      <p className="field-hint">Say what it starts from, the steps in order, and how to check the result.</p>
      <div className="field">
        <span>Examples to follow (optional)</span>
        {files.length > 0 && (
          <ul className="skill-files">
            {files.map((file) => (
              <li key={`${file.name}:${file.size}:${file.lastModified}`}>
                <span className="skill-file-path">{file.name}</span>
                <span className="skill-file-size">{formatBytes(file.size)}</span>
                <button
                  type="button"
                  className="icon-btn"
                  aria-label={`Remove ${file.name}`}
                  disabled={busy}
                  onClick={() => onFiles(files.filter((item) => item !== file))}
                >
                  <Icon name="x" size={14} />
                </button>
              </li>
            ))}
          </ul>
        )}
        <div>
          <button type="button" className="btn btn-small" disabled={busy} onClick={() => pickFiles({ multiple: true }, addFiles)}>
            <Icon name="paperclip" size={13} /> Add files
          </button>
        </div>
        <p className="field-hint">A page, a document or a picture the result should look like.</p>
      </div>
      <p className="field-hint">
        Start opens a new project where the agent asks what it needs and drafts the skill. Nothing joins your library until
        you allow it.
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
        <BackButton disabled={busy} onBack={onBack} />
        <button type="submit" className="btn btn-primary" disabled={busy}>
          {busy && <Spinner size={13} />}
          Start
        </button>
      </div>
    </form>
  );
}

// ---- write it yourself ---------------------------------------------------------------------

function WritePane({
  draft,
  onDraft,
  busy,
  onBusy,
  onBack,
  onAdded,
}: {
  draft: WriteDraft;
  onDraft: (draft: WriteDraft) => void;
  busy: boolean;
  onBusy: (busy: boolean) => void;
  onBack: () => void;
  onAdded: (name: string) => void;
}) {
  const [problem, setProblem] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const ids = useId();
  const change = (part: Partial<WriteDraft>) => {
    onDraft({ ...draft, ...part });
    setProblem(null);
  };

  const save = async () => {
    const trimmed = draft.name.trim();
    const issue =
      (trimmed ? null : "Give it a name.") ??
      (trimmed.length > 60 ? "The name can be at most 60 characters." : null) ??
      (/^[a-z0-9_-]+$/.test(trimmed) ? null : "Use lower-case letters, digits, “-” and “_” only.") ??
      (draft.description.trim() ? null : "Say in one line when the agent should use it.") ??
      (draft.body.trim() ? null : "Write the instructions.");
    if (issue) {
      setProblem(issue);
      return;
    }
    onBusy(true);
    setError(null);
    try {
      await api.createSkill({ name: trimmed, description: draft.description.trim(), body: draft.body });
      onAdded(trimmed);
    } catch (cause) {
      setError(errorMessage(cause));
      onBusy(false);
    }
  };

  return (
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
          value={draft.name}
          maxLength={60}
          spellCheck={false}
          autoComplete="off"
          placeholder="weekly-report"
          onChange={(event) => change({ name: event.target.value })}
        />
      </label>
      <label className="field" htmlFor={`${ids}-description`}>
        <span>When to use it</span>
        <input
          id={`${ids}-description`}
          value={draft.description}
          placeholder="Use when the person asks for the weekly sales report."
          onChange={(event) => change({ description: event.target.value })}
        />
      </label>
      <label className="field" htmlFor={`${ids}-body`}>
        <span>Instructions</span>
        <textarea
          id={`${ids}-body`}
          className="field-textarea is-mono"
          rows={10}
          value={draft.body}
          onChange={(event) => change({ body: event.target.value })}
        />
      </label>
      <p className="field-hint">Saved in the studio, not in a project. On in every project unless you switch it off there.</p>
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
        <BackButton disabled={busy} onBack={onBack} />
        <button type="submit" className="btn btn-primary" disabled={busy}>
          {busy && <Spinner size={13} />}
          Add to my library
        </button>
      </div>
    </form>
  );
}

// ---- import a file -------------------------------------------------------------------------

function ImportPane({
  busy,
  onBusy,
  onBack,
  onAdded,
}: {
  busy: boolean;
  onBusy: (busy: boolean) => void;
  onBack: () => void;
  onAdded: (name: string) => void;
}) {
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<SkillImportPreview | null>(null);
  const [reading, setReading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const choose = () =>
    pickFiles({ accept: ".zip,application/zip" }, ([picked]) => {
      setFile(picked);
      setPreview(null);
      setError(null);
      setReading(true);
      api
        .previewSkillImport(picked)
        .then(setPreview, (cause) => setError(errorMessage(cause)))
        .finally(() => setReading(false));
    });

  const add = async (options: Parameters<typeof api.importSkill>[1], name: string) => {
    if (!file) return;
    onBusy(true);
    setError(null);
    try {
      const skill = await api.importSkill(file, options);
      onAdded(skill.name || name);
    } catch (cause) {
      setError(errorMessage(cause));
      onBusy(false);
    }
  };

  return (
    <div className="ds-form">
      <div className="skill-import-file">
        <button type="button" className="btn" data-autofocus disabled={busy || reading} onClick={choose}>
          <Icon name="upload" size={14} /> {file ? "Choose another file" : "Choose a .zip file"}
        </button>
        {file && <span className="skill-file-path">{file.name}</span>}
      </div>
      {!file && <p className="field-hint">You see what is inside before anything is added.</p>}
      {reading && (
        <p className="dialog-body" role="status">
          <Spinner /> Reading the file…
        </p>
      )}
      {preview ? (
        <ImportPreview key={file?.name} preview={preview} busy={busy} error={error} onBack={onBack} onAdd={add} />
      ) : (
        <>
          {error && <ImportError message={error} />}
          <div className="dialog-actions">
            <BackButton disabled={busy} onBack={onBack} />
          </div>
        </>
      )}
    </div>
  );
}

function ImportError({ message }: { message: string }) {
  return (
    <p className="ds-error" role="alert">
      <Icon name="alert" size={14} /> {message}
    </p>
  );
}

export function ImportPreview({
  preview,
  busy,
  error = null,
  onBack,
  onAdd,
}: {
  preview: SkillImportPreview;
  busy: boolean;
  /** Why the last try was refused: shown beside the button that was pressed. */
  error?: string | null;
  onBack: () => void;
  onAdd: (options: Parameters<typeof api.importSkill>[1], name: string) => void;
}) {
  const [scriptsAccepted, setScriptsAccepted] = useState(false);
  const [nameChoice, setNameChoice] = useState<NameChoice>(null);
  const [newName, setNewName] = useState("");
  const ids = useId();
  const plan = importPlan(preview, { scriptsAccepted, nameChoice, newName });
  const count = preview.scripts.length;

  return (
    <>
      <div className="skill-reading">
        <section>
          <h3>Name</h3>
          <p>{preview.name}</p>
        </section>
        <section>
          <h3>When it is used</h3>
          <p>{preview.description}</p>
        </section>
        <section>
          <h3>How the instructions begin</h3>
          <pre className="skill-text is-short" tabIndex={0} aria-label="How the instructions begin">
            {preview.body_start}
          </pre>
        </section>
        <section>
          <h3>Files ({preview.files.length})</h3>
          <ul className="skill-files is-scroll" tabIndex={0} aria-label="The files in this skill">
            {preview.files.map((file) => (
              <li key={file.path}>
                <span className="skill-file-path">{file.path}</span>
                <span className="skill-file-size">{formatBytes(file.size)}</span>
              </li>
            ))}
          </ul>
        </section>
      </div>

      {count > 0 && (
        <div className="skill-scripts">
          <p className="skill-scripts-lead">
            <Icon name="warning" size={15} /> The agent can run these on this computer when it uses the skill, under the
            project’s approval rules.
          </p>
          <ul className="skill-files">
            {preview.scripts.map((path) => (
              <li key={path}>
                <span className="skill-file-path">{path}</span>
              </li>
            ))}
          </ul>
          <label className="skill-tick">
            <input type="checkbox" checked={scriptsAccepted} onChange={(event) => setScriptsAccepted(event.target.checked)} />
            <span>{scriptsLine(count)}</span>
          </label>
        </div>
      )}

      {plan.nameAsk === "replace" && (
        <fieldset className="field save-what">
          <legend>You already have a skill called “{preview.name}”</legend>
          <label className={`save-choice${nameChoice === "replace" ? " is-on" : ""}`}>
            <input type="radio" name={`${ids}-name`} checked={nameChoice === "replace"} onChange={() => setNameChoice("replace")} />
            <span>Replace mine with this one</span>
          </label>
          <label className={`save-choice${nameChoice === "rename" ? " is-on" : ""}`}>
            <input type="radio" name={`${ids}-name`} checked={nameChoice === "rename"} onChange={() => setNameChoice("rename")} />
            <span>Keep mine and add this under another name</span>
          </label>
        </fieldset>
      )}
      {plan.nameAsk === "rename" && (
        <p className="ds-note">One of the studio’s own skills is called “{preview.name}”, so this one needs another name.</p>
      )}
      {plan.renaming && (
        <label className="field" htmlFor={`${ids}-new-name`}>
          <span>New name</span>
          <input
            id={`${ids}-new-name`}
            value={newName}
            maxLength={60}
            spellCheck={false}
            autoComplete="off"
            aria-invalid={plan.nameIssue ? true : undefined}
            onChange={(event) => setNewName(event.target.value)}
          />
          {plan.nameIssue && <span className="ds-field-error">{plan.nameIssue}</span>}
        </label>
      )}

      {plan.waiting && <p className="field-hint">{plan.waiting}</p>}
      {error && <ImportError message={error} />}
      <div className="dialog-actions">
        <BackButton disabled={busy} onBack={onBack} />
        <button
          type="button"
          className="btn btn-primary"
          disabled={busy || !plan.ready}
          onClick={() => onAdd(plan.options, plan.options.name ?? preview.name)}
        >
          {busy && <Spinner size={13} />}
          Add to my library
        </button>
      </div>
    </>
  );
}
