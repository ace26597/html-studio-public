// Inside a project: its context from this computer, which data sources it may use, and what the
// studio can do.

import { useCallback, useEffect, useMemo, useState } from "react";
import { api, errorMessage } from "./api";
import { DataSourceDialog, kindLabel, kindsOrKnown, sourceAddress } from "./DataSources";
import { Icon, Spinner } from "./icons";
import { LibraryView, useLibrary } from "./LibraryView";
import { ProjectCapabilities } from "./ProjectCapabilities";
import { ProjectTemplate } from "./ProjectTemplate";
import {
  CONTEXT_LIMIT,
  contextIdsKey,
  contextTabLabel,
  contextWhere,
  dropContextItem,
  foundSummary,
  kindIcon,
  kindName,
} from "./context";
import type { ContextItem, ContextState, Project, ProjectDataSource } from "./types";
import { IconButton, useConfirm, useToast } from "./ui";

interface ProjectLibraryPanelProps {
  projectId: string;
  /** Called with the attached ids after every successful attach/detach (and on load). */
  onAttachedChange: (ids: string[]) => void;
  onManage: () => void;
  onClose: () => void;
  project?: Project | null;
  running?: boolean;
  onProjectChange?: (project: Project) => void;
  /** Ask for a tab: when `n` changes the panel shows `tab` (a context chip asks for "context"). */
  tabRequest?: { tab: LibraryTab; n: number } | null;
  /** "Add context": the owner opens the Add dialog. */
  onAddContext?: () => void;
  /** Edit on a context item: the owner opens the edit dialog. */
  onEditContext?: (item: ContextItem) => void;
  /** The project's context items after a removal, or as the server listed them. */
  onContextChange?: (items: ContextItem[]) => void;
}

export type LibraryTab = "context" | "data" | "capabilities" | "library";
const TABS: LibraryTab[] = ["context", "data", "capabilities", "library"];
type Tab = LibraryTab;

export function ProjectLibraryPanel({
  projectId,
  onAttachedChange,
  onManage,
  onClose,
  project = null,
  running = false,
  onProjectChange,
  tabRequest = null,
  onAddContext,
  onEditContext,
  onContextChange,
}: ProjectLibraryPanelProps) {
  const toast = useToast();
  const confirm = useConfirm();
  const library = useLibrary();
  const kinds = kindsOrKnown(library.library?.data_kinds);
  // Today's default stays "data"; a chip or the composer asks for "context" through `tabRequest`.
  const [tab, setTab] = useState<Tab>(tabRequest?.tab ?? "data");
  useEffect(() => {
    if (tabRequest) setTab(tabRequest.tab);
  }, [tabRequest]);
  const [sources, setSources] = useState<ProjectDataSource[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);
  const [busy, setBusy] = useState<Record<string, boolean>>({});
  const [rowErrors, setRowErrors] = useState<Record<string, string>>({});
  const [adding, setAdding] = useState(false);

  const attachedChange = useCallback(onAttachedChange, [onAttachedChange]);

  // ---- context: the items come from the project; their states from GET …/context ----------
  const [contextList, setContextList] = useState<(ContextItem & { state?: ContextState })[] | null>(null);
  const [contextError, setContextError] = useState<string | null>(null);
  const [contextAttempt, setContextAttempt] = useState(0);
  const [contextBusy, setContextBusy] = useState<Record<string, boolean>>({});
  const [contextRowErrors, setContextRowErrors] = useState<Record<string, string>>({});
  const idsKey = contextIdsKey(project?.context);
  const contextChange = useCallback((items: ContextItem[]) => onContextChange?.(items), [onContextChange]);

  // Read when the Context tab is shown (an address is asked whether it answers, up to 2 seconds).
  const onContextTab = tab === "context";
  useEffect(() => {
    if (!onContextTab) return;
    const controller = new AbortController();
    setContextError(null);
    api.listProjectContext(projectId, controller.signal).then(
      (list) => {
        setContextList(list);
        contextChange(list.map(({ state: _state, ...item }) => item));
      },
      (cause) => {
        if ((cause as Error)?.name !== "AbortError") setContextError(errorMessage(cause));
      },
    );
    return () => controller.abort();
    // Read again when items are added or removed; a rename needs no new states.
  }, [projectId, idsKey, contextAttempt, contextChange, onContextTab]);

  /** The project's items (current after an edit anywhere), each with the state last read. */
  const contextItems = useMemo(() => {
    const states = new Map((contextList ?? []).map((item) => [item.id, item.state]));
    const items: ContextItem[] | null = project?.context ?? contextList;
    return items ? items.map((item) => ({ ...item, state: states.get(item.id) })) : null;
  }, [project?.context, contextList]);
  const contextCount = contextItems?.length ?? 0;

  const removeContext = async (item: ContextItem) => {
    const ok = await confirm({
      title: `Remove “${item.label}”?`,
      body: "The agent is no longer told about it. Nothing at that path or address is touched.",
      confirmLabel: "Remove",
      danger: true,
    });
    if (!ok) return;
    setContextBusy((map) => ({ ...map, [item.id]: true }));
    setContextRowErrors((map) => {
      const next = { ...map };
      delete next[item.id];
      return next;
    });
    try {
      await api.removeProjectContext(projectId, item.id);
      setContextList((list) => (list ? list.filter((entry) => entry.id !== item.id) : list));
      contextChange(dropContextItem(contextItems ?? [], item.id));
    } catch (cause) {
      setContextRowErrors((map) => ({ ...map, [item.id]: errorMessage(cause) }));
    } finally {
      setContextBusy((map) => ({ ...map, [item.id]: false }));
    }
  };

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
      aria-label="Context, library and data sources"
      onKeyDown={(event) => {
        if (event.key === "Escape" && !event.defaultPrevented) {
          event.stopPropagation();
          onClose();
        }
      }}
    >
      <header className="panel-header">
        <div className="plib-tabs" role="tablist" aria-label="Library">
          {tabButton("context", contextTabLabel(contextCount), contextTabLabel(contextCount))}
          {tabButton("data", attachedCount ? `Data sources · ${attachedCount}` : "Data sources", attachedCount ? `Data · ${attachedCount}` : "Data")}
          {tabButton("capabilities", "Skills & connectors", "Skills")}
          {tabButton("library", "Everything", "All")}
        </div>
        <span className="panel-spacer" />
        <IconButton icon="x" label="Close the library" tip="Close (Esc)" tipAlign="end" onClick={onClose} />
      </header>

      <div className="panel-body plib-body" role="tabpanel" tabIndex={0} id={`plib-panel-${tab}`} aria-labelledby={`plib-tab-${tab}`}>
        {tab === "context" ? (
          <div className="plib-data">
            <p className="ds-lede">
              Folders, files and addresses on this computer that this project’s questions may be about. The agent reads
              them where they are and changes nothing there.
            </p>
            {contextError && (
              <div className="lib-error" role="alert">
                <Icon name="alert" size={15} />
                <span>The context couldn’t be loaded: {contextError}</span>
                <button type="button" className="btn btn-small" onClick={() => setContextAttempt((n) => n + 1)}>
                  Try again
                </button>
              </div>
            )}
            {!contextItems && !contextError && (
              <div className="panel-empty">
                <Spinner />
              </div>
            )}
            {contextItems && contextItems.length === 0 && (
              <p className="lib-empty">
                No context yet. Add a folder of code, a file, or the address of a running app for the agent to read.
              </p>
            )}
            {contextItems && contextItems.length > 0 && (
              <ul className="ds-list ctx-list">
                {contextItems.map((item) => {
                  const where = contextWhere(item);
                  const summary = foundSummary(item.kind, item.found);
                  const problem = item.state && !item.state.ok ? item.state.problem : null;
                  return (
                    <li key={item.id} className={`ds-item ctx-item${problem ? " is-problem" : ""}`}>
                      <div className="ds-item-main">
                        <span className="ds-item-name">
                          <Icon name={kindIcon(item.kind)} size={14} />
                          <span className="ctx-item-label">{item.label}</span>
                          <span className="sr-only">({kindName(item.kind)})</span>
                        </span>
                        <code className="ctx-item-where" title={where}>
                          {where}
                        </code>
                        {item.note && <p className="ctx-item-note">{item.note}</p>}
                        {summary && <span className="ds-item-meta">{summary}</span>}
                        {problem && (
                          <span className="ds-last is-fail">
                            <Icon name="alert" size={12} /> {problem}
                          </span>
                        )}
                        {contextRowErrors[item.id] && (
                          <p className="ds-error" role="alert">
                            {contextRowErrors[item.id]}
                          </p>
                        )}
                      </div>
                      <div className="ds-item-actions">
                        {onEditContext && (
                          <button
                            type="button"
                            className="btn btn-small"
                            aria-label={`Edit ${item.label}`}
                            disabled={contextBusy[item.id]}
                            onClick={() => onEditContext(item)}
                          >
                            Edit
                          </button>
                        )}
                        <button
                          type="button"
                          className="btn btn-small"
                          aria-label={`Remove ${item.label}`}
                          disabled={contextBusy[item.id]}
                          onClick={() => void removeContext(item)}
                        >
                          {contextBusy[item.id] ? <Spinner size={12} /> : null}
                          Remove
                        </button>
                      </div>
                    </li>
                  );
                })}
              </ul>
            )}
            {onAddContext && (
              <div className="plib-actions">
                <button
                  type="button"
                  className="btn btn-small"
                  disabled={contextCount >= CONTEXT_LIMIT}
                  title={contextCount >= CONTEXT_LIMIT ? `A project can hold at most ${CONTEXT_LIMIT} items.` : undefined}
                  onClick={onAddContext}
                >
                  <Icon name="plus" size={14} strokeWidth={2.2} /> Add context
                </button>
              </div>
            )}
          </div>
        ) : tab === "data" ? (
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
