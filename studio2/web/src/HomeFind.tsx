// The home page's search box and what it finds inside projects (CONTRACT.md, "Find words in a
// project's pages and across projects"). The cards are filtered by name in Home; this part asks
// the server after a short pause and lists pages and chat lines.

import { useEffect, useState, type MouseEvent, type ReactNode } from "react";
import { api, errorMessage } from "./api";
import { highlight, queryTerms } from "./homeSearch";
import { Icon } from "./icons";
import { projectHref } from "./router";
import type { FindPage, FindProject, StudioFind } from "./types";

export const FIND_PAUSE_MS = 250;

export type FindState =
  | { status: "idle" }
  | { status: "searching"; query: string }
  | { status: "done"; query: string; result: StudioFind }
  | { status: "failed"; query: string; message: string };

/** Pages or chat lines inside projects that the answer holds. */
export function insideCount(state: FindState): number {
  return state.status === "done"
    ? state.result.projects.filter((project) => project.pages.length || project.chat.length).length
    : 0;
}

/** The short word at the end of the box, or null. `byName`: cards whose name matches. */
export function searchStatus(search: string, state: FindState, byName: number): string | null {
  if (!search.trim()) return null;
  if (!queryTerms(search).length) return "Type at least two letters";
  if (state.status === "searching") return "Searching…";
  if (state.status === "done" && byName === 0 && insideCount(state) === 0) return "Nothing found";
  return null;
}

/** Asks `GET /api/find` once the person stops typing for a moment; a newer query cancels an older one. */
export function useStudioFind(query: string): FindState {
  const [state, setState] = useState<FindState>({ status: "idle" });
  useEffect(() => {
    const words = query.trim();
    if (!queryTerms(words).length) {
      setState({ status: "idle" });
      return;
    }
    setState((now) => (now.status === "done" && now.query === words ? now : { status: "searching", query: words }));
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      api.findEverywhere(words, controller.signal).then(
        (result) => {
          // An answer that arrives after a newer query was typed is not this query's any more.
          if (!controller.signal.aborted) setState({ status: "done", query: words, result });
        },
        (cause) => {
          if (!controller.signal.aborted) setState({ status: "failed", query: words, message: errorMessage(cause) });
        },
      );
    }, FIND_PAUSE_MS);
    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [query]);
  return state;
}

/** A snippet with the matched words bold. */
export function Marked({ text, terms }: { text: string; terms: string[] }) {
  return (
    <>
      {highlight(text, terms).map((piece, index) =>
        piece.match ? <mark key={index}>{piece.text}</mark> : <span key={index}>{piece.text}</span>,
      )}
    </>
  );
}

function plainClick(event: MouseEvent): boolean {
  return !(event.metaKey || event.ctrlKey || event.shiftKey || event.button !== 0);
}

function Link({ href, onGo, className, children }: { href: string; onGo: (href: string) => void; className: string; children: ReactNode }) {
  return (
    <a
      className={className}
      href={href}
      onClick={(event) => {
        if (!plainClick(event)) return;
        event.preventDefault();
        onGo(href);
      }}
    >
      {children}
    </a>
  );
}

function PageHits({ project, page, query, terms, onGo }: { project: FindProject; page: FindPage; query: string; terms: string[]; onGo: (href: string) => void }) {
  return (
    <li className="find-page">
      <Link className="find-page-link" href={projectHref(project.id, { page: page.path, find: query })} onGo={onGo}>
        <span className="find-page-head">
          <Icon name="file" size={13} />
          <span className="find-page-title">
            <Marked text={page.title || page.path} terms={terms} />
          </span>
          {page.title && <span className="find-page-path">{page.path}</span>}
        </span>
        {page.hits.map((hit, index) => (
          <span className="find-hit" key={index}>
            {hit.section && <span className="find-hit-section">{hit.section}</span>}
            <span className={`find-hit-text${hit.kind === "heading" ? " is-heading" : ""}`}>
              <Marked text={hit.snippet} terms={terms} />
            </span>
          </span>
        ))}
        {page.more > 0 && <span className="find-more">and {page.more} more in this file</span>}
      </Link>
    </li>
  );
}

/** What was found inside projects: the projects that have pages or chat lines that match. */
export function FindResults({ state, onGo }: { state: FindState; onGo: (href: string) => void }) {
  if (state.status === "idle") return null;
  if (state.status === "failed") {
    return <p className="home-find-note is-error">The search did not work: {state.message}</p>;
  }
  if (state.status === "searching") {
    return (
      <p className="home-find-note" role="status">
        Searching inside the pages…
      </p>
    );
  }
  const { result, query } = state;
  const terms = queryTerms(query);
  const inside = result.projects.filter((project) => project.pages.length || project.chat.length);
  return (
    <section className="find-results" aria-label="Found inside pages">
      {inside.length > 0 && <h2 className="find-results-title">Found inside</h2>}
      <ul className="find-projects">
        {inside.map((project) => (
          <li className="find-project" key={project.id}>
            <Link className="find-project-name" href={projectHref(project.id, { find: query })} onGo={onGo}>
              <Marked text={project.name} terms={project.name_match ? terms : []} />
            </Link>
            {project.pages.length > 0 && (
              <ul className="find-pages">
                {project.pages.map((page) => (
                  <PageHits key={page.path} project={project} page={page} query={query} terms={terms} onGo={onGo} />
                ))}
              </ul>
            )}
            {project.chat.length > 0 && (
              <ul className="find-chat">
                {project.chat.map((line) => (
                  <li key={line.seq}>
                    <Link className="find-chat-link" href={projectHref(project.id, { find: query })} onGo={onGo}>
                      <span className="find-chat-who">{line.role === "person" ? "You" : "Agent"}</span>
                      <span className="find-hit-text">
                        <Marked text={line.snippet} terms={terms} />
                      </span>
                    </Link>
                  </li>
                ))}
              </ul>
            )}
            {(project.more.pages > 0 || project.more.chat > 0) && (
              <p className="find-more">
                Also{" "}
                {[
                  project.more.pages > 0 && `${project.more.pages} more ${project.more.pages === 1 ? "file" : "files"}`,
                  project.more.chat > 0 && `${project.more.chat} more ${project.more.chat === 1 ? "chat line" : "chat lines"}`,
                ]
                  .filter(Boolean)
                  .join(" and ")}
              </p>
            )}
          </li>
        ))}
      </ul>
      {result.capped && (
        <p className="home-find-note">Not everything was searched: some files were too large or it took too long.</p>
      )}
    </section>
  );
}

/** The box itself. Esc clears it. */
export function HomeSearchBox({ value, onChange, status }: { value: string; onChange: (value: string) => void; status: string | null }) {
  return (
    <div className="lib-filter home-search">
      <Icon name="search" size={15} />
      <label htmlFor="home-search" className="sr-only">
        Search pages, their text and chats
      </label>
      <input
        id="home-search"
        type="search"
        value={value}
        placeholder="Search pages, their text and chats"
        autoComplete="off"
        onChange={(event) => onChange(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === "Escape" && value) {
            event.preventDefault();
            event.stopPropagation();
            onChange("");
          }
        }}
      />
      {status && (
        <span className="lib-filter-count" role="status">
          {status}
        </span>
      )}
    </div>
  );
}
