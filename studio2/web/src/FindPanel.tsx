// The Find panel: a box, the count, previous / next, and the matches in groups (CONTRACT.md,
// "Find on the page and in the chat"). Groups come from `useFind`; more can be added there.

import { Fragment, useEffect, useRef } from "react";
import { markParts, parseQuery, type FindRow } from "./find";
import { Icon } from "./icons";
import { IconButton } from "./ui";
import type { FindState } from "./useFind";

const KIND_LABEL: Record<FindRow["kind"], string | null> = {
  heading: "Section",
  text: null,
  you: "You",
  agent: "Agent",
};

function Marked({ text, query }: { text: string; query: string }) {
  const parts = markParts(text, parseQuery(query));
  return (
    <>
      {parts.map((part, index) => (part.hit ? <strong key={index}>{part.text}</strong> : <Fragment key={index}>{part.text}</Fragment>))}
    </>
  );
}

export function FindPanel({ find }: { find: FindState }) {
  const { query, groups, current, note, countText, outlineShown, inputRef } = find;
  const hasRows = groups.some((group) => group.rows.length > 0);
  const list = useRef<HTMLDivElement>(null);

  // Keep the current row in sight inside the list (only the list scrolls: never the studio).
  useEffect(() => {
    const box = list.current;
    const row = box?.querySelector<HTMLElement>(".find-row.is-current");
    if (!box || !row) return;
    const top = row.offsetTop; // the list is positioned, so this is within it
    if (top < box.scrollTop) box.scrollTop = top - 28;
    else if (top + row.offsetHeight > box.scrollTop + box.clientHeight) box.scrollTop = top + row.offsetHeight - box.clientHeight + 8;
  }, [current, groups]);

  return (
    <section
      className="find-panel"
      role="search"
      aria-label="Find on the page and in the chat"
      onKeyDown={(event) => {
        if (event.key === "Escape") {
          event.preventDefault();
          event.stopPropagation();
          find.close();
        }
      }}
    >
      <div className="find-bar">
        <Icon name="search" size={15} />
        <input
          ref={inputRef}
          className="find-input"
          type="search"
          value={query}
          placeholder="Find a title, a section or any words"
          aria-label="Find"
          aria-describedby="find-count"
          autoComplete="off"
          spellCheck={false}
          onChange={(event) => find.setQuery(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter") {
              event.preventDefault();
              find.step(event.shiftKey ? -1 : 1);
            } else if (event.key === "ArrowDown" || event.key === "ArrowUp") {
              event.preventDefault();
              find.step(event.key === "ArrowDown" ? 1 : -1);
            }
          }}
        />
        <span id="find-count" className="find-count" aria-live="polite">
          {countText}
        </span>
        <IconButton icon="arrow-up" label="Previous match" tip="Previous (Shift+Enter)" disabled={!hasRows || outlineShown} onClick={() => find.step(-1)} />
        <IconButton icon="arrow-down" label="Next match" tip="Next (Enter)" disabled={!hasRows || outlineShown} onClick={() => find.step(1)} />
        <IconButton icon="x" label="Close find" tip="Close (Esc)" tipAlign="end" onClick={find.close} />
      </div>
      {note && (
        <p className="find-note" role="status">
          {note}
        </p>
      )}
      {groups.length > 0 && (
        <div className="find-list" ref={list}>
          {groups.map((group) => (
            <div key={group.id} className="find-group" role="group" aria-label={group.title}>
              <h3 className="find-group-title">
                {group.title}
                {group.rows.length > 0 && <span className="find-group-count">{group.rows.length}</span>}
              </h3>
              {group.note && <p className="find-group-note">{group.note}</p>}
              {group.rows.length > 0 && (
                <ul className="find-rows">
                  {group.rows.map((row) => {
                    const label = KIND_LABEL[row.kind];
                    return (
                      <li key={row.key}>
                        <button
                          type="button"
                          className={`find-row is-${row.kind}${row.key === current ? " is-current" : ""}${row.hidden ? " is-hidden" : ""}`}
                          style={row.level ? { paddingLeft: `${10 + (row.level - 1) * 14}px` } : undefined}
                          aria-current={row.key === current ? "true" : undefined}
                          onClick={row.onGo}
                        >
                          {(row.section || label || row.hidden) && !row.level && (
                            <span className="find-row-where">
                              {label && <span className="find-row-kind">{label}</span>}
                              {row.section && <span className="find-row-section">{row.section}</span>}
                              {row.hidden && <span className="find-row-hidden">not shown now</span>}
                            </span>
                          )}
                          <span className="find-row-text">
                            {row.level ? row.snippet : <Marked text={row.snippet} query={query} />}
                            {row.level && row.hidden ? <span className="find-row-hidden"> not shown now</span> : null}
                          </span>
                        </button>
                      </li>
                    );
                  })}
                </ul>
              )}
            </div>
          ))}
        </div>
      )}
    </section>
  );
}
