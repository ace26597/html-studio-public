// Step 3o: small pieces shared by the library page, the project panel and the "picked" card.

import { useEffect, useState } from "react";
import { Icon } from "./icons";
import { connectorState, type Decision } from "./libraryPick";
import type { ConnectorState } from "./types";

const DECISIONS: { value: Decision; label: string }[] = [
  { value: "auto", label: "Auto" },
  { value: "always", label: "Always" },
  { value: "never", label: "Never" },
];

/** Auto / Always / Never as one radio group (arrow keys move and choose, like the other segmented controls). */
export function DecisionControl({
  value,
  onChange,
  label,
  disabled,
}: {
  value: Decision;
  onChange: (next: Decision) => void;
  /** Read out for the group, e.g. "Use the skill pdf". */
  label: string;
  disabled?: boolean;
}) {
  return (
    <div className="seg decide" role="radiogroup" aria-label={label} aria-disabled={disabled || undefined}>
      {DECISIONS.map((option, index) => {
        const on = option.value === value;
        return (
          <button
            key={option.value}
            type="button"
            role="radio"
            aria-checked={on}
            tabIndex={on ? 0 : -1}
            disabled={disabled}
            className={`seg-item decide-item is-${option.value}${on ? " is-on" : ""}`}
            onClick={() => {
              if (!on) onChange(option.value);
            }}
            onKeyDown={(event) => {
              const step = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 }[event.key];
              if (!step) return;
              event.preventDefault();
              const next = DECISIONS[(index + step + DECISIONS.length) % DECISIONS.length];
              onChange(next.value);
              const buttons = event.currentTarget.parentElement?.querySelectorAll<HTMLButtonElement>("button");
              buttons?.[DECISIONS.indexOf(next)]?.focus();
            }}
          >
            {option.label}
          </button>
        );
      })}
    </div>
  );
}

/** A connector's sign-in / setting / failure state, with the sign-in command to copy. */
export function ConnectorStateNote({ state, fix }: { state: ConnectorState | undefined; fix: string | null | undefined }) {
  const line = connectorState(state, fix);
  const [copied, setCopied] = useState(false);
  if (!line) return null;
  return (
    <div className={`conn-state is-${line.tone}`}>
      <span className="conn-state-text">
        <Icon name={line.tone === "err" ? "alert" : "warning"} size={13} /> {line.text}
      </span>
      {line.sentence && <span className="conn-state-fix">{line.sentence}</span>}
      {line.command && (
        <span className="conn-cmd">
          <code>{line.command}</code>
          <button
            type="button"
            className="btn btn-small conn-copy"
            aria-label={`Copy the command ${line.command}`}
            onClick={() => {
              void navigator.clipboard?.writeText(line.command as string).then(
                () => {
                  setCopied(true);
                  window.setTimeout(() => setCopied(false), 1600);
                },
                () => setCopied(false),
              );
            }}
          >
            <Icon name={copied ? "check" : "file"} size={13} /> {copied ? "Copied" : "Copy"}
          </button>
        </span>
      )}
    </div>
  );
}

/** `value`, settled for `ms` milliseconds. */
export function useDebounced<T>(value: T, ms = 250): T {
  const [settled, setSettled] = useState(value);
  useEffect(() => {
    const timer = window.setTimeout(() => setSettled(value), ms);
    return () => window.clearTimeout(timer);
  }, [value, ms]);
  return settled;
}
