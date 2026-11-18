// "What this studio can do": the sections of `GET /api/library`, as a page or inside a panel.

import { Fragment, useCallback, useEffect, useId, useMemo, useState } from "react";
import { api, errorMessage } from "./api";
import { Icon, Spinner } from "./icons";
import { countRows, filterSections, FILTER_THRESHOLD, librarySections, type LibrarySection } from "./library";
import { McpServersView, serverMatches } from "./McpServersView";
import { NewSkillDialog } from "./NewSkillDialog";
import { SkillOpenDialog } from "./SkillOpenDialog";
import type { Library } from "./types";
import { useConfirm, useToast } from "./ui";

export interface LibraryState {
  library: Library | null;
  error: string | null;
  reload: () => void;
}

export function useLibrary(): LibraryState {
  const [library, setLibrary] = useState<Library | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    setError(null);
    api.getLibrary(controller.signal).then(setLibrary, (cause) => {
      if ((cause as Error)?.name !== "AbortError") setError(errorMessage(cause));
    });
    return () => controller.abort();
  }, [attempt]);
  const reload = useCallback(() => setAttempt((n) => n + 1), []);
  return { library, error, reload };
}

function observedLabel(iso: string | null): string | null {
  if (!iso) return null;
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return null;
  return date.toLocaleString(undefined, { day: "numeric", month: "short", hour: "numeric", minute: "2-digit" });
}

/** Rows shown in a long section before "Show all". */
const COLLAPSED_ROWS = 8;

/** Where the MCP servers block goes: after the sub-agents, or after the last skills section. */
function mcpAfter(sections: LibrarySection[]): LibrarySection["id"] | null {
  if (sections.some((section) => section.id === "agents")) return "agents";
  const skills = sections.filter((section) => section.id.startsWith("skills-"));
  return skills.length ? skills[skills.length - 1].id : null;
}

export function LibraryView({
  state,
  compact = false,
  manage = true,
  omit,
  onOpenProject,
}: {
  state: LibraryState;
  compact?: boolean;
  /** Show add / edit / delete for skills and servers (the Library page); off inside a project. */
  manage?: boolean;
  /** Section ids the page shows in a block of their own (the Library page's Models). Keep the array stable. */
  omit?: readonly string[];
  /** Go to a project (a skill described in the Library is made in a project of its own). */
  onOpenProject?: (id: string) => void;
}) {
  const { library, error, reload } = state;
  const toast = useToast();
  const confirm = useConfirm();
  const [query, setQuery] = useState("");
  const [addingSkill, setAddingSkill] = useState(false);
  const [openSkill, setOpenSkill] = useState<string | null>(null);
  // Long sections (a machine can have 100+ skills) show their first rows until expanded.
  const [expanded, setExpanded] = useState<ReadonlySet<string>>(() => new Set());
  const filterId = useId();

  const deleteSkill = async (name: string) => {
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
      reload();
    } catch (cause) {
      toast({ message: errorMessage(cause), kind: "error" });
    }
  };

  const sections = useMemo(
    () =>
      library
        ? librarySections(library, observedLabel(library.observed_at)).filter((section) => !omit?.includes(section.id))
        : [],
    [library, omit],
  );
  const shown = useMemo(() => filterSections(sections, query), [sections, query]);
  const servers = library?.mcp_servers ?? [];
  const total = countRows(sections) + servers.length;
  const matches = countRows(shown) + (query ? servers.filter((server) => serverMatches(server, query)).length : servers.length);
  const mcpSlot = mcpAfter(sections);
  const mcpBlock = (
    <McpServersView key="mcp" servers={servers} query={query} onChanged={reload} manage={manage} />
  );

  if (error && !library) {
    return (
      <div className="lib-error" role="alert">
        <Icon name="alert" size={15} />
        <span>The library couldn’t be loaded: {error}</span>
        <button type="button" className="btn btn-small" onClick={reload}>
          Try again
        </button>
      </div>
    );
  }
  if (!library) {
    return (
      <div className="panel-empty">
        <Spinner />
      </div>
    );
  }

  return (
    <div className={`lib${compact ? " is-compact" : ""}`}>
      {total > FILTER_THRESHOLD && (
        <div className="lib-filter">
          <Icon name="search" size={14} />
          <label htmlFor={filterId} className="sr-only">
            Filter the library
          </label>
          <input
            id={filterId}
            type="search"
            value={query}
            placeholder={`Filter ${total} items`}
            onChange={(event) => setQuery(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Escape" && query) {
                event.stopPropagation();
                setQuery("");
              }
            }}
          />
          {query && (
            <span className="lib-filter-count" role="status">
              {matches === 0 ? "No matches" : `${matches} of ${total}`}
            </span>
          )}
        </div>
      )}
      {mcpSlot === null && mcpBlock}
      {shown.map((section, index) => {
        const after = section.id === mcpSlot ? mcpBlock : null;
        // While filtering, sections with nothing matching are left out.
        if (query && section.rows.length === 0) return <Fragment key={section.id}>{after}</Fragment>;
        const headingId = `lib-${section.id}`;
        return (
          <Fragment key={section.id}>
          <section className="lib-section" aria-labelledby={headingId}>
            <div className="lib-section-head">
              <h3 id={headingId} className="lib-title">
                {section.title}
                {sections[index].rows.length > 0 && <span className="lib-count">{sections[index].rows.length}</span>}
              </h3>
              {manage && section.id === "skills-yours" && (
                <button type="button" className="btn btn-small" onClick={() => setAddingSkill(true)}>
                  <Icon name="plus" size={13} strokeWidth={2.2} /> New skill
                </button>
              )}
            </div>
            {section.intro && <p className="lib-intro">{section.intro}</p>}
            {section.rows.length === 0 ? (
              <p className="lib-empty">{section.empty}</p>
            ) : (
              <ul className="lib-rows" id={`${headingId}-rows`}>
                {(query || expanded.has(section.id) ? section.rows : section.rows.slice(0, COLLAPSED_ROWS)).map((row) => (
                  <li key={row.key} className="lib-row">
                    <div className="lib-row-main">
                      <span className="lib-row-title">{row.title}</span>
                      {row.detail && <span className="lib-row-detail">{row.detail}</span>}
                      {row.note && <span className="lib-row-note">{row.note}</span>}
                    </div>
                    {row.status && <span className={`lib-status is-${row.status.tone}`}>{row.status.text}</span>}
                    {manage && (row.exportId || row.action) && (
                      <div className="lib-row-actions">
                        {row.action && (
                          <button
                            type="button"
                            className="btn btn-small"
                            aria-label={`Open the skill ${row.action.name}`}
                            onClick={() => setOpenSkill((row.action as { name: string }).name)}
                          >
                            Open
                          </button>
                        )}
                        {row.exportId && (
                          <a
                            className="btn btn-small"
                            href={api.skillExportUrl(row.exportId)}
                            download
                            aria-label={`Export the skill ${row.title}`}
                          >
                            <Icon name="download" size={13} /> Export
                          </a>
                        )}
                        {row.action && (
                          <button
                            type="button"
                            className="icon-btn"
                            aria-label={`Delete the skill ${row.action.name}`}
                            data-tip="Delete"
                            data-tip-align="end"
                            onClick={() => void deleteSkill((row.action as { name: string }).name)}
                          >
                            <Icon name="trash" size={15} />
                          </button>
                        )}
                      </div>
                    )}
                  </li>
                ))}
              </ul>
            )}
            {!query && section.rows.length > COLLAPSED_ROWS && (
              <button
                type="button"
                className="lib-more"
                aria-expanded={expanded.has(section.id)}
                aria-controls={`${headingId}-rows`}
                onClick={() =>
                  setExpanded((set) => {
                    const next = new Set(set);
                    if (next.has(section.id)) next.delete(section.id);
                    else next.add(section.id);
                    return next;
                  })
                }
              >
                {expanded.has(section.id) ? "Show fewer" : `Show all ${section.rows.length}`}
              </button>
            )}
          </section>
          {after}
          </Fragment>
        );
      })}
      {addingSkill && (
        <NewSkillDialog
          onClose={() => setAddingSkill(false)}
          onAdded={(name) => {
            setAddingSkill(false);
            toast({ message: `Added the skill “${name}”`, kind: "success" });
            reload();
          }}
          onOpenProject={(id) => (onOpenProject ? onOpenProject(id) : window.location.assign(`/p/${id}`))}
        />
      )}
      {openSkill && <SkillOpenDialog name={openSkill} onClose={() => setOpenSkill(null)} onChanged={reload} />}
    </div>
  );
}
