// The person's templates on the Library page: export, rename, delete and import.

import { useCallback, useEffect, useId, useReducer, useRef, useState } from "react";
import { ApiError, api, errorMessage } from "./api";
import { formatBytes } from "./format";
import { Icon, Spinner } from "./icons";
import { matchesQuery } from "./library";
import { Modal } from "./Modal";
import {
  groupTemplates,
  idleImport,
  importReducer,
  templateKindLabel,
  templateOrigin,
  templatePreviewSrc,
} from "./templates";
import type { Template } from "./types";
import { useConfirm, useToast } from "./ui";

function EditTemplateDialog({
  template,
  onSaved,
  onClose,
}: {
  template: Template;
  onSaved: (template: Template) => void;
  onClose: () => void;
}) {
  const [label, setLabel] = useState(template.label);
  const [description, setDescription] = useState(template.description ?? "");
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const ids = useId();

  const save = async () => {
    if (!label.trim()) {
      setError("Give the template a name.");
      return;
    }
    setSaving(true);
    setError(null);
    try {
      onSaved(await api.patchTemplate(template.id, { label: label.trim(), description: description.trim() }));
    } catch (cause) {
      setError(errorMessage(cause));
      setSaving(false);
    }
  };

  return (
    <Modal title={`Edit ${template.label}`} onClose={onClose} busy={saving} wide>
      <form
        className="ds-form"
        onSubmit={(event) => {
          event.preventDefault();
          void save();
        }}
      >
        <label className="field" htmlFor={`${ids}-label`}>
          <span>Name</span>
          <input id={`${ids}-label`} data-autofocus value={label} maxLength={80} onChange={(event) => setLabel(event.target.value)} />
        </label>
        <label className="field" htmlFor={`${ids}-description`}>
          <span>What should stay the same when this is reused?</span>
          <textarea
            id={`${ids}-description`}
            className="field-textarea"
            rows={3}
            value={description}
            onChange={(event) => setDescription(event.target.value)}
          />
        </label>
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

/** `guest`: someone on a shared studio who is not its owner; templates can be used and exported, not changed. */
export function TemplatesManager({
  query = "",
  heading,
  guest = false,
}: { query?: string; heading?: string; guest?: boolean } = {}) {
  const toast = useToast();
  const confirm = useConfirm();
  const [templates, setTemplates] = useState<Template[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);
  const [editing, setEditing] = useState<Template | null>(null);
  const [importState, dispatchImport] = useReducer(importReducer, idleImport);
  const fileInput = useRef<HTMLInputElement>(null);

  useEffect(() => {
    const controller = new AbortController();
    setError(null);
    api.listTemplates(controller.signal).then(
      (list) => setTemplates(groupTemplates(list).yours),
      (cause) => {
        if ((cause as Error)?.name !== "AbortError") setError(errorMessage(cause));
      },
    );
    return () => controller.abort();
  }, [attempt]);
  const refresh = useCallback(() => setAttempt((n) => n + 1), []);
  // The Library's search reaches your templates too.
  const shown = templates && query.trim() ? templates.filter((t) => matchesQuery([t.label, t.description, templateKindLabel(t)], query)) : templates;

  // The import flow: upload; when the server asks (the template has code), ask the person, then upload confirmed.
  const runImport = async (file: File, confirmed: boolean): Promise<void> => {
    try {
      const template = await api.importTemplate(file, confirmed);
      dispatchImport({ type: "done" });
      toast({ message: `Imported “${template.label}”`, kind: "success" });
      refresh();
    } catch (cause) {
      if (cause instanceof ApiError && cause.code === "needs_confirmation" && !confirmed) {
        dispatchImport({ type: "needs-confirmation", message: cause.message });
        const ok = await confirm({
          title: "Import a template that contains code?",
          body: cause.message,
          confirmLabel: "Import anyway",
          danger: true,
        });
        if (!ok) {
          dispatchImport({ type: "cancel" });
          return;
        }
        dispatchImport({ type: "confirm" });
        await runImport(file, true);
        return;
      }
      dispatchImport({ type: "done" });
      toast({ message: errorMessage(cause), kind: "error" });
    }
  };

  const remove = async (template: Template) => {
    const ok = await confirm({
      title: `Delete the template “${template.label}”?`,
      body: "Projects that already use it keep their copy.",
      confirmLabel: "Delete",
      danger: true,
    });
    if (!ok) return;
    try {
      await api.deleteTemplate(template.id);
      toast({ message: `Deleted “${template.label}”` });
      refresh();
    } catch (cause) {
      toast({ message: errorMessage(cause), kind: "error" });
    }
  };

  const searching = query.trim() !== "";
  // While the Library is searched, say nothing unless one of your templates matches.
  if (searching && !(shown && shown.length > 0)) return null;

  const body = (
    <div className="ds">
      <div className="ds-head">
        <p className="ds-lede">
          Saved from your projects (“Save as template…” in a project’s menu) or imported from a file. Start a new page from
          one, or let a project follow one.
        </p>
        <input
          ref={fileInput}
          type="file"
          accept=".zip,application/zip"
          hidden
          onChange={(event) => {
            const file = event.target.files?.[0];
            event.target.value = "";
            if (!file || importState.phase !== "idle") return;
            dispatchImport({ type: "pick", file });
            void runImport(file, false);
          }}
        />
        {guest ? (
          <p className="ds-lede">On a shared studio only its owner saves, imports, edits or deletes templates.</p>
        ) : (
          <button
            type="button"
            className="btn btn-small"
            disabled={importState.phase !== "idle"}
            onClick={() => fileInput.current?.click()}
          >
            {importState.phase === "idle" ? <Icon name="upload" size={14} /> : <Spinner size={12} />}
            {importState.phase === "idle" ? "Import" : "Importing…"}
          </button>
        )}
      </div>
      {error && (
        <div className="lib-error" role="alert">
          <Icon name="alert" size={15} />
          <span>Your templates couldn’t be loaded: {error}</span>
          <button type="button" className="btn btn-small" onClick={refresh}>
            Try again
          </button>
        </div>
      )}
      {!templates && !error && (
        <div className="panel-empty">
          <Spinner />
        </div>
      )}
      {templates && templates.length === 0 && <p className="lib-empty">You have no templates yet.</p>}
      {shown && shown.length > 0 && (
        <ul className="ds-list">
          {shown.map((template) => {
            const preview = templatePreviewSrc(template);
            const origin = templateOrigin(template);
            return (
              <li key={template.id} className="ds-item tpl-item">
                <span className="tpl-thumb" aria-hidden="true">
                  {preview ? <img src={preview} alt="" loading="lazy" /> : <Icon name="page" size={20} />}
                </span>
                <div className="ds-item-main">
                  <span className="ds-item-name">{template.label}</span>
                  <span className="ds-item-meta">
                    {templateKindLabel(template)}
                    {origin ? ` · ${origin}` : ""}
                    {typeof template.files === "number" ? ` · ${template.files} ${template.files === 1 ? "file" : "files"}` : ""}
                    {typeof template.bytes === "number" ? ` · ${formatBytes(template.bytes)}` : ""}
                    {template.has_code ? " · contains code" : ""}
                  </span>
                  {template.description && <span className="lib-row-detail">{template.description}</span>}
                </div>
                <div className="ds-item-actions">
                  <a className="btn btn-small" href={api.templateExportUrl(template.id)} aria-label={`Export ${template.label}`}>
                    Export
                  </a>
                  {!guest && (
                    <>
                      <button
                        type="button"
                        className="btn btn-small"
                        aria-label={`Edit ${template.label}`}
                        onClick={() => setEditing(template)}
                      >
                        Edit
                      </button>
                      <button
                        type="button"
                        className="icon-btn"
                        aria-label={`Delete ${template.label}`}
                        data-tip="Delete"
                        data-tip-align="end"
                        onClick={() => void remove(template)}
                      >
                        <Icon name="trash" size={15} />
                      </button>
                    </>
                  )}
                </div>
              </li>
            );
          })}
        </ul>
      )}
      {editing && (
        <EditTemplateDialog
          template={editing}
          onClose={() => setEditing(null)}
          onSaved={(saved) => {
            setEditing(null);
            toast({ message: `Saved “${saved.label}”`, kind: "success" });
            refresh();
          }}
        />
      )}
    </div>
  );
  if (!heading) return body;
  return (
    <section className="lib-section" aria-label={heading}>
      <h3 className="lib-title">{heading}</h3>
      {body}
    </section>
  );
}
