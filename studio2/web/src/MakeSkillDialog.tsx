// The project's menu → Make a skill from this…: what to learn from, and what changes each time.

import { useId, useState } from "react";
import { Spinner } from "./icons";
import { Modal } from "./Modal";
import type { Selection } from "./types";
import type { MenuEntry } from "./ui";

/** The same entry in both of the project's menus. Open while the agent works: the request waits its turn. */
export function makeSkillEntry(onSelect: () => void): MenuEntry {
  return { label: "Make a skill from this…", icon: "sparkle", onSelect };
}

interface MakeSkillDialogProps {
  hasBackEnd: boolean;
  /** What is selected on the page now, if anything. */
  selection: Selection | null;
  /** Another message is on its way: Start waits for it. */
  sending: boolean;
  onStart: (note: string, part: Selection | null) => Promise<void>;
  onClose: () => void;
}

export function MakeSkillDialog({ hasBackEnd, selection, sending, onStart, onClose }: MakeSkillDialogProps) {
  const [what, setWhat] = useState<"whole" | "part">(selection ? "part" : "whole");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const ids = useId();
  const usePart = what === "part" && selection !== null;

  const start = async () => {
    setBusy(true);
    try {
      await onStart(note, usePart ? selection : null);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal title="Make a skill from this" onClose={onClose} busy={busy} wide>
      <form
        className="ds-form"
        onSubmit={(event) => {
          event.preventDefault();
          void start();
        }}
      >
        <fieldset className="field save-what">
          <legend>What should the skill learn from?</legend>
          <label className={`save-choice${usePart ? "" : " is-on"}`}>
            <input
              type="radio"
              name={`${ids}-what`}
              checked={!usePart}
              onChange={() => setWhat("whole")}
            />
            <span>{hasBackEnd ? "The whole app" : "The whole page"}</span>
          </label>
          {selection && (
            <label className={`save-choice${usePart ? " is-on" : ""}`}>
              <input type="radio" name={`${ids}-what`} checked={usePart} onChange={() => setWhat("part")} />
              <span>The part I selected</span>
            </label>
          )}
        </fieldset>
        <label className="field" htmlFor={`${ids}-note`}>
          <span>What changes each time? (optional)</span>
          <input
            id={`${ids}-note`}
            data-autofocus
            value={note}
            placeholder="For example: the company and the quarter"
            onChange={(event) => setNote(event.target.value)}
          />
        </label>
        <p className="field-hint">
          The agent works out how this was made and writes the steps down. It shows you the skill and asks before adding
          it to your library.
        </p>
        <div className="dialog-actions">
          <button type="button" className="btn" disabled={busy} onClick={onClose}>
            Cancel
          </button>
          <button type="submit" className="btn btn-primary" disabled={busy || sending}>
            {busy && <Spinner size={13} />}
            Start
          </button>
        </div>
      </form>
    </Modal>
  );
}
