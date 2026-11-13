// Make it live: the recipe, the card the person allows, live progress, the result and the
// history of ships (CONTRACT.md, "Ship: make it live"). Opened from the top bar's button.

import { useCallback, useEffect, useId, useState } from "react";
import { api, errorMessage } from "./api";
import { fileStatus, relativeTime } from "./format";
import { Icon, Spinner } from "./icons";
import { Modal } from "./Modal";
import {
  METHOD_LABEL,
  METHOD_SHORT,
  confirmedBy,
  formRecipe,
  progressSteps,
  recipeForm,
  resultText,
  type ProgressStep,
  type RecipeForm,
} from "./ship";
import type { Project, ShipCard, ShipRecord, ShipStepEvent, ShipView } from "./types";

type Tab = "live" | "recipe" | "history";

interface ShipPanelProps {
  project: Project;
  view: ShipView;
  /** The latest `ship_step` event while a ship runs. */
  liveStep: ShipStepEvent | null;
  /** Changes whenever a ship event arrives: the panel reads the record again. */
  tick: number;
  initialTab?: Tab;
  onClose: () => void;
  /** After a change the panel made (recipe saved, ship started): read the project again. */
  onChanged: () => void;
}

export function ShipPanel({ project, view, liveStep, tick, initialTab, onClose, onChanged }: ShipPanelProps) {
  const [tab, setTab] = useState<Tab>(initialTab ?? (view.confirmed ? "live" : "recipe"));
  const [records, setRecords] = useState<ShipRecord[] | null>(null);
  const [current, setCurrent] = useState<ShipRecord | null>(null);
  const [starting, setStarting] = useState(false);
  const [startError, setStartError] = useState<string | null>(null);
  const [updating, setUpdating] = useState(false);
  const [updateNote, setUpdateNote] = useState<string | null>(null);

  const shipId = view.running ?? view.last?.ship_id ?? null;
  useEffect(() => {
    let live = true;
    if (shipId) {
      api.getShipRecord(project.id, shipId).then(
        (record) => live && setCurrent(record),
        () => live && setCurrent(null),
      );
    } else setCurrent(null);
    return () => {
      live = false;
    };
  }, [project.id, shipId, tick]);

  useEffect(() => {
    if (tab !== "history") return;
    let live = true;
    api.listShips(project.id).then(
      (list) => live && setRecords(list),
      () => live && setRecords([]),
    );
    return () => {
      live = false;
    };
  }, [project.id, tab, tick]);

  const start = useCallback(() => {
    setStarting(true);
    setStartError(null);
    setUpdateNote(null);
    api
      .startShip(project.id)
      .then(onChanged, (error) => setStartError(errorMessage(error)))
      .finally(() => setStarting(false));
  }, [project.id, onChanged]);

  const updateCopy = useCallback(() => {
    setUpdating(true);
    setUpdateNote(null);
    api
      .updateCopy(project.id)
      .then(
        (answer) => {
          setUpdateNote(answer.message);
          setStartError(null);
          onChanged();
        },
        (error) => setUpdateNote(errorMessage(error)),
      )
      .finally(() => setUpdating(false));
  }, [project.id, onChanged]);

  const gitCopy = project.attach?.mode === "copy" && project.attach.kind === "git";
  const movedOn = Boolean(startError && /moved on/.test(startError));

  return (
    <Modal title="Make it live" onClose={onClose} wide>
      <p className="ship-where">
        <Icon name="external" size={13} />
        <span>
          {project.attach?.mode === "in_place" ? "Edited in place: " : "Original: "}
          <code>{project.attach?.path}</code>
        </span>
      </p>
      <div className="newpage-tabs ship-tabs" role="tablist" aria-label="Make it live">
        {(
          [
            ["live", view.running ? "Now" : "Ship"],
            ["recipe", "Recipe"],
            ["history", "History"],
          ] as [Tab, string][]
        ).map(([key, label]) => (
          <button
            key={key}
            type="button"
            role="tab"
            aria-selected={tab === key}
            className={`newpage-tab${tab === key ? " is-on" : ""}`}
            onClick={() => setTab(key)}
          >
            {label}
            {key === "live" && view.card && <span className="ship-dot" aria-label="waiting for you" />}
          </button>
        ))}
      </div>

      {tab === "live" && (
        <div className="ship-body" role="tabpanel">
          {view.running ? (
            <>
              <Progress steps={progressSteps(current, liveStep)} />
              {view.card && <Card key={view.card.ship_id} projectId={project.id} card={view.card} />}
              {!view.card && (
                <p className="ship-note">
                  <Spinner size={13} /> The studio is working. You can close this; the button in the top bar shows when it
                  needs you.
                </p>
              )}
            </>
          ) : (
            <>
              {current?.result && <Result record={current} />}
              {!view.confirmed ? (
                <div className="ship-empty">
                  <p>There is no recipe yet. Ask the assistant to “set up making this live” (it reads the app and proposes one), or write it yourself.</p>
                  <button type="button" className="btn" onClick={() => setTab("recipe")}>
                    Write the recipe
                  </button>
                </div>
              ) : (
                <div className="ship-start">
                  <p className="ship-note">
                    {METHOD_LABEL[view.recipe!.method]}. The studio saves the current version, runs the checks, and
                    shows you exactly what it will write before anything reaches the original.
                  </p>
                  {startError && (
                    <p className="ship-error" role="alert">
                      {startError}
                    </p>
                  )}
                  <div className="ship-actions">
                    {gitCopy && (movedOn || updateNote) && (
                      <button type="button" className="btn" onClick={updateCopy} disabled={updating}>
                        {updating ? <Spinner size={13} /> : <Icon name="refresh" size={14} />}
                        Bring the copy up to date
                      </button>
                    )}
                    <button type="button" className="btn btn-primary" onClick={start} disabled={starting}>
                      {starting ? <Spinner size={13} /> : <Icon name="play" size={14} />}
                      Make it live
                    </button>
                  </div>
                  {updateNote && <p className="ship-note">{updateNote}</p>}
                </div>
              )}
            </>
          )}
        </div>
      )}

      {tab === "recipe" && (
        <RecipeEditor
          project={project}
          view={view}
          onSaved={() => {
            onChanged();
            setTab("live");
          }}
        />
      )}

      {tab === "history" && (
        <div className="ship-body" role="tabpanel">
          {records === null ? (
            <p className="ship-note">
              <Spinner size={13} /> Loading…
            </p>
          ) : records.length === 0 ? (
            <p className="ship-note">Nothing has been made live from this project yet.</p>
          ) : (
            <ul className="ship-history">
              {records.map((record) => (
                <HistoryItem key={record.id} record={record} />
              ))}
            </ul>
          )}
        </div>
      )}
    </Modal>
  );
}

// ---- progress -----------------------------------------------------------------------
const STEP_ICON: Record<ProgressStep["state"], "check" | "x" | "hand" | "info"> = {
  ok: "check",
  failed: "x",
  waiting: "hand",
  skipped: "info",
  running: "info",
};

function Progress({ steps }: { steps: ProgressStep[] }) {
  if (!steps.length) {
    return (
      <p className="ship-note">
        <Spinner size={13} /> Saving this version and starting…
      </p>
    );
  }
  return (
    <ol className="ship-steps" aria-live="polite">
      {steps.map((step) => (
        <li key={step.key} className={`ship-step is-${step.state}`}>
          <span className="ship-step-icon" aria-hidden="true">
            {step.state === "running" ? <Spinner size={13} /> : <Icon name={STEP_ICON[step.state]} size={13} strokeWidth={2.2} />}
          </span>
          <div className="ship-step-text">
            <span className={`ship-step-label${step.command ? " is-command" : ""}`}>{step.label}</span>
            {step.detail && <span className="ship-step-detail">{step.detail}</span>}
            {step.output && step.state === "failed" && <pre className="ship-output">{step.output}</pre>}
            {step.output && step.state === "ok" && (
              <details>
                <summary>Output</summary>
                <pre className="ship-output">{step.output || "(no output)"}</pre>
              </details>
            )}
          </div>
        </li>
      ))}
    </ol>
  );
}

// ---- the card -------------------------------------------------------------------------
function Card({ projectId, card }: { projectId: string; card: ShipCard }) {
  const [typed, setTyped] = useState("");
  const [busy, setBusy] = useState<"allow" | "deny" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [allFiles, setAllFiles] = useState(false);
  const nameId = useId();
  const nameOk = !card.needs_name || typed.trim().replace(/\s+/g, " ") === card.project_name.trim().replace(/\s+/g, " ");
  const shown = allFiles ? card.files : card.files.slice(0, 12);

  const decide = (decision: "allow" | "deny") => {
    setBusy(decision);
    setError(null);
    api.decideShip(projectId, card.ship_id, decision, typed).then(
      () => undefined, // the panel updates from the ship's events
      (failure) => {
        setError(errorMessage(failure));
        setBusy(null);
      },
    );
  };

  return (
    <section className="ship-card" aria-label="Allow this change to go live">
      <div className="ship-card-head">
        <Icon name="hand" size={15} />
        <strong>Allow this change to go live?</strong>
      </div>
      <dl className="ship-facts">
        <dt>How</dt>
        <dd>{card.method_label}</dd>
        <dt>Files</dt>
        <dd>
          <ul className="ship-files">
            {shown.map((file) => (
              <li key={file.path}>
                <span className={`ship-fs is-${fileStatus(file.status)}`}>{file.status[0].toUpperCase()}</span>
                <code className={card.protected.includes(file.path) ? "is-protected" : undefined}>{file.path}</code>
              </li>
            ))}
          </ul>
          {(card.files.length > 12 || card.files_more > 0) && !allFiles && (
            <button type="button" className="ship-link" onClick={() => setAllFiles(true)}>
              Show all {card.files.length + card.files_more}
            </button>
          )}
          {allFiles && card.files_more > 0 && <span className="ship-step-detail">and {card.files_more} more</span>}
        </dd>
        <dt>Checks</dt>
        <dd>
          {card.checks.length === 0
            ? "None in the recipe."
            : `${card.checks.length} passed in ${card.method === "none" ? "the folder" : "the copy"}: ${card.checks
                .map((check) => check.command)
                .join("; ")}`}
        </dd>
        <dt>Writes</dt>
        <dd>
          <ul className="ship-lines">
            {card.writes.map((line) => (
              <li key={line}>{line}</li>
            ))}
          </ul>
        </dd>
        <dt>Then runs</dt>
        <dd>
          {card.commands.length === 0 ? (
            card.method === "branch" ? "Nothing: a branch left there changes nothing that runs." : "No commands."
          ) : (
            <ul className="ship-lines">
              {card.commands.map((item, index) => (
                <li key={index}>
                  <code>{item.command}</code> <span className="ship-step-detail">in {item.where}</span>
                </li>
              ))}
            </ul>
          )}
        </dd>
        {card.address && (
          <>
            <dt>Address</dt>
            <dd>
              <code>{card.address}</code> must answer afterwards
            </dd>
          </>
        )}
        <dt>Going back</dt>
        <dd>{card.going_back}</dd>
      </dl>
      {card.protected.length > 0 && (
        <div className="ship-protected" role="note">
          <Icon name="shield" size={14} />
          <div>
            <p>
              <strong>Protected {card.protected.length === 1 ? "path" : "paths"}:</strong> {card.protected.join(", ")}
            </p>
            <label className="field-label" htmlFor={nameId}>
              Type the project name “{card.project_name}” to allow
            </label>
            <input
              id={nameId}
              className="text-input"
              value={typed}
              autoComplete="off"
              onChange={(event) => setTyped(event.target.value)}
            />
          </div>
        </div>
      )}
      <ul className="ship-limits">
        {card.limits.map((line) => (
          <li key={line}>{line}</li>
        ))}
      </ul>
      {error && (
        <p className="ship-error" role="alert">
          {error}
        </p>
      )}
      <div className="ship-actions">
        <button type="button" className="btn" onClick={() => decide("deny")} disabled={busy !== null}>
          {busy === "deny" ? <Spinner size={13} /> : null}
          Don’t allow
        </button>
        <button
          type="button"
          className="btn btn-primary"
          onClick={() => decide("allow")}
          disabled={busy !== null || !nameOk}
        >
          {busy === "allow" ? <Spinner size={13} /> : <Icon name="check" size={14} />}
          Allow and make it live
        </button>
      </div>
    </section>
  );
}

// ---- result and history ---------------------------------------------------------------
function Result({ record }: { record: ShipRecord }) {
  const text = resultText(record.result!, record.plan.method);
  const help = record.result === "shipped" && record.plan.method === "branch" ? record.message : null;
  return (
    <section className={`ship-result is-${text.tone}`} aria-label="Last ship">
      <div className="ship-result-head">
        <span className={`ship-chip is-${text.tone}`}>{text.label}</span>
        <span className="ship-step-detail">
          {METHOD_SHORT[record.plan.method]} · {record.finished_at ? relativeTime(record.finished_at) : ""}
        </span>
      </div>
      {help ? <MergeHelp text={help} /> : <p>{record.message}</p>}
    </section>
  );
}

/** Branch way: the message holds commands in backticks; show them as code. */
function MergeHelp({ text }: { text: string }) {
  const parts = text.split("`");
  return (
    <p className="ship-merge-help">
      {parts.map((part, index) => (index % 2 ? <code key={index}>{part}</code> : <span key={index}>{part}</span>))}
    </p>
  );
}

function HistoryItem({ record }: { record: ShipRecord }) {
  const [open, setOpen] = useState(false);
  const text = record.result ? resultText(record.result, record.plan.method) : { label: "Running", tone: "muted" as const };
  const count = record.files.length + record.files_more;
  return (
    <li className="ship-history-item">
      <button type="button" className="ship-history-row" aria-expanded={open} onClick={() => setOpen(!open)}>
        <span className={`ship-chip is-${text.tone}`}>{text.label}</span>
        <span className="ship-history-what">
          {METHOD_SHORT[record.plan.method]} · {count} {count === 1 ? "file" : "files"} ·{" "}
          {record.by === "agent" ? "asked in chat" : "button"}
        </span>
        <span className="ship-step-detail">{relativeTime(record.started_at)}</span>
      </button>
      {open && (
        <div className="ship-history-detail">
          {record.message && (record.plan.method === "branch" && record.result === "shipped" ? <MergeHelp text={record.message} /> : <p>{record.message}</p>)}
          <Progress steps={progressSteps(record, null)} />
          <p className="ship-step-detail">
            {record.plan.head.slice(0, 7)} · started {new Date(record.started_at).toLocaleString()} · record {record.id}
          </p>
        </div>
      )}
    </li>
  );
}

// ---- the recipe editor ----------------------------------------------------------------
function RecipeEditor({ project, view, onSaved }: { project: Project; view: ShipView; onSaved: () => void }) {
  const [form, setForm] = useState<RecipeForm>(() => recipeForm(view.recipe!));
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const ids = { checks: useId(), after: useId(), address: useId(), protected: useId(), back: useId(), timeout: useId(), method: useId() };
  const set = (key: keyof RecipeForm) => (event: { target: { value: string } }) =>
    setForm((value) => ({ ...value, [key]: event.target.value }));
  const running = Boolean(view.running);

  const save = () => {
    const { recipe, error: problem } = formRecipe(form);
    if (!recipe) {
      setError(problem);
      return;
    }
    setSaving(true);
    setError(null);
    api
      .setShipRecipe(project.id, recipe)
      .then(onSaved, (failure) => setError(errorMessage(failure)))
      .finally(() => setSaving(false));
  };

  return (
    <div className="ship-body ship-recipe" role="tabpanel">
      <p className="ship-note">
        {view.confirmed
          ? `${confirmedBy(view.confirmed_by)}${view.confirmed_at ? ` ${relativeTime(view.confirmed_at)}` : ""}. Saving a change here confirms it.`
          : "Not set yet. These are defaults; nothing runs until you save and press Make it live."}
      </p>
      <label className="field-label" htmlFor={ids.method}>
        How the change reaches the original
      </label>
      <select id={ids.method} className="text-input" value={form.method} onChange={set("method")} disabled={view.methods.length < 2}>
        {view.methods.map((method) => (
          <option key={method} value={method}>
            {METHOD_LABEL[method]}
          </option>
        ))}
      </select>
      {form.method === "merge" && (
        <p className="field-hint">Fast-forward only. Refused while the original has uncommitted changes or has moved on.</p>
      )}
      <label className="field-label" htmlFor={ids.checks}>
        Checks <span className="field-optional">run in the copy first, one per line</span>
      </label>
      <textarea id={ids.checks} className="text-input is-mono ship-textarea" rows={2} value={form.checks} onChange={set("checks")} placeholder="npm test" />
      <label className="field-label" htmlFor={ids.after}>
        After-commands <span className="field-optional">run in the original, one per line</span>
      </label>
      <textarea id={ids.after} className="text-input is-mono ship-textarea" rows={2} value={form.after} onChange={set("after")} placeholder="npm run build" />
      {form.method === "branch" && <p className="field-hint">Not run when a branch is only left there.</p>}
      <label className="field-label" htmlFor={ids.address}>
        Address to check afterwards <span className="field-optional">optional</span>
      </label>
      <input id={ids.address} className="text-input is-mono" value={form.address} onChange={set("address")} placeholder="http://127.0.0.1:3000/" />
      <label className="field-label" htmlFor={ids.protected}>
        Paths that need you <span className="field-optional">patterns, one per line</span>
      </label>
      <textarea id={ids.protected} className="text-input is-mono ship-textarea" rows={2} value={form.protected} onChange={set("protected")} placeholder={".env*\nmigrations/"} />
      <details className="ship-more">
        <summary>Going back and time limit</summary>
        <label className="field-label" htmlFor={ids.back}>
          Commands after going back <span className="field-optional">empty: the after-commands again</span>
        </label>
        <textarea id={ids.back} className="text-input is-mono ship-textarea" rows={2} value={form.back} onChange={set("back")} />
        <label className="field-label" htmlFor={ids.timeout}>
          Time limit per command (seconds)
        </label>
        <input id={ids.timeout} className="text-input" inputMode="numeric" value={form.timeout} onChange={set("timeout")} />
      </details>
      {form.notes && <p className="field-hint">Notes: {form.notes}</p>}
      {error && (
        <p className="ship-error" role="alert">
          {error}
        </p>
      )}
      <div className="ship-actions">
        <button type="button" className="btn btn-primary" onClick={save} disabled={saving || running}>
          {saving ? <Spinner size={13} /> : <Icon name="check" size={14} />}
          {view.confirmed ? "Save the recipe" : "Save and confirm"}
        </button>
      </div>
    </div>
  );
}
