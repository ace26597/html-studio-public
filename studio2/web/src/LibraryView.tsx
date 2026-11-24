// The Library: Skills, Connectors and Templates first, everything else in one closed section at the
// end. The same component is the Library page's body and the "Everything" tab of a project's drawer.

import { type ReactNode, useCallback, useEffect, useId, useMemo, useState } from "react";
import { api, errorMessage } from "./api";
import { Icon, Spinner } from "./icons";
import {
  blockMatches,
  countElsewhere,
  countRows,
  countWords,
  elsewhereCounts,
  filterSections,
  firstKindFound,
  librarySections,
  MAIN_MCP_SOURCES,
  MORE_STORAGE_KEY,
  MORE_TITLE,
  moreMatchCount,
  splitSections,
  splitServers,
  type IndexKind,
  type LibraryRow,
  type LibrarySection,
} from "./library";
import { useDebounced } from "./LibraryControls";
import { McpServersView, serverMatches } from "./McpServersView";
import { NewSkillDialog } from "./NewSkillDialog";
import { SkillOpenDialog } from "./SkillOpenDialog";
import { skillRowOffers } from "./skillReader";
import { loadString, saveString } from "./storage";
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

/**
 * Bring an element to the top now and again shortly after: blocks above it (Models, Keys, …)
 * load their own data and grow after the first scroll.
 */
export function scrollSoon(id: string): number[] {
  return [0, 350, 900, 1600].map((ms) =>
    window.setTimeout(() => document.getElementById(id)?.scrollIntoView({ block: "start" }), ms),
  );
}

/** Rows shown in a long section before "Show all". */
const COLLAPSED_ROWS = 8;

/** A block of "Everything else" given by the page (Models, Keys, Data sources, …). */
export interface MoreBlock {
  /** The block's element id (a link like `/library#data-sources` lands on it). */
  id: string;
  title: string;
  /** Words a search finds the block by, besides its title. */
  words: string;
  intro?: string;
  render: () => ReactNode;
}

/** The list of what this computer has (the page's "Found on this computer"), searched with the Library's search. */
export type IndexBlock = (search: { query: string; preferKind: IndexKind | null }) => ReactNode;

type IndexHits = Record<IndexKind, number> | "failed" | null;

/**
 * While a search is typed: how many skills, connectors and helper agents of this computer that are
 * not shown up front match it (null until known; "failed" when it could not be asked).
 */
function useIndexHits(query: string, enabled: boolean): IndexHits {
  const q = useDebounced(query.trim(), 250);
  const [answer, setAnswer] = useState<{ q: string; hits: IndexHits } | null>(null);
  useEffect(() => {
    if (!enabled || !q) return;
    const controller = new AbortController();
    const kinds: IndexKind[] = ["skills", "connectors", "agents"];
    Promise.all(kinds.map((kind) => api.libraryItems({ kind, q, limit: 1 }, controller.signal))).then(
      (pages) => {
        const hits = { skills: 0, connectors: 0, agents: 0 };
        kinds.forEach((kind, i) => (hits[kind] = countElsewhere(kind, pages[i].counts)));
        setAnswer({ q, hits });
      },
      (cause) => {
        if ((cause as Error)?.name !== "AbortError") setAnswer({ q, hits: "failed" });
      },
    );
    return () => controller.abort();
  }, [q, enabled]);
  return enabled && q && q === query.trim() && answer?.q === q ? answer.hits : null;
}

const DRAWER_MORE_LINE =
  "The studio’s own tools and helper agents, the models it can use, database types, and connectors found on this computer.";

export function LibraryView({
  state,
  compact = false,
  manage = true,
  omit,
  onOpenProject,
  shared = false,
  startNewSkill = false,
  startNewConnector = false,
  yourTemplates,
  moreBlocks,
  indexBlock,
  moreLine = DRAWER_MORE_LINE,
  openMore = false,
}: {
  state: LibraryState;
  compact?: boolean;
  /** Show add / edit / delete for skills and servers (the Library page); off inside a project. */
  manage?: boolean;
  /** Section ids the page shows in a block of their own (the Library page's Models). Keep the array stable. */
  omit?: readonly string[];
  /** Go to a project (a skill described in the Library is made in a project of its own). */
  onOpenProject?: (id: string) => void;
  /** A shared studio: skills can be read, explained and exported, not changed. */
  shared?: boolean;
  /** Open the New skill dialog at "Describe it" once (the New page dialog's "A skill"). */
  startNewSkill?: boolean;
  /** Open Add a connector at "Describe what you need" once (the New page dialog's "A connector"). */
  startNewConnector?: boolean;
  /** The person's own templates under Templates (the Library page), given the search. */
  yourTemplates?: (query: string, heading: string) => ReactNode;
  /** More blocks under "Everything else" (the Library page's Models, Keys, Data sources, …). Keep the array stable. */
  moreBlocks?: readonly MoreBlock[];
  /** "Found on this computer" under "Everything else" (the Library page). */
  indexBlock?: IndexBlock;
  /** One line saying what is under "Everything else". */
  moreLine?: string;
  /** Show "Everything else" open on this visit (a link to something in it), without remembering it. */
  openMore?: boolean;
}) {
  const { library, error, reload } = state;
  const toast = useToast();
  const confirm = useConfirm();
  const [query, setQuery] = useState("");
  const [addingSkill, setAddingSkill] = useState(startNewSkill && manage);
  // "A skill" from the New page dialog lands on "Describe it" once; New skill afterwards starts at the choice.
  const [describeFirst, setDescribeFirst] = useState(startNewSkill);
  /** The id of the skill being read. */
  const [openSkill, setOpenSkill] = useState<string | null>(null);
  const openProject = (id: string) => (onOpenProject ? onOpenProject(id) : window.location.assign(`/p/${id}`));
  // Long sections (a machine can have 100+ skills) show their first rows until expanded.
  const [expanded, setExpanded] = useState<ReadonlySet<string>>(() => new Set());
  const filterId = useId();
  const moreId = useId();
  // "Everything else": closed until opened, and remembered in this browser.
  const [moreOpen, setMoreOpen] = useState(() => loadString(MORE_STORAGE_KEY) === "open");
  const [moreForced, setMoreForced] = useState(openMore);
  /** A search for which the person closed "Everything else" again: it stays closed for that search. */
  const [closedFor, setClosedFor] = useState<string | null>(null);
  const [scrollTo, setScrollTo] = useState<string | null>(null);
  const indexHits = useIndexHits(query, Boolean(indexBlock));

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
  const split = useMemo(() => splitSections(sections), [sections]);
  const shownMain = useMemo(() => filterSections(split.main, query), [split, query]);
  const shownMore = useMemo(() => filterSections(split.more, query), [split, query]);
  const serverSplit = useMemo(() => splitServers(library?.mcp_servers ?? []), [library]);

  // After "Show them" opens "Everything else", bring the list into view once it is drawn.
  useEffect(() => {
    if (!scrollTo) return;
    // Not cleared when `scrollTo` goes back to null just below; the timers are short.
    scrollSoon(scrollTo);
    setScrollTo(null);
  }, [scrollTo]);

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

  const searching = query.trim() !== "";
  const matching = (list: typeof serverSplit.main) => list.filter((server) => serverMatches(server, query)).length;
  const blocks = moreBlocks ?? [];
  const indexFound = indexHits && indexHits !== "failed" ? indexHits : null;
  const moreHits = moreMatchCount({
    sections: split.more,
    servers: searching ? matching(serverSplit.more) : 0,
    blocks,
    query,
    index: indexFound,
  });
  const mainHits = countRows(shownMain) + (searching ? matching(serverSplit.main) : 0);
  const total = countRows(sections) + serverSplit.main.length + serverSplit.more.length;
  const autoShown = searching && moreHits > 0 && closedFor !== query && !moreOpen && !moreForced;
  const showMore = moreOpen || moreForced || autoShown;
  const elsewhere = elsewhereCounts(library.index);
  const showIndex = Boolean(indexBlock) && (!searching || indexHits === "failed" || (indexFound !== null && Object.values(indexFound).some((n) => n > 0)));

  const toggleMore = () => {
    const next = !showMore;
    setMoreOpen(next);
    setMoreForced(false);
    setClosedFor(next || !searching ? null : query);
    saveString(MORE_STORAGE_KEY, next ? "open" : null);
  };
  const openMoreAt = (id: string) => {
    setMoreOpen(true);
    saveString(MORE_STORAGE_KEY, "open");
    setScrollTo(id);
  };

  const sectionById = (id: string) => sections.find((section) => section.id === id);
  const renderSection = (section: LibrarySection) => {
    // While searching, sections with nothing matching are left out.
    if (searching && section.rows.length === 0) return null;
    const headingId = `lib-${section.id}`;
    const all = sectionById(section.id)?.rows.length ?? 0;
    return (
      <section key={section.id} className="lib-section" aria-labelledby={headingId}>
        <div className="lib-section-head">
          <h3 id={headingId} className="lib-title">
            {section.title}
            {all > 0 && <span className="lib-count">{all}</span>}
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
            {(searching || expanded.has(section.id) ? section.rows : section.rows.slice(0, COLLAPSED_ROWS)).map((row) => (
              <li key={row.key} className="lib-row">
                <div className="lib-row-main">
                  <span className="lib-row-title">{row.title}</span>
                  {row.detail && <span className="lib-row-detail">{row.detail}</span>}
                  {row.note && <span className="lib-row-note">{row.note}</span>}
                </div>
                {row.status && <span className={`lib-status is-${row.status.tone}`}>{row.status.text}</span>}
                {manage && (row.exportId || row.action) && <RowActions row={row} shared={shared} onOpen={setOpenSkill} onDelete={(name) => void deleteSkill(name)} />}
              </li>
            ))}
          </ul>
        )}
        {!searching && section.rows.length > COLLAPSED_ROWS && (
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
    );
  };

  const skillSections = shownMain.filter((section) => section.id.startsWith("skills-"));
  const templateSection = shownMain.find((section) => section.id === "templates");
  const skillCount = countRows(split.main.filter((section) => section.id.startsWith("skills-")));
  const showSkills = !searching || countRows(skillSections) > 0;
  const templateHits = (templateSection?.rows.length ?? 0) > 0;
  const showTemplates = !searching || Boolean(yourTemplates) || templateHits;
  const shownBlocks = searching ? blocks.filter((block) => blockMatches(block, query)) : blocks;
  const moreNothing = searching && moreHits === 0 && indexHits !== "failed";

  return (
    <div className={`lib${compact ? " is-compact" : ""}`}>
      <div className="lib-filter lib-search">
        <Icon name="search" size={14} />
        <label htmlFor={filterId} className="sr-only">
          Search the library
        </label>
        <input
          id={filterId}
          type="search"
          value={query}
          placeholder={compact ? "Search the library" : "Search skills, connectors, templates and everything else"}
          onChange={(event) => setQuery(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Escape" && query) {
              event.stopPropagation();
              setQuery("");
            }
          }}
        />
        {searching && (
          <span className="lib-filter-count" role="status">
            {mainHits + moreHits === 0
              ? indexHits === null && indexBlock
                ? "Searching…"
                : "No matches"
              : `${mainHits + moreHits} found${moreHits > 0 ? ` · ${moreHits} in ${MORE_TITLE.toLowerCase()}` : ""}`}
          </span>
        )}
      </div>
      {!searching && total === 0 && <p className="lib-empty">The library is empty.</p>}

      {showSkills && (
        <section className="lib-group" aria-labelledby={`${moreId}-skills`}>
          <h2 id={`${moreId}-skills`} className="lib-group-title">
            Skills {skillCount > 0 && <span className="lib-count">{skillCount}</span>}
          </h2>
          <p className="lib-intro">Instructions the agent follows for one kind of job.</p>
          {skillSections.map(renderSection)}
          {!searching && indexBlock && elsewhere.skills > 0 && (
            <p className="lib-elsewhere">
              Another {countWords(elsewhere.skills, "skills")} found on this computer {elsewhere.skills === 1 ? "is" : "are"} under{" "}
              {MORE_TITLE}.{" "}
              <button type="button" className="lib-link" onClick={() => openMoreAt("everything")}>
                Show them
              </button>
            </p>
          )}
        </section>
      )}

      {(!searching || matching(serverSplit.main) > 0) && (
      <div className="lib-group">
        <McpServersView
          servers={serverSplit.main}
          query={query}
          onChanged={reload}
          manage={manage}
          startAdding={startNewConnector}
          sources={MAIN_MCP_SOURCES}
          level={2}
          headingId={`${moreId}-connectors`}
        />
        {!searching && serverSplit.more.length > 0 && (
          <p className="lib-elsewhere">
            Another {countWords(serverSplit.more.length, "connectors")} found on this computer{" "}
            {serverSplit.more.length === 1 ? "is" : "are"} under {MORE_TITLE}.{" "}
            <button type="button" className="lib-link" onClick={() => openMoreAt(`${moreId}-found`)}>
              Show them
            </button>
          </p>
        )}
      </div>
      )}

      {showTemplates && (
        <section id="templates" className="lib-group" aria-labelledby={`${moreId}-templates`}>
          {(!searching || templateHits) && (
            <>
              <h2 id={`${moreId}-templates`} className="lib-group-title">
                Templates
              </h2>
              <p className="lib-intro">Pages and apps to start from.</p>
            </>
          )}
          {templateSection && renderSection(templateSection)}
          {yourTemplates?.(query, searching ? "Your own templates" : "Your own")}
        </section>
      )}

      <section className="lib-group lib-rest" aria-labelledby={`${moreId}-rest`}>
        <h2 id={`${moreId}-rest`} className="lib-group-title lib-rest-title">
          <button
            type="button"
            className="lib-rest-toggle"
            aria-expanded={showMore}
            aria-controls={`${moreId}-rest-body`}
            onClick={toggleMore}
          >
            <Icon name={showMore ? "chevron-down" : "chevron-right"} size={16} />
            {MORE_TITLE}
          </button>
        </h2>
        <p className="lib-intro lib-rest-line">{moreLine}</p>
        {autoShown && (
          <p className="lib-rest-note" role="status">
            Opened while you search: {moreHits} {moreHits === 1 ? "match" : "matches"} here.
          </p>
        )}
        {showMore && (
          <div id={`${moreId}-rest-body`} className="lib-rest-body">
            {moreNothing && <p className="lib-empty">Nothing here matches “{query.trim()}”.</p>}
            <div id={`${moreId}-found`}>
              <McpServersView
                servers={serverSplit.more}
                query={query}
                onChanged={reload}
                manage={manage}
                exclude={MAIN_MCP_SOURCES}
                title="Connectors found on this computer"
                secondary={null}
                intro={null}
                headingId={`${moreId}-found-title`}
              />
            </div>
            {shownMore.map(renderSection)}
            {shownBlocks.map((block) => (
              <section key={block.id} id={block.id} className="lib-section lib-rest-block" aria-labelledby={`${block.id}-title`}>
                <h3 id={`${block.id}-title`} className="lib-title">
                  {block.title}
                </h3>
                {block.intro && <p className="lib-intro">{block.intro}</p>}
                {block.render()}
              </section>
            ))}
            {indexBlock && showIndex && (
              <section id="everything" className="lib-section lib-rest-block" aria-labelledby="everything-title">
                <h3 id="everything-title" className="lib-title">
                  Found on this computer
                </h3>
                <p className="lib-intro lix-lede">
                  Every skill, connector and helper agent on this computer. Before each request the studio picks what fits
                  from these. Say <strong>Always</strong> or <strong>Never</strong> to decide for every project; a project
                  can still decide for itself.
                </p>
                {indexBlock({ query, preferKind: indexFound ? firstKindFound(indexFound) : null })}
              </section>
            )}
          </div>
        )}
      </section>

      {addingSkill && (
        <NewSkillDialog
          initialWay={describeFirst ? "describe" : null}
          onClose={() => {
            setAddingSkill(false);
            setDescribeFirst(false);
          }}
          onAdded={(name) => {
            setAddingSkill(false);
            toast({ message: `Added the skill “${name}”`, kind: "success" });
            reload();
          }}
          onOpenProject={openProject}
        />
      )}
      {openSkill && (
        <SkillOpenDialog
          id={openSkill}
          shared={shared}
          onClose={() => setOpenSkill(null)}
          onChanged={reload}
          onOpenProject={openProject}
        />
      )}
    </div>
  );
}

/** Open, Export and (for one's own) Delete on a skill's row. */
export function RowActions({
  row,
  shared,
  onOpen,
  onDelete,
}: {
  row: LibraryRow;
  shared: boolean;
  onOpen: (id: string) => void;
  onDelete: (name: string) => void;
}) {
  const offers = skillRowOffers(row, shared);
  if (!offers.open && !offers.export && !offers.delete) return null;
  return (
    <div className="lib-row-actions">
      {offers.open && row.exportId && (
        <button
          type="button"
          className="btn btn-small"
          aria-label={`Open the skill ${row.title}`}
          onClick={() => onOpen(row.exportId as string)}
        >
          Open
        </button>
      )}
      {offers.export && row.exportId && (
        <a className="btn btn-small" href={api.skillExportUrl(row.exportId)} download aria-label={`Export the skill ${row.title}`}>
          <Icon name="download" size={13} /> Export
        </a>
      )}
      {offers.delete && row.action && (
        <button
          type="button"
          className="icon-btn"
          aria-label={`Delete the skill ${row.action.name}`}
          data-tip="Delete"
          data-tip-align="end"
          onClick={() => onDelete((row.action as { name: string }).name)}
        >
          <Icon name="trash" size={15} />
        </button>
      )}
    </div>
  );
}
