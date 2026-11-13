// Inside a project: which data sources this project may use, and what the studio can do.

import { useCallback, useEffect, useState } from "react";
import { api, errorMessage } from "./api";
import { DataSourceDialog, kindLabel, kindsOrKnown, sourceAddress } from "./DataSources";
import { Icon, Spinner } from "./icons";
import { LibraryView, useLibrary } from "./LibraryView";
import { ProjectCapabilities } from "./ProjectCapabilities";
import { ProjectTemplate } from "./ProjectTemplate";
import type { Project, ProjectDataSource } from "./types";
import { IconButton, useToast } from "./ui";

interface ProjectLibraryPanelProps {
  projectId: string;
  /** Called with the attached ids after every successful attach/detach (and on load). */
  onAttachedChange: (ids: string[]) => void;
  onManage: () => void;
  onClose: () => void;
  project?: Project | null;
  running?: boolean;
  onProjectChange?: (project: Project) => void;
}

type Tab = "data" | "capabilities" | "library";
const TABS: Tab[] = ["data", "capabilities", "library"];

export function ProjectLibraryPanel({
  projectId,
  onAttachedChange,
  onManage,
  onClose,
  project = null,
  running = false,
  onProjectChange,
}: ProjectLibraryPanelProps) {
  const toast = useToast();
  const library = useLibrary();
  const kinds = kindsOrKnown(library.library?.data_kinds);
  const [tab, setTab] = useState<Tab>("data");
  const [sources, setSources] = useState<ProjectDataSource[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);
  const [busy, setBusy] = useState<Record<string, boolean>>({});
  const [rowErrors, setRowErrors] = useState<Record<string, string>>({});
  const [adding, setAdding] = useState(false);

  const attachedChange = useCallback(onAttachedChange, [onAttachedChange]);

  useEffect(() => {
    const controller = new AbortController();
    setError(null);
    api.listProjectDataSources(projectId, controller.signal).then(
      (list) => {
        setSources(list);
        attachedChange(list.filter((item) => item.attached).map((item) => item.id));
      },
      (cause) => {
        if ((cause as Error)?.name !== "AbortError") setError(errorMessage(cause));
      },
    );
    return () => controller.abort();
  }, [projectId, attempt, attachedChange]);

  const toggle = async (source: ProjectDataSource) => {
    const attach = !source.attached;
    setBusy((map) => ({ ...map, [source.id]: true }));
    setRowErrors((map) => {
      const next = { ...map };
      delete next[source.id];
      return next;
    });
    try {
      if (attach) await api.attachDataSource(projectId, source.id);
      else await api.detachDataSource(projectId, source.id);
      setSources((list) => {
        const next = list?.map((item) => (item.id === source.id ? { ...item, attached: attach } : item)) ?? list;
        if (next) attachedChange(next.filter((item) => item.attached).map((item) => item.id));
        return next;
      });
    } catch (cause) {
      setRowErrors((map) => ({ ...map, [source.id]: errorMessage(cause) }));
    } finally {
      setBusy((map) => ({ ...map, [source.id]: false }));
    }
  };

  const attachedCount = sources?.filter((item) => item.attached).length ?? 0;

  const tabButton = (id: Tab, label: string, short: string) => (
    <button
      type="button"
      role="tab"
      id={`plib-tab-${id}`}
      aria-label={label}
      aria-selected={tab === id}
      aria-controls={`plib-panel-${id}`}
      tabIndex={tab === id ? 0 : -1}
      className={`plib-tab${tab === id ? " is-on" : ""}`}
      onClick={() => setTab(id)}
      onKeyDown={(event) => {
        if (event.key === "ArrowRight" || event.key === "ArrowLeft") {
          event.preventDefault();
          const index = TABS.indexOf(tab);
          const next = TABS[(index + (event.key === "ArrowRight" ? 1 : TABS.length - 1)) % TABS.length];
          setTab(next);
          document.getElementById(`plib-tab-${next}`)?.focus();
        }
      }}
    >
      <span className="tab-long">{label}</span>
      <span className="tab-short" aria-hidden="true">
        {short}
      </span>
    </button>
  );

  return (
    <aside
      className="panel panel-right logs-panel plib"
      aria-label="Library and data sources"
      onKeyDown={(event) => {
        if (event.key === "Escape" && !event.defaultPrevented) {
          event.stopPropagation();
          onClose();
        }
      }}
    >
      <header className="panel-header">
        <div className="plib-tabs" role="tablist" aria-label="Library">
          {tabButton("data", attachedCount ? `Data sources · ${attachedCount}` : "Data sources", attachedCount ? `Data · ${attachedCount}` : "Data")}
          {tabButton("capabilities", "Skills & connectors", "Skills")}
          {tabButton("library", "Everything", "All")}
        </div>
        <span className="panel-spacer" />
        <IconButton icon="x" label="Close the library" tip="Close (Esc)" tipAlign="end" onClick={onClose} />
      </header>

      <div className="panel-body plib-body" role="tabpanel" tabIndex={0} id={`plib-panel-${tab}`} aria-labelledby={`plib-tab-${tab}`}>
        {tab === "data" ? (
          <div className="plib-data">
            <p className="ds-lede">
              Attached sources can be queried read-only by the assistant and by this project’s app. Passwords stay in your
              Mac’s Keychain; the assistant never sees them.
            </p>
            {error && (
              <div className="lib-error" role="alert">
                <Icon name="alert" size={15} />
                <span>Data sources couldn’t be loaded: {error}</span>
                <button type="button" className="btn btn-small" onClick={() => setAttempt((n) => n + 1)}>
                  Try again
                </button>
              </div>
            )}
            {!sources && !error && (
              <div className="panel-empty">
                <Spinner />
              </div>
            )}
            {sources && sources.length === 0 && (
              <p className="lib-empty">No data sources yet. Add one to let this project read from your database.</p>
            )}
            {sources && sources.length > 0 && (
              <ul className="ds-list">
                {sources.map((source) => (
                  <li key={source.id} className={`ds-item${source.attached ? " is-attached" : ""}`}>
                    <div className="ds-item-main">
                      <span className="ds-item-name">
                        <Icon name="database" size={14} /> {source.name}
                      </span>
                      <span className="ds-item-meta">
                        {kindLabel(source.kind, kinds)} · <code>{sourceAddress(source)}</code>
                      </span>
                      {source.last_test && !source.last_test.ok && (
                        <span className="ds-last is-fail">Last test failed: {source.last_test.message}</span>
                      )}
                      {rowErrors[source.id] && (
                        <p className="ds-error" role="alert">
                          {rowErrors[source.id]}
                        </p>
                      )}
                    </div>
                    <button
                      type="button"
                      role="switch"
                      aria-checked={source.attached}
                      aria-label={`Attach ${source.name} to this project`}
                      className={`switch${source.attached ? " is-on" : ""}`}
                      disabled={busy[source.id]}
                      onClick={() => void toggle(source)}
                    >
                      <span className="switch-thumb" aria-hidden="true" />
                    </button>
                  </li>
                ))}
              </ul>
            )}
            <div className="plib-actions">
              <button type="button" className="btn btn-small" onClick={() => setAdding(true)}>
                <Icon name="plus" size={14} strokeWidth={2.2} /> Add a data source
              </button>
              <button type="button" className="btn btn-small" onClick={onManage}>
                Manage data sources
              </button>
            </div>
          </div>
        ) : tab === "capabilities" ? (
          <>
            {project && onProjectChange && (
              <ProjectTemplate project={project} running={running} onProjectChange={onProjectChange} />
            )}
            <ProjectCapabilities projectId={projectId} />
          </>
        ) : (
          <LibraryView state={library} compact manage={false} />
        )}
      </div>

      {adding && (
        <DataSourceDialog
          kinds={kinds}
          source={null}
          onClose={() => setAdding(false)}
          onSaved={(saved) => {
            setAdding(false);
            toast({ message: `Added “${saved.name}”. Switch it on to attach it to this project.`, kind: "success" });
            setAttempt((n) => n + 1);
          }}
        />
      )}
    </aside>
  );
}
