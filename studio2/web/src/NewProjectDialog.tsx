// New page: choose what the project starts from (`GET /api/templates`).

import { useEffect, useId, useRef, useState } from "react";
import { api, errorMessage } from "./api";
import { AttachForm } from "./AttachForm";
import { Icon, Spinner } from "./icons";
import { groupTemplates, templateKindLabel, templateOrigin, templatePreviewSrc, templateStartLine } from "./templates";
import type { AttachRequest, Template } from "./types";

interface NewProjectDialogProps {
  creating: boolean;
  /** Error from creating the project, shown in the dialog. */
  createError: string | null;
  onCreate: (template: string | null) => void;
  /** Step 3k: create a project from an app on this computer. */
  onAttach: (request: AttachRequest) => void;
  onClose: () => void;
}

type Source = "template" | "folder";

const DEFAULT_TEMPLATE = "blank";

export function NewProjectDialog({ creating, createError, onCreate, onAttach, onClose }: NewProjectDialogProps) {
  const [source, setSource] = useState<Source>("template");
  const [attachRequest, setAttachRequest] = useState<AttachRequest | null>(null);
  const [attachBlocker, setAttachBlocker] = useState<string | null>(null);
  const submitAttach = () => {
    if (!creating && attachRequest) onAttach(attachRequest);
  };
  const [templates, setTemplates] = useState<Template[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);
  const [chosen, setChosen] = useState<string>(DEFAULT_TEMPLATE);
  const dialog = useRef<HTMLDivElement>(null);
  const restoreFocus = useRef<HTMLElement | null>(null);
  const titleId = useId();

  useEffect(() => {
    restoreFocus.current = document.activeElement as HTMLElement | null;
    return () => restoreFocus.current?.focus?.();
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    setLoadError(null);
    api.listTemplates(controller.signal).then(
      (list) => {
        setTemplates(list);
        setChosen((current) =>
          list.some((item) => item.id === current) ? current : (list.find((item) => item.id === DEFAULT_TEMPLATE)?.id ?? list[0]?.id ?? DEFAULT_TEMPLATE),
        );
      },
      (cause) => {
        if ((cause as Error)?.name !== "AbortError") setLoadError(errorMessage(cause));
      },
    );
    return () => controller.abort();
  }, [attempt]);

  // Focus the chosen card once the list is there.
  useEffect(() => {
    if (!templates || source !== "template") return;
    dialog.current?.querySelector<HTMLElement>('[role="radio"][aria-checked="true"]')?.focus();
  }, [templates, source]);

  // Arrow keys move through both groups as one list.
  const groups = groupTemplates(templates ?? []);
  const ordered = [...groups.builtIn, ...groups.yours];
  const chosenTemplate = ordered.find((item) => item.id === chosen) ?? null;
  const startLine = chosenTemplate ? templateStartLine(chosenTemplate) : null;

  const onCardKey = (event: React.KeyboardEvent, index: number) => {
    if (!templates) return;
    const templates_ = ordered;
    const keys: Record<string, number> = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 };
    if (event.key in keys) {
      event.preventDefault();
      const next = (index + keys[event.key] + templates_.length) % templates_.length;
      setChosen(templates_[next].id);
      dialog.current?.querySelectorAll<HTMLElement>('[role="radio"]')[next]?.focus();
    } else if (event.key === "Enter") {
      event.preventDefault();
      if (!creating) onCreate(templates_[index].id);
    }
  };

  const card = (template: Template, index: number) => {
    const checked = template.id === chosen;
    const preview = templatePreviewSrc(template);
    const origin = templateOrigin(template);
    const yours = template.source === "yours";
    return (
      <button
        key={template.id}
        type="button"
        role="radio"
        aria-checked={checked}
        tabIndex={checked ? 0 : -1}
        className={`template-card${checked ? " is-checked" : ""}${yours ? " is-yours" : ""}`}
        onClick={() => setChosen(template.id)}
        onDoubleClick={() => !creating && onCreate(template.id)}
        onKeyDown={(event) => onCardKey(event, index)}
      >
        {yours && (
          <span className="template-thumb" aria-hidden="true">
            {preview ? <img src={preview} alt="" loading="lazy" /> : <Icon name="page" size={22} />}
          </span>
        )}
        <span className="template-top">
          <span className="template-label">{template.label}</span>
          {!yours && template.app && <span className="badge badge-app">Has a back end</span>}
        </span>
        {yours ? (
          <span className="template-description">
            {templateKindLabel(template)}
            {origin ? ` · ${origin}` : ""}
          </span>
        ) : (
          <span className="template-description">{template.description}</span>
        )}
      </button>
    );
  };

  return (
    <div
      className="dialog-backdrop"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget && !creating) onClose();
      }}
      onKeyDown={(event) => {
        if (event.key === "Escape") {
          event.stopPropagation();
          if (!creating) onClose();
        }
        if (event.key === "Tab") {
          const focusable = Array.from(
            dialog.current?.querySelectorAll<HTMLElement>(
              'button:not(:disabled), [tabindex="0"], a[href], input:not(:disabled)',
            ) ?? [],
          );
          if (!focusable.length) return;
          const index = focusable.indexOf(document.activeElement as HTMLElement);
          event.preventDefault();
          const next = (index + (event.shiftKey ? focusable.length - 1 : 1) + focusable.length) % focusable.length;
          focusable[next]?.focus();
        }
      }}
    >
      <div className="dialog dialog-wide" role="dialog" aria-modal="true" aria-labelledby={titleId} ref={dialog}>
        <h2 id={titleId}>New page</h2>
        <div className="newpage-tabs" role="tablist" aria-label="Start from">
          {(
            [
              ["template", "A template or a blank page"],
              ["folder", "An app on this computer"],
            ] as const
          ).map(([value, label]) => (
            <button
              key={value}
              type="button"
              role="tab"
              aria-selected={source === value}
              className={`newpage-tab${source === value ? " is-on" : ""}`}
              disabled={creating}
              onClick={() => setSource(value)}
            >
              {label}
            </button>
          ))}
        </div>
        {source === "folder" ? (
          <AttachForm
            disabled={creating}
            onReady={(request, blocker) => {
              setAttachRequest(request);
              setAttachBlocker(blocker);
            }}
            onSubmit={submitAttach}
          />
        ) : (
        <>
        <p className="dialog-body">Choose what it starts from. You can change anything afterwards by asking the agent.</p>

        {templates === null && !loadError && (
          <div className="template-loading">
            <Spinner size={16} />
          </div>
        )}

        {loadError && (
          <div className="template-error" role="alert">
            <Icon name="alert" size={15} />
            <span>The starting points couldn’t be loaded: {loadError}</span>
            <button type="button" className="btn btn-small" onClick={() => setAttempt((n) => n + 1)}>
              Try again
            </button>
          </div>
        )}

        {templates && templates.length === 0 && (
          <p className="dialog-body">The studio has no templates; the page starts blank.</p>
        )}

        {templates && templates.length > 0 && (
          <div className="template-groups" role="radiogroup" aria-label="Start from">
            <h3 className="template-group-title">Start from</h3>
            <div className="template-grid">
              {groups.builtIn.map((template) => card(template, ordered.indexOf(template)))}
            </div>
            {groups.yours.length > 0 && (
              <>
                <h3 className="template-group-title">Your templates</h3>
                <div className="template-grid">
                  {groups.yours.map((template) => card(template, ordered.indexOf(template)))}
                </div>
              </>
            )}
            {startLine && <p className="template-start-line">{startLine}</p>}
          </div>
        )}
        </>
        )}

        {createError && (
          <p className="template-error" role="alert">
            <Icon name="alert" size={15} />
            <span>{createError}</span>
          </p>
        )}

        <div className="dialog-actions">
          <button type="button" className="btn" disabled={creating} onClick={onClose}>
            Cancel
          </button>
          {source === "folder" ? (
            <button
              type="button"
              className="btn btn-primary"
              disabled={creating || !attachRequest}
              title={attachBlocker ?? undefined}
              onClick={submitAttach}
            >
              {creating && <Spinner size={14} />}
              {creating ? (attachRequest?.mode === "copy" ? "Copying…" : "Creating…") : "Create"}
            </button>
          ) : (
            <button
              type="button"
              className="btn btn-primary"
              disabled={creating || (templates === null && !loadError)}
              onClick={() => onCreate(templates && templates.length ? chosen : null)}
            >
              {creating && <Spinner size={14} />}
              {loadError ? "Create a blank page" : "Create"}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
