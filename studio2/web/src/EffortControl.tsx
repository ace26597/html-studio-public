import { useMemo, useState } from "react";
import { effortLabel, effortSummary } from "./effortRow";
import { Icon } from "./icons";
import { Menu, type MenuEntry } from "./ui";
import {
  DEPTHS,
  MATCH_MEANS,
  STATUS_LABEL,
  verificationHeadline,
  verificationTone,
  type TurnOptions,
  type VerificationItem,
} from "./effort";

/**
 * How much to do, in the composer's bar beside the model: a quiet button that names the depth
 * (with a tick when the result will be checked) and opens a menu with the three steps, the
 * check switch and "Check this page now". Nothing is added to the composer's height.
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
  const entries = useMemo<MenuEntry[]>(
    () => [
      { type: "section", label: "How much to do" },
      ...DEPTHS.map(
        (step): MenuEntry => ({
          label: step.label,
          hint: step.short,
          checked: options.depth === step.id,
          onSelect: () => step.id !== options.depth && onChange({ ...options, depth: step.id }),
        }),
      ),
      { type: "separator" },
      {
        label: "Check the result",
        hint: options.verify ? "on" : "off",
        checked: options.verify,
        toggle: true,
        onSelect: () => onChange({ ...options, verify: !options.verify }),
      },
      { type: "separator" },
      { label: "Check this page now", icon: "shield", disabled, onSelect: onVerifyPage },
    ],
    [options, onChange, onVerifyPage, disabled],
  );
  return (
    <Menu
      label="How much to do"
      side="top"
      align="start"
      entries={entries}
      trigger={(props) => (
        <button
          type="button"
          className="model-btn effort-btn"
          aria-label={effortSummary(options)}
          data-tip="How much to do, and whether the result is checked"
          data-tip-side="top"
          data-tip-align="start"
          {...props}
        >
          <span>{effortLabel(options)}</span>
          {options.verify && <Icon name="check" size={12} strokeWidth={2.4} className="effort-btn-check" />}
          <Icon name="chevron-down" size={12} strokeWidth={2} />
        </button>
      )}
    />
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
