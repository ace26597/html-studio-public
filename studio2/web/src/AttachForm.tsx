// Step 3k: "An app on this computer" in the New page dialog. The person types a folder's path,
// the studio inspects it (nothing is created yet), and they choose a safe copy or edit in place.

import { useEffect, useId, useRef, useState } from "react";
import { api, errorMessage } from "./api";
import { addressProblem, attachBlocker, attachRequest, defaultAttachName, shortPath, sizeLine } from "./attach";
import { Icon, Spinner } from "./icons";
import type { AttachMode, AttachRequest, Inspection } from "./types";

interface AttachFormProps {
  disabled: boolean;
  /** Called whenever the choices change: the request to send, or why Create is not possible yet. */
  onReady: (request: AttachRequest | null, blocker: string | null) => void;
  /** Enter in a field: create now if possible. */
  onSubmit: () => void;
}

const SHOWN_CHANGES = 8;

export function AttachForm({ disabled, onReady, onSubmit }: AttachFormProps) {
  const [path, setPath] = useState("");
  const [inspection, setInspection] = useState<Inspection | null>(null);
  const [inspecting, setInspecting] = useState(false);
  const [inspectError, setInspectError] = useState<string | null>(null);
  const [mode, setMode] = useState<AttachMode>("copy");
  const [name, setName] = useState("");
  const [run, setRun] = useState("");
  const [address, setAddress] = useState("");
  const [envFiles, setEnvFiles] = useState<string[]>([]);
  const [includeUncommitted, setIncludeUncommitted] = useState(false);
  const controller = useRef<AbortController | null>(null);
  const ids = { path: useId(), name: useId(), run: useId(), address: useId(), hint: useId() };

  const inspect = () => {
    const typed = path.trim();
    if (!typed || inspecting) return;
    controller.current?.abort();
    const current = new AbortController();
    controller.current = current;
    setInspecting(true);
    setInspectError(null);
    api.inspectFolder(typed, current.signal).then(
      (found) => {
        setInspection(found);
        setMode(found.refused.copy && !found.refused.in_place ? "in_place" : "copy");
        setRun(found.run ?? "");
        setEnvFiles([]);
        setIncludeUncommitted(false);
        setAddress("");
        setInspecting(false);
      },
      (cause) => {
        if ((cause as Error)?.name === "AbortError") return;
        setInspection(null);
        setInspectError(errorMessage(cause));
        setInspecting(false);
      },
    );
  };

  useEffect(() => () => controller.current?.abort(), []);

  // A changed path makes the last inspection stale.
  useEffect(() => {
    if (inspection && path.trim() && path.trim() !== inspection.path && shortPath(inspection.path) !== path.trim()) {
      setInspection(null);
    }
  }, [path, inspection]);

  const choices = { mode, name, run, address, envFiles, includeUncommitted };
  const blocker = attachBlocker(inspection, choices);
  const request = inspection && !blocker ? attachRequest(inspection, choices) : null;
  const requestKey = JSON.stringify(request);
  useEffect(() => {
    onReady(request, blocker);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [requestKey, blocker]);

  const onEnter = (event: React.KeyboardEvent, action: () => void) => {
    if (event.key === "Enter") {
      event.preventDefault();
      action();
    }
  };

  const way = (value: AttachMode, label: string, description: string) => {
    const refused = inspection ? (value === "copy" ? inspection.refused.copy : inspection.refused.in_place) : null;
    const checked = mode === value;
    return (
      <button
        type="button"
        role="radio"
        aria-checked={checked}
        aria-disabled={refused ? true : undefined}
        className={`template-card attach-way${checked ? " is-checked" : ""}${refused ? " is-refused" : ""}`}
        onClick={() => setMode(value)}
      >
        <span className="template-top">
          <span className="template-label">{label}</span>
          {value === "copy" && <span className="badge badge-app">Default</span>}
        </span>
        <span className="template-description">{refused ? `Not possible: ${refused}` : description}</span>
      </button>
    );
  };

  const uncommitted = inspection?.git?.uncommitted ?? [];
  const moreChanges = (inspection?.git?.uncommitted_more ?? 0) + Math.max(0, uncommitted.length - SHOWN_CHANGES);
  const addressError = mode === "in_place" ? addressProblem(address) : null;

  return (
    <div className="attach-form">
      <label className="field-label" htmlFor={ids.path}>
        Folder on this computer
      </label>
      <div className="attach-path-row">
        <input
          id={ids.path}
          className="text-input"
          value={path}
          placeholder="~/dev/my-app"
          spellCheck={false}
          autoCapitalize="off"
          autoCorrect="off"
          autoFocus
          disabled={disabled}
          aria-describedby={ids.hint}
          onChange={(event) => setPath(event.target.value)}
          onKeyDown={(event) => onEnter(event, inspection ? onSubmit : inspect)}
        />
        <button type="button" className="btn" disabled={disabled || inspecting || !path.trim()} onClick={inspect}>
          {inspecting && <Spinner size={14} />}
          Check
        </button>
      </div>
      <p id={ids.hint} className="field-hint">
        Type the folder’s full path; a browser cannot look through your disk. Nothing is created until you press Create.
      </p>

      {inspectError && (
        <p className="template-error" role="alert">
          <Icon name="alert" size={15} />
          <span>{inspectError}</span>
        </p>
      )}

      {inspection && !inspection.exists && (
        <p className="template-error" role="alert">
          <Icon name="alert" size={15} />
          <span>{inspection.refused.copy}</span>
        </p>
      )}

      {inspection && inspection.exists && (
        <>
          <dl className="attach-facts" aria-label="What the studio found">
            <div>
              <dt>App</dt>
              <dd>{inspection.framework ? inspection.framework.label : "Not recognised"}</dd>
            </div>
            <div>
              <dt>History</dt>
              <dd>
                {inspection.git
                  ? `git · ${inspection.git.branch ?? "detached"} · ${inspection.git.head?.slice(0, 7) ?? "no commits"}${
                      inspection.git.subject ? ` “${inspection.git.subject}”` : ""
                    }`
                  : "Not under git"}
              </dd>
            </div>
            <div>
              <dt>Size</dt>
              <dd>{sizeLine(inspection)}</dd>
            </div>
            {inspection.install && (
              <div>
                <dt>Packages</dt>
                <dd>
                  <code>{inspection.install}</code>
                </dd>
              </div>
            )}
          </dl>
          {inspection.notes.length > 0 && (
            <ul className="attach-notes">
              {inspection.notes.map((note) => (
                <li key={note}>{note}</li>
              ))}
            </ul>
          )}

          <h3 className="template-group-title">How the studio works on it</h3>
          <div className="template-grid attach-ways" role="radiogroup" aria-label="Way in">
            {way(
              "copy",
              "Safe copy",
              inspection.git
                ? "The studio works on its own clone, with the app’s history, on a branch of its own. Your folder is not changed."
                : "The studio works on its own copy of the files. Your folder is not changed.",
            )}
            {way(
              "in_place",
              "Edit in place",
              "Changes are written straight into this folder. The studio keeps its own versions; the app’s git is not touched.",
            )}
          </div>

          {mode === "copy" && uncommitted.length > 0 && (
            <div className="attach-section">
              <p className="field-label">Changed here but not committed</p>
              <ul className="attach-changes">
                {uncommitted.slice(0, SHOWN_CHANGES).map((change) => (
                  <li key={change.path}>
                    <span className={`attach-change is-${change.status}`}>{change.status}</span> {change.path}
                  </li>
                ))}
                {moreChanges > 0 && <li className="field-hint">and {moreChanges} more</li>}
              </ul>
              <label className="check-row">
                <input
                  type="checkbox"
                  checked={includeUncommitted}
                  disabled={disabled}
                  onChange={(event) => setIncludeUncommitted(event.target.checked)}
                />
                <span>Take these too (otherwise the copy starts from the last commit)</span>
              </label>
            </div>
          )}

          {mode === "copy" && inspection.env_files.length > 0 && (
            <div className="attach-section">
              <p className="field-label">Env files to copy</p>
              {inspection.env_files.map((file) => (
                <label key={file} className="check-row">
                  <input
                    type="checkbox"
                    checked={envFiles.includes(file)}
                    disabled={disabled}
                    onChange={(event) =>
                      setEnvFiles((list) => (event.target.checked ? [...list, file] : list.filter((item) => item !== file)))
                    }
                  />
                  <code>{file}</code>
                </label>
              ))}
              <p className="field-hint">Only the ones you tick. They may hold real keys; they are never kept in versions.</p>
            </div>
          )}

          {mode === "in_place" && (
            <div className="attach-section">
              <label className="field-label" htmlFor={ids.address}>
                Address where it already runs <span className="field-optional">optional</span>
              </label>
              <input
                id={ids.address}
                className={`text-input${addressError ? " is-invalid" : ""}`}
                value={address}
                placeholder="http://localhost:5173"
                spellCheck={false}
                autoCapitalize="off"
                disabled={disabled}
                aria-invalid={addressError ? true : undefined}
                onChange={(event) => setAddress(event.target.value)}
                onKeyDown={(event) => onEnter(event, onSubmit)}
              />
              <p className={`field-hint${addressError ? " is-error" : ""}`}>
                {addressError ?? "The studio shows that server and never starts or stops it. Leave empty to use a start command."}
              </p>
            </div>
          )}

          {!(mode === "in_place" && address.trim()) && (
            <div className="attach-section">
              <label className="field-label" htmlFor={ids.run}>
                Start command
              </label>
              <input
                id={ids.run}
                className="text-input is-mono"
                value={run}
                placeholder="Leave empty to serve the files, or ask the agent later"
                spellCheck={false}
                autoCapitalize="off"
                disabled={disabled}
                onChange={(event) => setRun(event.target.value)}
                onKeyDown={(event) => onEnter(event, onSubmit)}
              />
              <p className="field-hint">
                It must listen on <code>127.0.0.1:$PORT</code>.{" "}
                {mode === "copy" && inspection.install ? "Packages are installed when it first starts; the app log shows it." : ""}
              </p>
            </div>
          )}

          <div className="attach-section">
            <label className="field-label" htmlFor={ids.name}>
              Name
            </label>
            <input
              id={ids.name}
              className="text-input"
              value={name}
              maxLength={120}
              placeholder={defaultAttachName(inspection, mode)}
              disabled={disabled}
              onChange={(event) => setName(event.target.value)}
              onKeyDown={(event) => onEnter(event, onSubmit)}
            />
          </div>
        </>
      )}
    </div>
  );
}
