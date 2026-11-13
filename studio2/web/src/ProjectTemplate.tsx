// The template a project follows (its files are in `template/`): show it, remove it, or choose one.

import { useEffect, useId, useState } from "react";
import { api, errorMessage } from "./api";
import { Icon, Spinner } from "./icons";
import { groupTemplates, templateKindLabel } from "./templates";
import type { Project, Template } from "./types";

interface ProjectTemplateProps {
  project: Project;
  running: boolean;
  onProjectChange: (project: Project) => void;
}

export function ProjectTemplate({ project, running, onProjectChange }: ProjectTemplateProps) {
  const current = project.uses_template ?? null;
  const [yours, setYours] = useState<Template[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [picked, setPicked] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const selectId = useId();

  useEffect(() => {
    const controller = new AbortController();
    api.listTemplates(controller.signal).then(
      (list) => setYours(groupTemplates(list).yours),
      (cause) => {
        if ((cause as Error)?.name !== "AbortError") setLoadError(errorMessage(cause));
      },
    );
    return () => controller.abort();
  }, []);

  const act = async (work: () => Promise<Project>) => {
    setBusy(true);
    setError(null);
    try {
      onProjectChange(await work());
      setPicked("");
    } catch (cause) {
      setError(errorMessage(cause));
    } finally {
      setBusy(false);
    }
  };

  const reason = running ? "Wait for the agent to finish to change the template." : null;
  const choices = (yours ?? []).filter((item) => item.id !== current?.id);

  return (
    <section className="ptpl" aria-labelledby={`${selectId}-title`}>
      <h3 id={`${selectId}-title`} className="lib-title">
        Template
      </h3>
      {current ? (
        <div className="ptpl-current">
          <Icon name="page" size={15} />
          <span className="ptpl-label">
            Template: <strong>{current.label}</strong>
            <span className="dim"> · {templateKindLabel({ kind: current.kind, app: current.kind === "app" })}</span>
          </span>
          <button
            type="button"
            className="btn btn-small"
            disabled={busy || running}
            aria-description={reason ?? undefined}
            onClick={() => void act(() => api.stopUsingTemplate(project.id))}
          >
            Remove
          </button>
        </div>
      ) : (
        <p className="ds-lede">No template. The assistant builds this project from your messages alone.</p>
      )}
      {loadError && <p className="ds-error">Your templates couldn’t be loaded: {loadError}</p>}
      {yours && yours.length === 0 && !current && (
        <p className="lib-empty">You have no templates yet. Use “Save as template…” on a project you like.</p>
      )}
      {choices.length > 0 && (
        <div className="ptpl-pick">
          <label htmlFor={selectId} className="sr-only">
            Use a template
          </label>
          <select id={selectId} value={picked} disabled={busy || running} onChange={(event) => setPicked(event.target.value)}>
            <option value="">{current ? "Use another template…" : "Use a template…"}</option>
            {choices.map((item) => (
              <option key={item.id} value={item.id}>
                {item.label} ({templateKindLabel(item)})
              </option>
            ))}
          </select>
          <button
            type="button"
            className="btn btn-small"
            disabled={!picked || busy || running}
            onClick={() => void act(() => api.useTemplate(project.id, picked))}
          >
            {busy && <Spinner size={12} />}
            Use
          </button>
        </div>
      )}
      {reason && <p className="field-hint">{reason}</p>}
      {error && (
        <p className="ds-error" role="alert">
          {error}
        </p>
      )}
      <p className="field-hint">
        The template’s files go in the project’s template/ folder. The assistant follows its look and the way it was built,
        not its content. Takes effect from your next message.
      </p>
    </section>
  );
}
