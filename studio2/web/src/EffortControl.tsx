import { useId, useRef, useState } from "react";
import { effortSummary } from "./effortRow";
import { Icon } from "./icons";
import {
  DEPTHS,
  MATCH_MEANS,
  STATUS_LABEL,
  VERIFY_LINE,
  depthLine,
  stepDepth,
  verificationHeadline,
  verificationTone,
  type Depth,
  type TurnOptions,
  type VerificationItem,
} from "./effort";

/**
 * Quick / Standard / Deep, the Check switch and "Check this page", above the composer's buttons.
 * Folded to one row ("Deep · Check on" + "Check this page") so a phone keeps room for the
 * conversation; the row's button opens the steps, the switch and the explanation.
 */
export function EffortControl({
  options,
  onChange,
  onVerifyPage,
  disabled,
}: {
  options: TurnOptions;
  onChange: (next: TurnOptions) => void;
  onVerifyPage: () => void;
  disabled?: boolean;
}) {
  const lineId = useId();
  const panelId = useId();
  const [open, setOpen] = useState(false);
  const toggle = useRef<HTMLButtonElement>(null);
  const pick = (depth: Depth) => depth !== options.depth && onChange({ ...options, depth });
  return (
    <div className={open ? "effort is-open" : "effort"}>
      <div className="effort-row">
        <button
          ref={toggle}
          type="button"
          className="effort-toggle"
          aria-expanded={open}
          aria-controls={panelId}
          title={open ? "Hide how much to do" : "Change how much to do and whether the result is checked"}
          onClick={() => setOpen(!open)}
        >
          <span className="sr-only">How much to do: </span>
          {effortSummary(options)}
          <Icon name="chevron-down" size={13} strokeWidth={2} className="effort-chevron" />
        </button>
        <button type="button" className="effort-check-page" disabled={disabled} onClick={onVerifyPage}>
          Check this page
        </button>
      </div>
      {open && (
        <div
          id={panelId}
          className="effort-panel"
          onKeyDown={(event) => {
            if (event.key === "Escape") {
              event.stopPropagation();
              setOpen(false);
              toggle.current?.focus();
            }
          }}
        >
          <div className="effort-row">
            <div
              className="effort-steps"
              role="radiogroup"
              aria-label="How much to do"
              aria-describedby={lineId}
              onKeyDown={(event) => {
                const next = stepDepth(options.depth, event.key);
                if (next) {
                  event.preventDefault();
                  pick(next);
                  (event.currentTarget.querySelector(`[data-depth="${next}"]`) as HTMLElement | null)?.focus();
                }
              }}
            >
              {DEPTHS.map((step) => (
                <button
                  key={step.id}
                  type="button"
                  role="radio"
                  data-depth={step.id}
                  aria-checked={options.depth === step.id}
                  tabIndex={options.depth === step.id ? 0 : -1}
                  className={options.depth === step.id ? "effort-step on" : "effort-step"}
                  onClick={() => pick(step.id)}
                >
                  {step.label}
                </button>
              ))}
            </div>
            <label className="effort-verify" title={VERIFY_LINE}>
              <input
                type="checkbox"
                checked={options.verify}
                onChange={(event) => onChange({ ...options, verify: event.target.checked })}
              />
              Check the result
            </label>
          </div>
          <p id={lineId} className="effort-line">
            {depthLine(options.depth)}
            {options.verify && " The result is checked before the request ends."}
          </p>
        </div>
      )}
    </div>
  );
}

/** The `verification` card: counts first, findings expandable, failed ones first. */
export function VerificationCard({ item }: { item: VerificationItem }) {
  const [open, setOpen] = useState(item.failed > 0);
  const tone = verificationTone(item);
  return (
    <section className={`verification verification-${tone}`} aria-label="Check of the page" tabIndex={0}>
      <p className="verification-title">
        <strong>Check of the page</strong> {verificationHeadline(item)}
      </p>
      {item.summary && <p className="verification-summary">{item.summary}</p>}
      {item.findings.length > 0 && (
        <button type="button" className="verification-toggle" aria-expanded={open} onClick={() => setOpen(!open)}>
          {open ? "Hide the details" : `Show the details (${item.findings.length})`}
        </button>
      )}
      {open && (
        <ul className="verification-findings">
          {item.findings.map((finding, index) => (
            <li key={index} className={`finding finding-${finding.status}`}>
              <span className="finding-status">{STATUS_LABEL[finding.status]}</span>{" "}
              <span className="finding-what">{finding.what}</span>
              {finding.where && <span className="finding-where"> — {finding.where}</span>}
              {finding.note && <span className="finding-note">{finding.note}</span>}
            </li>
          ))}
          {item.cut > 0 && <li className="finding-cut">{item.cut} more were reported and not kept.</li>}
        </ul>
      )}
      <p className="verification-means">{MATCH_MEANS}</p>
    </section>
  );
}
