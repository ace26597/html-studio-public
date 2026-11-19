// Context from this computer: the dialog that adds a folder, a file or an address to a project as
// read-only context (check first, then add), and its edit mode (label and note only).
// The state is a reducer in context.ts, so the flow can be tested without a browser.

import { useEffect, useId, useReducer, useRef, useState } from "react";
import { api, errorMessage } from "./api";
import {
  addBlocker,
  addContextReducer,
  addContextRequest,
  canAdd,
  canCheck,
  CONTEXT_LIMIT,
  contextWhere,
  editContextPatch,
  emptyAddContext,
  enterAction,
  FIELD_EXAMPLE,
  FIELD_LABEL,
  foundSummary,
  isChecked,
  kindIcon,
  kindName,
  LABEL_MAX,
  labelNoteProblem,
  NOTE_MAX,
  topLine,
  type AddContextAction,
  type AddContextState,
} from "./context";
import { Icon, Spinner } from "./icons";
import { Modal } from "./Modal";
import type { ContextItem } from "./types";

interface ContextDialogProps {
  projectId: string;
  /** The item to edit (label and note), or null to add one. */
  item: ContextItem | null;
  /** How many items the project has now (Add is not offered at the limit). */
  count?: number;
  onSaved: (item: ContextItem) => void;
  onClose: () => void;
}

export function ContextDialog(props: ContextDialogProps) {
  return props.item ? <EditContext {...props} item={props.item} /> : <AddContext {...props} />;
}

// ---- add -------------------------------------------------------------------------------

function AddContext({ projectId, count = 0, onSaved, onClose }: Omit<ContextDialogProps, "item">) {
  const [state, dispatch] = useReducer(addContextReducer, emptyAddContext);
  const controller = useRef<AbortController | null>(null);
  useEffect(() => () => controller.current?.abort(), []);

  const check = () => {
    const value = state.value.trim();
    if (!canCheck(state)) return;
    controller.current?.abort();
    const current = new AbortController();
    controller.current = current;
    dispatch({ type: "check-start" });
    api.inspectContext(value, current.signal).then(
      (inspection) => dispatch({ type: "check-done", value, inspection }),
      (cause) => {
        if ((cause as Error)?.name === "AbortError") return;
        dispatch({ type: "check-failed", message: errorMessage(cause) });
      },
    );
  };

  const add = async () => {
    if (!canAdd(state) || count >= CONTEXT_LIMIT) return;
    dispatch({ type: "add-start" });
    try {
      onSaved(await api.addProjectContext(projectId, addContextRequest(state)));
    } catch (cause) {
      dispatch({ type: "add-failed", message: errorMessage(cause) });
    }
  };

  return (
    <Modal title="Add context" onClose={onClose} busy={state.adding} wide>
      <AddContextView
        state={state}
        dispatch={dispatch}
        full={count >= CONTEXT_LIMIT}
        onCheck={check}
        onAdd={() => void add()}
        onCancel={onClose}
      />
    </Modal>
  );
}

interface AddContextViewProps {
  state: AddContextState;
  dispatch: (action: AddContextAction) => void;
  /** The project already has the most items it can hold. */
  full?: boolean;
  onCheck: () => void;
  onAdd: () => void;
  onCancel: () => void;
}

/** The dialog's body for a given state: what is drawn is decided here, the requests are made above. */
export function AddContextView({ state, dispatch, full = false, onCheck, onAdd, onCancel }: AddContextViewProps) {
  const ids = { value: useId(), hint: useId(), label: useId(), note: useId(), error: useId() };
  const checked = isChecked(state);
  const inspection = checked ? state.inspection : null;
  const refused = inspection?.refused ?? null;
  const blocker = addBlocker(state);
  const fieldProblem = inspection && !refused ? labelNoteProblem(state.label, state.note, { labelRequired: true }) : null;
  const summary = inspection && !refused ? foundSummary(inspection.kind, inspection.found) : "";
  const top = inspection && !refused && inspection.kind === "folder" ? topLine(inspection.found) : "";
  const where = inspection ? contextWhere(inspection) : "";
  const addable = canAdd(state) && !full;

  return (
    <form
      className="ctx-form"
      onSubmit={(event) => {
        event.preventDefault();
        if (addable) onAdd();
      }}
    >
      <p className="dialog-body ctx-lede">
        Point this project at something on this computer. The agent reads it when your questions are about it, and
        changes nothing there. Nothing is copied.
      </p>

      <label className="field-label" htmlFor={ids.value}>
        {FIELD_LABEL}
      </label>
      <div className="attach-path-row">
        <input
          id={ids.value}
          className="text-input is-mono"
          value={state.value}
          placeholder={FIELD_EXAMPLE}
          spellCheck={false}
          autoCapitalize="off"
          autoCorrect="off"
          autoComplete="off"
          data-autofocus
          disabled={state.adding}
          aria-describedby={ids.hint}
          onChange={(event) => dispatch({ type: "value", value: event.target.value })}
          onKeyDown={(event) => {
            if (event.key !== "Enter" || event.nativeEvent.isComposing) return;
            event.preventDefault();
            const action = enterAction(state);
            if (action === "check") onCheck();
            else if (action === "add" && addable) onAdd();
          }}
        />
        <button type="button" className="btn" disabled={!canCheck(state)} onClick={onCheck}>
          {state.checking && <Spinner size={14} />}
          Check
        </button>
      </div>
      <p id={ids.hint} className="field-hint">
        A full path (it starts with / or ~) or the address of a running app. A browser cannot look through your disk, so
        type it.
      </p>

      {state.error && (
        <p className="template-error" role="alert">
          <Icon name="alert" size={15} />
          <span>{state.error}</span>
        </p>
      )}

      {refused && (
        <p className="template-error ctx-refused" role="alert">
          <Icon name="alert" size={15} />
          <span>{refused}</span>
        </p>
      )}

      {inspection && !refused && (
        <>
          <dl className="attach-facts ctx-facts" aria-label="What the studio found">
            <div>
              <dt>{kindName(inspection.kind)}</dt>
              <dd>
                <code title={where}>{where}</code>
              </dd>
            </div>
            {summary && (
              <div>
                <dt>Found</dt>
                <dd>{summary}</dd>
              </div>
            )}
            {top && (
              <div>
                <dt>At the top</dt>
                <dd>
                  <code className="ctx-top">{top}</code>
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

          <label className="field-label ctx-field" htmlFor={ids.label}>
            Name
          </label>
          <input
            id={ids.label}
            className={`text-input${fieldProblem && !state.label.trim() ? " is-invalid" : ""}`}
            value={state.label}
            maxLength={LABEL_MAX}
            disabled={state.adding}
            onChange={(event) => dispatch({ type: "label", value: event.target.value })}
          />

          <label className="field-label ctx-field" htmlFor={ids.note}>
            What is it? <span className="field-optional">optional</span>
          </label>
          <textarea
            id={ids.note}
            className="text-input ctx-note"
            rows={3}
            value={state.note}
            maxLength={NOTE_MAX}
            placeholder="In your own words; the agent is given it. For example: Our CRM. The API is in api/."
            disabled={state.adding}
            onChange={(event) => dispatch({ type: "note", value: event.target.value })}
          />
          {fieldProblem && (
            <p className="field-hint is-error" id={ids.error} role="alert">
              {fieldProblem}
            </p>
          )}
        </>
      )}

      {full && (
        <p className="field-hint is-error" role="alert">
          A project can hold at most {CONTEXT_LIMIT} items. Remove one in the Context tab to add another.
        </p>
      )}

      <div className="dialog-actions">
        <button type="button" className="btn" disabled={state.adding} onClick={onCancel}>
          Cancel
        </button>
        <button
          type="submit"
          className="btn btn-primary"
          disabled={!addable}
          title={!addable && blocker && !refused ? blocker : undefined}
        >
          {state.adding && <Spinner size={14} />}
          {state.adding ? "Adding…" : "Add"}
        </button>
      </div>
    </form>
  );
}

// ---- edit ------------------------------------------------------------------------------

function EditContext({ projectId, item, onSaved, onClose }: ContextDialogProps & { item: ContextItem }) {
  const [label, setLabel] = useState(item.label);
  const [note, setNote] = useState(item.note);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const ids = { label: useId(), note: useId() };
  const problem = labelNoteProblem(label, note, { labelRequired: true });
  const where = contextWhere(item);

  const save = async () => {
    if (problem || saving) return;
    const patch = editContextPatch(item, label, note);
    if (!Object.keys(patch).length) {
      onClose();
      return;
    }
    setSaving(true);
    setError(null);
    try {
      onSaved(await api.updateProjectContext(projectId, item.id, patch));
    } catch (cause) {
      setError(errorMessage(cause));
      setSaving(false);
    }
  };

  return (
    <Modal title={`Edit ${item.label}`} onClose={onClose} busy={saving}>
      <form
        className="ctx-form"
        onSubmit={(event) => {
          event.preventDefault();
          void save();
        }}
      >
        <p className="ctx-where">
          <Icon name={kindIcon(item.kind)} size={14} />
          <code title={where}>{where}</code>
        </p>
        <label className="field-label ctx-field" htmlFor={ids.label}>
          Name
        </label>
        <input
          id={ids.label}
          className={`text-input${!label.trim() ? " is-invalid" : ""}`}
          value={label}
          maxLength={LABEL_MAX}
          data-autofocus
          disabled={saving}
          onChange={(event) => {
            setLabel(event.target.value);
            setError(null);
          }}
        />
        <label className="field-label ctx-field" htmlFor={ids.note}>
          What is it? <span className="field-optional">optional</span>
        </label>
        <textarea
          id={ids.note}
          className="text-input ctx-note"
          rows={3}
          value={note}
          maxLength={NOTE_MAX}
          disabled={saving}
          onChange={(event) => {
            setNote(event.target.value);
            setError(null);
          }}
        />
        <p className="field-hint">To point it somewhere else, remove it and add the new path or address.</p>
        {problem && (
          <p className="field-hint is-error" role="alert">
            {problem}
          </p>
        )}
        {error && (
          <p className="template-error" role="alert">
            <Icon name="alert" size={15} />
            <span>{error}</span>
          </p>
        )}
        <div className="dialog-actions">
          <button type="button" className="btn" disabled={saving} onClick={onClose}>
            Cancel
          </button>
          <button type="submit" className="btn btn-primary" disabled={Boolean(problem) || saving}>
            {saving && <Spinner size={14} />}
            {saving ? "Saving…" : "Save"}
          </button>
        </div>
      </form>
    </Modal>
  );
}
