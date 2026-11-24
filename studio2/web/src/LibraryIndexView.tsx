// Step 3o: "Found on this computer" (under "Everything else") on the Library page. A paged view of
// `GET /api/library/items`: never more rows in the page than were fetched.

import { useCallback, useEffect, useId, useRef, useState } from "react";
import { api, errorMessage } from "./api";
import { Icon, Spinner } from "./icons";
import { ConnectorStateNote, DecisionControl, useDebounced } from "./LibraryControls";
import {
  fmt,
  fromPref,
  indexSummary,
  mergePage,
  PAGE_SIZE,
  remaining,
  saveDecision,
  skillFacts,
  withDecision,
  type Decision,
} from "./libraryPick";
import { SkillOpenDialog } from "./SkillOpenDialog";
import type { LibraryIndex, LibraryItem, LibraryItemKind } from "./types";

const KINDS: { kind: LibraryItemKind; label: string }[] = [
  { kind: "skills", label: "Skills" },
  { kind: "connectors", label: "Connectors" },
  { kind: "agents", label: "Helper agents" },
];

const SOURCES: Record<LibraryItemKind, { value: string; label: string }[]> = {
  skills: [
    { value: "machine", label: "Yours on this computer" },
    { value: "plugin", label: "Plugins" },
    { value: "studio", label: "Studio" },
    { value: "yours", label: "Added in the studio" },
  ],
  connectors: [
    { value: "yours", label: "Added in the studio" },
    { value: "machine", label: "On this computer" },
    { value: "plugin", label: "Plugins" },
    { value: "suggested", label: "Suggested" },
  ],
  agents: [
    { value: "machine", label: "Yours on this computer" },
    { value: "plugin", label: "Plugins" },
    { value: "studio", label: "Studio" },
  ],
};

function statesFor(kind: LibraryItemKind): { value: string; label: string }[] {
  if (kind === "agents") return [];
  const out = [
    { value: "", label: "All choices" },
    { value: "always", label: "Always" },
    { value: "never", label: "Never" },
  ];
  if (kind === "skills") out.push({ value: "benched", label: "No longer picked" });
  else out.push({ value: "signin", label: "Needs signing in" });
  return out;
}

const NOUN: Record<LibraryItemKind, string> = { skills: "skills", connectors: "connectors", agents: "helper agents" };

export function LibraryIndexView({
  initialIndex,
  shared = false,
  onOpenProject,
  onChanged,
  search,
  preferKind = null,
}: {
  initialIndex?: LibraryIndex | null;
  /** The Library's own search: when it changes, this list searches for it too. */
  search?: string;
  /** While searching, list this kind first (the first kind the search found something for). */
  preferKind?: LibraryItemKind | null;
  /** A shared studio: a skill opened here can be read, explained and exported, not changed. */
  shared?: boolean;
  /** Go to a project (Explain this skill, Ask for a change). */
  onOpenProject?: (id: string) => void;
  /** A skill was copied, changed or deleted from here: the other lists reload too. */
  onChanged?: () => void;
}) {
  const [kind, setKind] = useState<LibraryItemKind>("skills");
  const [query, setQuery] = useState("");
  const q = useDebounced(query.trim(), 250);
  const [source, setSource] = useState<string>("");
  const [state, setState] = useState<string>("");
  const [items, setItems] = useState<LibraryItem[] | null>(null);
  const [total, setTotal] = useState(0);
  // Counts per source with no source filter, so the chips keep their numbers while one is chosen.
  const [counts, setCounts] = useState<Record<string, number>>({});
  const [index, setIndex] = useState<LibraryIndex | null>(initialIndex ?? null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [rowErrors, setRowErrors] = useState<Record<string, string>>({});
  const [attempt, setAttempt] = useState(0);
  /** The id of the skill being read. */
  const [openSkill, setOpenSkill] = useState<string | null>(null);
  const ticket = useRef(0);
  const searchId = useId();
  const stateId = useId();

  // The page's search box drives this one; the person can still type here afterwards.
  const kindNow = useRef(kind);
  kindNow.current = kind;
  useEffect(() => {
    if (search === undefined) return;
    setQuery(search);
    if (search.trim() && preferKind && preferKind !== kindNow.current) {
      setKind(preferKind);
      setSource("");
      setState("");
      setCounts({});
    }
  }, [search, preferKind]);

  const load = useCallback(
    (offset: number, signal?: AbortSignal) => {
      const mine = ++ticket.current;
      setLoading(true);
      setError(null);
      return api
        .libraryItems({ kind, q, source: source || null, state: state || null, offset, limit: PAGE_SIZE }, signal)
        .then(
          (page) => {
            if (mine !== ticket.current) return;
            setItems((previous) => mergePage(previous, page, offset > 0));
            setTotal(page.total);
            setIndex(page.index);
            if (!source) setCounts(page.counts);
          },
          (cause) => {
            if ((cause as Error)?.name === "AbortError" || mine !== ticket.current) return;
            setError(errorMessage(cause));
          },
        )
        .finally(() => {
          if (mine === ticket.current) setLoading(false);
        });
    },
    [kind, q, source, state],
  );

  useEffect(() => {
    const controller = new AbortController();
    setItems(null);
    void load(0, controller.signal);
    return () => controller.abort();
  }, [load, attempt]);

  const changeKind = (next: LibraryItemKind) => {
    if (next === kind) return;
    setKind(next);
    setSource("");
    setState("");
    setCounts({});
  };

  const decide = (item: LibraryItem, decision: Decision) => {
    const body = kind === "connectors" ? "mcp_servers" : "skills";
    const before = item;
    setItems((list) => list?.map((row) => (row.id === item.id ? withDecision(row, "machine", decision) : row)) ?? list);
    setRowErrors(({ [item.id]: _gone, ...rest }) => rest);
    saveDecision(body, item.id, "machine", decision).catch((cause) => {
      setItems((list) => list?.map((row) => (row.id === item.id ? before : row)) ?? list);
      setRowErrors((map) => ({ ...map, [item.id]: errorMessage(cause) }));
    });
  };

  const refresh = () => {
    setRefreshing(true);
    api.refreshLibrary().then(
      (fresh) => {
        setIndex(fresh);
        setAttempt((n) => n + 1);
      },
      (cause) => setError(errorMessage(cause)),
    ).finally(() => setRefreshing(false));
  };

  const allCount = Object.values(counts).reduce((sum, n) => sum + n, 0);
  const sources = SOURCES[kind].filter((option) => option.value === source || (counts[option.value] ?? 0) > 0);
  const states = statesFor(kind);
  const left = items ? remaining(items.length, total) : 0;

  return (
    <div className="lix">
      <div className="lix-summary">
        <p className="lib-intro lix-summary-text" aria-live="polite">
          {index ? indexSummary(index) + "." : "Looking through this computer…"}
        </p>
        <button type="button" className="btn btn-small" onClick={refresh} disabled={refreshing}>
          {refreshing ? <Spinner size={13} /> : <Icon name="refresh" size={13} />} Refresh
        </button>
      </div>

      <div className="seg lix-kinds" role="tablist" aria-label="What to list">
        {KINDS.map((option, i) => (
          <button
            key={option.kind}
            type="button"
            role="tab"
            id={`lix-tab-${option.kind}`}
            aria-selected={kind === option.kind}
            aria-controls="lix-panel"
            tabIndex={kind === option.kind ? 0 : -1}
            className={`seg-item${kind === option.kind ? " is-on" : ""}`}
            onClick={() => changeKind(option.kind)}
            onKeyDown={(event) => {
              const step = { ArrowRight: 1, ArrowLeft: -1 }[event.key];
              if (!step) return;
              event.preventDefault();
              const next = KINDS[(i + step + KINDS.length) % KINDS.length].kind;
              changeKind(next);
              document.getElementById(`lix-tab-${next}`)?.focus();
            }}
          >
            {option.label}
            {index && <span className="lib-count">{fmt(index[option.kind])}</span>}
          </button>
        ))}
      </div>

      <div id="lix-panel" role="tabpanel" aria-labelledby={`lix-tab-${kind}`} className="lix-panel">
        <div className="lix-filters">
          <div className="lib-filter lix-search">
            <Icon name="search" size={14} />
            <label htmlFor={searchId} className="sr-only">
              Search {NOUN[kind]}
            </label>
            <input
              id={searchId}
              type="search"
              value={query}
              placeholder={`Search ${NOUN[kind]}`}
              onChange={(event) => setQuery(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Escape" && query) {
                  event.stopPropagation();
                  setQuery("");
                }
              }}
            />
          </div>
          {states.length > 0 && (
            <label className="lix-state" htmlFor={stateId}>
              <span className="sr-only">Show</span>
              <select id={stateId} value={state} onChange={(event) => setState(event.target.value)}>
                {states.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </select>
            </label>
          )}
        </div>

        <div className="lix-sources" role="group" aria-label="Where they come from">
          <button
            type="button"
            className={`lix-chip${source === "" ? " is-on" : ""}`}
            aria-pressed={source === ""}
            onClick={() => setSource("")}
          >
            All <span className="lib-count">{fmt(allCount)}</span>
          </button>
          {sources.map((option) => (
            <button
              key={option.value}
              type="button"
              className={`lix-chip${source === option.value ? " is-on" : ""}`}
              aria-pressed={source === option.value}
              onClick={() => setSource(source === option.value ? "" : option.value)}
            >
              {option.label} <span className="lib-count">{fmt(counts[option.value] ?? 0)}</span>
            </button>
          ))}
        </div>

        {kind === "agents" && (
          <p className="lib-intro">Listed only: the studio loads its own two helper agents.</p>
        )}

        {error && (
          <div className="lib-error" role="alert">
            <Icon name="alert" size={15} />
            <span>The list couldn’t be loaded: {error}</span>
            <button type="button" className="btn btn-small" onClick={() => setAttempt((n) => n + 1)}>
              Try again
            </button>
          </div>
        )}

        {items === null && !error ? (
          <div className="panel-empty">
            <Spinner />
          </div>
        ) : items && items.length === 0 && !error ? (
          <p className="lib-empty" role="status">
            {q ? `Nothing matches “${q}”.` : `No ${NOUN[kind]} here.`}
          </p>
        ) : (
          items && (
            <>
              <p className="lix-count" role="status">
                {q || source || state ? `${fmt(total)} found` : `${fmt(total)} ${NOUN[kind]}`}
                {left > 0 && ` · showing ${fmt(items.length)}`}
              </p>
              <ul className="lib-rows lix-rows">
                {items.map((item) => (
                  <ItemRow
                    key={item.id}
                    item={item}
                    kind={kind}
                    error={rowErrors[item.id]}
                    onDecide={(decision) => decide(item, decision)}
                    onOpen={() => setOpenSkill(item.id)}
                  />
                ))}
              </ul>
              {left > 0 && (
                <button
                  type="button"
                  className="btn btn-small lix-more"
                  disabled={loading}
                  onClick={() => void load(items.length)}
                >
                  {loading ? <Spinner size={13} /> : null} Show more ({fmt(Math.min(PAGE_SIZE, left))} of {fmt(left)} left)
                </button>
              )}
            </>
          )
        )}
      </div>
      {openSkill && (
        <SkillOpenDialog
          id={openSkill}
          shared={shared}
          onClose={() => setOpenSkill(null)}
          onChanged={() => {
            setAttempt((n) => n + 1);
            onChanged?.();
          }}
          onOpenProject={(id) => (onOpenProject ? onOpenProject(id) : window.location.assign(`/p/${id}`))}
        />
      )}
    </div>
  );
}

export function ItemRow({
  item,
  kind,
  error,
  onDecide,
  onOpen,
}: {
  item: LibraryItem;
  kind: LibraryItemKind;
  error?: string;
  onDecide: (decision: Decision) => void;
  onOpen?: () => void;
}) {
  const title = item.label || item.name;
  const facts = kind === "skills" ? skillFacts(item) : kind === "connectors" && item.plugin_enabled === false ? ["plugin switched off in Claude Code"] : [];
  return (
    <li className={`lib-row lix-row${item.benched ? " is-benched" : ""}`}>
      <div className="lib-row-main">
        <span className="lib-row-title lix-title">
          {title}
          {item.name && item.name !== title && <code className="lix-id">{item.name}</code>}
        </span>
        {item.origin && <span className="lix-origin">{item.origin}</span>}
        {item.description && <span className="lib-row-detail pcap-clamp">{item.description}</span>}
        {facts.length > 0 && <span className="lix-facts">{facts.join(" · ")}</span>}
        {kind === "connectors" && <ConnectorStateNote state={item.state} fix={item.fix} />}
        {error && (
          <p className="ds-error" role="alert">
            {error}
          </p>
        )}
      </div>
      {kind === "skills" && onOpen && (
        <button type="button" className="btn btn-small lix-open" aria-label={`Open the skill ${title}`} onClick={onOpen}>
          Open
        </button>
      )}
      {kind === "skills" && (
        <a
          className="btn btn-small lix-export"
          href={api.skillExportUrl(item.id)}
          download
          aria-label={`Export the skill ${title}`}
        >
          <Icon name="download" size={13} /> Export
        </a>
      )}
      {kind !== "agents" && (
        <DecisionControl
          value={fromPref(item.pref)}
          label={`${title} on this computer`}
          onChange={onDecide}
        />
      )}
    </li>
  );
}
