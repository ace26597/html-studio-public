// Step 3o: "Picked 6 skills and 1 connector for this request", at the top of a turn,
// and the same list (without the buttons) in the project's library panel.

import { useId, useState } from "react";
import { errorMessage } from "./api";
import { Icon } from "./icons";
import { ConnectorStateNote } from "./LibraryControls";
import {
  howWords,
  listSize,
  pickHeadline,
  pickModeWords,
  pickTime,
  saveDecision,
  savedWords,
  type Decision,
  type DecisionKind,
  type Scope,
} from "./libraryPick";
import type { LibraryPick, PickedConnector, PickedSkill } from "./types";

type Entry = { kind: DecisionKind; item: PickedSkill | PickedConnector };

/** One line about how the pick went: mode, time and list size. */
export function pickFacts(pick: LibraryPick): string {
  return [pickModeWords(pick.mode), pickTime(pick), listSize(pick)].filter(Boolean).join(" · ");
}

export function LibraryPickCard({ pick, projectId }: { pick: LibraryPick; projectId?: string | null }) {
  const [open, setOpen] = useState(false);
  const bodyId = useId();
  const headline = pickHeadline(pick);
  if (!headline) return null;
  return (
    <div className={`group pick-card${open ? " is-open" : ""}`}>
      <button
        type="button"
        className="tool-head group-head"
        aria-expanded={open}
        aria-controls={bodyId}
        onClick={() => setOpen((v) => !v)}
      >
        <Icon name={open ? "chevron-down" : "chevron-right"} size={14} className="tool-icon" />
        <span className="tool-title">{headline}</span>
        <Icon name="book" size={13} className="tool-mark" />
      </button>
      {open && (
        <div className="group-body pick-body" id={bodyId}>
          <PickList pick={pick} projectId={projectId} decide />
        </div>
      )}
    </div>
  );
}

/** The picked items with their reasons; `decide` adds Always / Never buttons. */
export function PickList({
  pick,
  projectId,
  decide = false,
}: {
  pick: LibraryPick;
  projectId?: string | null;
  decide?: boolean;
}) {
  const entries: Entry[] = [
    ...pick.skills.map((item) => ({ kind: "skills" as const, item })),
    ...pick.connectors.map((item) => ({ kind: "mcp_servers" as const, item })),
  ];
  return (
    <div className="pick-list">
      <p className="pick-facts">{pickFacts(pick)}</p>
      {pick.note && <p className="pick-note">{pick.note}</p>}
      {entries.length === 0 ? (
        <p className="pick-facts">Only the studio’s own skills were loaded.</p>
      ) : (
        <ul className="pick-items">
          {entries.map((entry) => (
            <PickRow
              key={`${entry.kind}:${entry.item.id}`}
              entry={entry}
              projectId={projectId}
              decide={decide && entry.item.source !== "studio"}
            />
          ))}
        </ul>
      )}
    </div>
  );
}

function PickRow({ entry, projectId, decide }: { entry: Entry; projectId?: string | null; decide: boolean }) {
  const { kind, item } = entry;
  const [scope, setScope] = useState<Scope>(projectId ? "project" : "machine");
  const [saved, setSaved] = useState<{ scope: Scope; decision: Decision } | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const scopeId = useId();
  const name = item.label || item.name;
  const connector = kind === "mcp_servers" ? (item as PickedConnector) : null;

  const save = (decision: Decision, where: Scope) => {
    setBusy(true);
    setError(null);
    saveDecision(kind, item.id, where, decision, projectId).then(
      () => setSaved(decision === "auto" ? null : { scope: where, decision }),
      (cause) => setError(errorMessage(cause)),
    ).finally(() => setBusy(false));
  };

  return (
    <li className="pick-item">
      <div className="pick-item-main">
        <span className="pick-item-name">
          <Icon name={connector ? "globe" : "sparkle"} size={13} />
          <span>{name}</span>
          {/* "Picked for this request" is the default; only other reasons get a tag. */}
          {item.how !== "picked" && <span className="lib-status is-neutral">{howWords(item.how)}</span>}
        </span>
        {item.origin && <span className="lix-origin">{item.origin}</span>}
        {item.reason && <span className="pick-reason">{item.reason}</span>}
        {connector && <ConnectorStateNote state={connector.state} fix={connector.fix} />}
        {error && (
          <p className="ds-error" role="alert">
            {error}
          </p>
        )}
      </div>
      {decide && (
        <div className="pick-actions">
          {saved ? (
            <span className="pick-saved" role="status">
              <Icon name="check" size={13} /> {savedWords(saved.scope, saved.decision)}
              <button type="button" className="btn btn-small" disabled={busy} onClick={() => save("auto", saved.scope)}>
                Undo
              </button>
            </span>
          ) : (
            <>
              <button
                type="button"
                className="btn btn-small"
                disabled={busy}
                aria-label={`Always use ${name}`}
                onClick={() => save("always", scope)}
              >
                Always
              </button>
              <button
                type="button"
                className="btn btn-small"
                disabled={busy}
                aria-label={`Never use ${name}`}
                onClick={() => save("never", scope)}
              >
                Never
              </button>
              <label className="pick-scope" htmlFor={scopeId}>
                <span className="sr-only">Where to keep this for {name}</span>
                <select id={scopeId} value={scope} onChange={(event) => setScope(event.target.value as Scope)}>
                  {projectId && <option value="project">this project</option>}
                  <option value="machine">this computer</option>
                </select>
              </label>
            </>
          )}
        </div>
      )}
    </li>
  );
}
