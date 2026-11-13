// Inside a project: what the studio picked for the last request, the studio's own skills,
// and what the person said always or never about in this project (step 3o). Changes are
// saved straight away (optimistic, rolled back on error) and take effect from the next message.

import { useCallback, useEffect, useId, useRef, useState } from "react";
import { api, errorMessage } from "./api";
import { Icon, Spinner } from "./icons";
import { ConnectorStateNote, DecisionControl, useDebounced } from "./LibraryControls";
import { mcpTarget, MCP_LIMIT, skillName } from "./library";
import { PickList } from "./LibraryPickCard";
import {
  fmt,
  fromChoice,
  mergePage,
  PROJECT_PAGE_SIZE,
  remaining,
  toChoice,
  type Decision,
  type DecisionKind,
} from "./libraryPick";
import { transportLabel } from "./McpServersView";
import type { LibraryItem, MachinePref, ProjectChoice, ProjectLibrary } from "./types";

type Skill = ProjectLibrary["skills"][number];
type Server = ProjectLibrary["mcp_servers"][number];

/** The studio's own skills and the ones added in it: on by default, only a switch. */
export const isOwnSkill = (skill: { source: string }) => skill.source === "studio" || skill.source === "yours";

/** What a turn loads without being asked, after a change of this project's word. */
export function enabledAfter(choice: ProjectChoice, pref: MachinePref | undefined, byDefault: boolean): boolean {
  if (choice !== null) return choice;
  if (pref) return pref === "always";
  return byDefault;
}

/** The project's library with one item's project choice changed (optimistic update and roll back). */
export function withProjectChoice(library: ProjectLibrary, kind: DecisionKind, id: string, choice: ProjectChoice): ProjectLibrary {
  if (kind === "skills") {
    return {
      ...library,
      skills: library.skills.map((item) =>
        item.id === id ? { ...item, choice, enabled: enabledAfter(choice, item.pref, isOwnSkill(item)) } : item,
      ),
    };
  }
  return {
    ...library,
    mcp_servers: library.mcp_servers.map((item) =>
      item.id === id ? { ...item, choice, enabled: enabledAfter(choice, item.pref, false) } : item,
    ),
  };
}

export function pinnedConnectors(library: ProjectLibrary): number {
  return library.mcp_servers.filter((item) => item.choice === true).length;
}

function Switch({ on, label, disabled, onToggle }: { on: boolean; label: string; disabled?: boolean; onToggle: () => void }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={on}
      aria-label={label}
      className={`switch${on ? " is-on" : ""}`}
      disabled={disabled}
      onClick={onToggle}
    >
      <span className="switch-thumb" aria-hidden="true" />
    </button>
  );
}

function machineWord(pref: MachinePref | undefined): string | null {
  return pref === "always" ? "Always on this computer" : pref === "never" ? "Never on this computer" : null;
}

function whenLabel(iso: string | undefined): string | null {
  if (!iso) return null;
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return null;
  return date.toLocaleString(undefined, { day: "numeric", month: "short", hour: "numeric", minute: "2-digit" });
}

export function ProjectCapabilities({ projectId }: { projectId: string }) {
  const [library, setLibrary] = useState<ProjectLibrary | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);
  const [rowErrors, setRowErrors] = useState<Record<string, string>>({});
  const [pending, setPending] = useState<Record<string, boolean>>({});
  // Changes answer out of order; only the newest answer replaces the whole list.
  const generation = useRef(0);

  useEffect(() => {
    const controller = new AbortController();
    setLoadError(null);
    api.getProjectLibrary(projectId, controller.signal).then(setLibrary, (cause) => {
      if ((cause as Error)?.name !== "AbortError") setLoadError(errorMessage(cause));
    });
    return () => controller.abort();
  }, [projectId, attempt]);

  /** Save this project's word about one item. Returns false when it was refused before sending. */
  const choose = useCallback(
    (kind: DecisionKind, id: string, choice: ProjectChoice, previous: ProjectChoice) => {
      const key = `${kind}:${id}`;
      const ticket = ++generation.current;
      setLibrary((current) => (current ? withProjectChoice(current, kind, id, choice) : current));
      setPending((map) => ({ ...map, [key]: true }));
      setRowErrors(({ [key]: _gone, ...rest }) => rest);
      return api
        .putProjectLibrary(projectId, { [kind]: { [id]: choice } })
        .then(
          (answer) => {
            if (ticket === generation.current) setLibrary(answer);
            return true;
          },
          (cause) => {
            setLibrary((current) => (current ? withProjectChoice(current, kind, id, previous) : current));
            setRowErrors((map) => ({ ...map, [key]: errorMessage(cause) }));
            return false;
          },
        )
        .finally(() => setPending((map) => ({ ...map, [key]: false })));
    },
    [projectId],
  );

  if (loadError && !library) {
    return (
      <div className="plib-data">
        <div className="lib-error" role="alert">
          <Icon name="alert" size={15} />
          <span>This project’s skills and connectors couldn’t be loaded: {loadError}</span>
          <button type="button" className="btn btn-small" onClick={() => setAttempt((n) => n + 1)}>
            Try again
          </button>
        </div>
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

  const own = library.skills.filter(isOwnSkill);
  const decided = library.skills.filter((skill) => !isOwnSkill(skill));
  const pinned = pinnedConnectors(library);
  const requested = new Set(library.requested ?? []);
  const pick = library.last_pick;
  const pickWhen = whenLabel(pick?.at);

  const decideConnector = (server: Server, decision: Decision) => {
    const key = `mcp_servers:${server.id}`;
    if (decision === "always" && server.choice !== true && pinned >= MCP_LIMIT) {
      setRowErrors((map) => ({ ...map, [key]: `A project can keep at most ${MCP_LIMIT} connectors always on.` }));
      return;
    }
    void choose("mcp_servers", server.id, toChoice(decision), server.choice ?? null);
  };

  return (
    <div className="plib-data pcap">
      <p className="ds-lede">
        Before each request the studio picks the skills and connectors that fit. Decide here what this project always or
        never gets. <strong>Takes effect from your next message.</strong>
      </p>

      <section aria-labelledby="pcap-pick" className="pcap-block">
        <h3 id="pcap-pick" className="lib-title">
          Picked for the last request
          {pickWhen && <span className="lib-count">{pickWhen}</span>}
        </h3>
        {pick ? (
          <div className="pcap-pick">
            <PickList pick={pick} projectId={projectId} />
          </div>
        ) : (
          <p className="lib-empty">Nothing yet. After your next message this shows what was picked and why.</p>
        )}
      </section>

      <section aria-labelledby="pcap-own" className="pcap-block">
        <h3 id="pcap-own" className="lib-title">
          The studio’s skills <span className="lib-count">{own.filter((s) => s.enabled).length} on</span>
        </h3>
        <p className="lib-intro">Always loaded unless you switch them off here.</p>
        {own.length === 0 ? (
          <p className="lib-empty">None.</p>
        ) : (
          <ul className="lib-rows">
            {own.map((skill) => {
              const key = `skills:${skill.id}`;
              const title = skill.label || skillName(skill);
              return (
                <li key={skill.id} className="lib-row">
                  <div className="lib-row-main">
                    <span className="lib-row-title">{title}</span>
                    {skill.description && <span className="lib-row-detail pcap-clamp">{skill.description}</span>}
                    {rowErrors[key] && (
                      <p className="ds-error" role="alert">
                        {rowErrors[key]}
                      </p>
                    )}
                  </div>
                  <Switch
                    on={skill.enabled}
                    label={`Use the skill ${title} in this project`}
                    disabled={pending[key]}
                    // Off is "never in this project"; on hands it back to the studio (on by default).
                    onToggle={() => void choose("skills", skill.id, skill.enabled ? false : null, skill.choice ?? null)}
                  />
                </li>
              );
            })}
          </ul>
        )}
      </section>

      <section aria-labelledby="pcap-decided" className="pcap-block">
        <h3 id="pcap-decided" className="lib-title">
          Always / never in this project <span className="lib-count">{decided.length}</span>
        </h3>
        {decided.length === 0 ? (
          <p className="lib-empty">You haven’t decided about any other skill. Find one below to keep it always or never.</p>
        ) : (
          <ul className="lib-rows">
            {decided.map((skill) => (
              <DecidedSkillRow
                key={skill.id}
                skill={skill}
                busy={pending[`skills:${skill.id}`]}
                error={rowErrors[`skills:${skill.id}`]}
                onDecide={(decision) => void choose("skills", skill.id, toChoice(decision), skill.choice ?? null)}
              />
            ))}
          </ul>
        )}
        <SkillFinder
          projectId={projectId}
          onDecide={(item, decision) => choose("skills", item.id, toChoice(decision), item.choice ?? null)}
        />
      </section>

      <section aria-labelledby="pcap-mcp" className="pcap-block">
        <h3 id="pcap-mcp" className="lib-title">
          Connectors{" "}
          <span className={`lib-count${pinned >= MCP_LIMIT ? " is-full" : ""}`}>
            {pinned} of {MCP_LIMIT} always on
          </span>
        </h3>
        <p className="lib-intro">
          Auto: picked when a request needs it. A connector the studio picks by itself asks before its first use.
        </p>
        {library.mcp_servers.length === 0 ? (
          <p className="lib-empty">No connectors. Add them in the Library.</p>
        ) : (
          <ul className="lib-rows">
            {library.mcp_servers.map((server) => {
              const key = `mcp_servers:${server.id}`;
              const title = server.label || server.name;
              const machine = machineWord(server.pref);
              return (
                <li key={server.id} className="lib-row lix-row">
                  <div className="lib-row-main">
                    <span className="lib-row-title">{title}</span>
                    <span className="mcp-meta">
                      <span className="lib-status is-neutral">{transportLabel(server.transport)}</span>
                      {requested.has(server.id) && <span className="lib-status is-ok">asked for by the assistant</span>}
                      <code className="mcp-target" title={mcpTarget(server)}>
                        {mcpTarget(server) || "—"}
                      </code>
                    </span>
                    {server.origin && <span className="lix-origin">{server.origin}</span>}
                    {machine && server.choice == null && <span className="lix-facts">{machine}</span>}
                    <ConnectorStateNote state={server.state} fix={server.fix} />
                    {rowErrors[key] && (
                      <p className="ds-error" role="alert">
                        {rowErrors[key]}
                      </p>
                    )}
                  </div>
                  <DecisionControl
                    value={fromChoice(server.choice)}
                    label={`${title} in this project`}
                    disabled={pending[key]}
                    onChange={(decision) => decideConnector(server, decision)}
                  />
                </li>
              );
            })}
          </ul>
        )}
      </section>
    </div>
  );
}

function DecidedSkillRow({
  skill,
  busy,
  error,
  onDecide,
}: {
  skill: Skill;
  busy?: boolean;
  error?: string;
  onDecide: (decision: Decision) => void;
}) {
  const title = skill.label || skillName(skill);
  const machine = machineWord(skill.pref);
  return (
    <li className="lib-row lix-row">
      <div className="lib-row-main">
        <span className="lib-row-title">{title}</span>
        {skill.origin && <span className="lix-origin">{skill.origin}</span>}
        {skill.description && <span className="lib-row-detail pcap-clamp">{skill.description}</span>}
        {machine && <span className="lix-facts">{machine}{skill.choice != null ? "; this project decides" : ""}</span>}
        {error && (
          <p className="ds-error" role="alert">
            {error}
          </p>
        )}
      </div>
      <DecisionControl value={fromChoice(skill.choice)} label={`${title} in this project`} disabled={busy} onChange={onDecide} />
    </li>
  );
}

/** Search the whole index for more skills to keep always or never in this project. */
function SkillFinder({
  projectId,
  onDecide,
}: {
  projectId: string;
  onDecide: (item: LibraryItem, decision: Decision) => Promise<boolean>;
}) {
  const [query, setQuery] = useState("");
  const q = useDebounced(query.trim(), 250);
  const [items, setItems] = useState<LibraryItem[] | null>(null);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const ticket = useRef(0);
  const searchId = useId();

  const load = useCallback(
    (offset: number, signal?: AbortSignal) => {
      const mine = ++ticket.current;
      setLoading(true);
      setError(null);
      api
        .libraryItems({ kind: "skills", q, project: projectId, offset, limit: PROJECT_PAGE_SIZE }, signal)
        .then(
          (page) => {
            if (mine !== ticket.current) return;
            setItems((previous) => mergePage(previous, page, offset > 0));
            setTotal(page.total);
          },
          (cause) => {
            if ((cause as Error)?.name !== "AbortError" && mine === ticket.current) setError(errorMessage(cause));
          },
        )
        .finally(() => {
          if (mine === ticket.current) setLoading(false);
        });
    },
    [q, projectId],
  );

  useEffect(() => {
    if (!q) {
      ticket.current++;
      setItems(null);
      setError(null);
      setLoading(false);
      return;
    }
    const controller = new AbortController();
    setItems(null);
    load(0, controller.signal);
    return () => controller.abort();
  }, [q, load]);

  const decide = (item: LibraryItem, decision: Decision) => {
    const before = item.choice ?? null;
    const set = (choice: ProjectChoice) =>
      setItems((list) => list?.map((row) => (row.id === item.id ? { ...row, choice } : row)) ?? list);
    set(toChoice(decision));
    void onDecide(item, decision).then((ok) => {
      if (!ok) set(before);
    });
  };

  const left = items ? remaining(items.length, total) : 0;

  return (
    <div className="pcap-finder">
      <div className="lib-filter">
        <Icon name="search" size={14} />
        <label htmlFor={searchId} className="sr-only">
          Find a skill on this computer
        </label>
        <input
          id={searchId}
          type="search"
          value={query}
          placeholder="Find a skill on this computer"
          onChange={(event) => setQuery(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Escape" && query) {
              event.stopPropagation();
              setQuery("");
            }
          }}
        />
        {loading && <Spinner size={13} />}
      </div>
      {error && (
        <p className="ds-error" role="alert">
          {error}
        </p>
      )}
      {items && items.length === 0 && <p className="lib-empty" role="status">Nothing matches “{q}”.</p>}
      {items && items.length > 0 && (
        <>
          <p className="lix-count" role="status">
            {fmt(total)} found{left > 0 ? ` · showing ${fmt(items.length)}` : ""}
          </p>
          <ul className="lib-rows">
            {items.map((item) => {
              const title = item.label || item.name;
              const machine = machineWord(item.pref);
              return (
                <li key={item.id} className="lib-row lix-row">
                  <div className="lib-row-main">
                    <span className="lib-row-title">{title}</span>
                    {item.origin && <span className="lix-origin">{item.origin}</span>}
                    {item.description && <span className="lib-row-detail pcap-clamp">{item.description}</span>}
                    {(machine || item.benched || item.enabled === false) && (
                      <span className="lix-facts">
                        {[
                          machine,
                          item.benched ? "no longer picked" : null,
                          item.enabled === false ? "plugin switched off in Claude Code" : null,
                        ]
                          .filter(Boolean)
                          .join(" · ")}
                      </span>
                    )}
                  </div>
                  <DecisionControl
                    value={fromChoice(item.choice)}
                    label={`${title} in this project`}
                    onChange={(decision) => decide(item, decision)}
                  />
                </li>
              );
            })}
          </ul>
          {left > 0 && (
            <button type="button" className="btn btn-small lix-more" disabled={loading} onClick={() => load(items.length)}>
              Show more ({fmt(Math.min(PROJECT_PAGE_SIZE, left))} of {fmt(left)} left)
            </button>
          )}
        </>
      )}
    </div>
  );
}

