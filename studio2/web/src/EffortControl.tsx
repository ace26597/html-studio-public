import { type ButtonHTMLAttributes, type CSSProperties, useEffect, useMemo, useState } from "react";
import { VERIFY_OFF_TIP, VERIFY_ON_TIP, effortLabel } from "./effortRow";
import { Icon } from "./icons";
import { INVOLVE_STOPS, involveStop } from "./questions";
import { Menu, type MenuEntry } from "./ui";
import {
  DEPTHS,
  depthLine,
  MATCH_MEANS,
  STATUS_LABEL,
  verificationHeadline,
  verificationTone,
  type TurnOptions,
  type VerificationItem,
} from "./effort";

/** One setting in the strip under the composer: what it is, and what it is set to. */
export function OptionButton({
  caption,
  value,
  tip,
  on,
  chevron = true,
  ...rest
}: {
  caption: string;
  value: string;
  tip: string;
  /** Marks a setting that is switched on (and, without `chevron`, makes the button a switch). */
  on?: boolean;
  chevron?: boolean;
} & ButtonHTMLAttributes<HTMLButtonElement>) {
  return (
    <button
      type="button"
      className={`opt-btn${on ? " is-on" : ""}`}
      aria-label={`${caption}: ${value}`}
      data-tip={tip}
      data-tip-side="top"
      data-tip-align="start"
      {...rest}
    >
      <span className="opt-k">{caption}</span>
      <span className="opt-v">{value}</span>
      {chevron && <Icon name="chevron-down" size={11} strokeWidth={2} />}
    </button>
  );
}

/** How much a request does: Quick, Standard or Deep. */
export function DepthPicker({ options, onChange }: { options: TurnOptions; onChange: (next: TurnOptions) => void }) {
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
    ],
    [options, onChange],
  );
  return (
    <Menu
      label="How much to do"
      side="top"
      align="start"
      entries={entries}
      trigger={(props) => <OptionButton caption="Depth" value={effortLabel(options)} tip={depthLine(options.depth)} {...props} />}
    />
  );
}

/** Whether the content is checked after each request: a switch, one click. */
export function VerifyToggle({ options, onChange }: { options: TurnOptions; onChange: (next: TurnOptions) => void }) {
  return (
    <OptionButton
      caption="Verify"
      value={options.verify ? "on" : "off"}
      tip={options.verify ? VERIFY_ON_TIP : VERIFY_OFF_TIP}
      on={options.verify}
      chevron={false}
      aria-label="Verify the content after each request"
      aria-pressed={options.verify}
      onClick={() => onChange({ ...options, verify: !options.verify })}
    />
  );
}

/** How much the agent involves the person: the slider. Shown only when the server knows the scale. */
export function InvolvePicker({ options, onChange }: { options: TurnOptions; onChange: (next: TurnOptions) => void }) {
  const entries = useMemo<MenuEntry[]>(
    () => [
      {
        type: "custom",
        render: () => <InvolveSlider value={options.involve ?? 0} onChange={(involve) => onChange({ ...options, involve })} />,
      },
    ],
    [options, onChange],
  );
  if (options.involve === undefined) return null;
  const stop = involveStop(options.involve);
  return (
    <Menu
      label="How much to involve you"
      side="top"
      align="start"
      entries={entries}
      trigger={(props) => (
        <OptionButton caption="Involve" value={`${stop.value}%`} tip={`${stop.name}: ${stop.line}`} on={stop.value > 0} {...props} />
      )}
    />
  );
}

/** Things to do to the page now, beside the other actions in the composer: verify its content, polish its look. */
export function PageChecks({
  onVerifyPage,
  onPolishPage,
  disabled,
}: {
  onVerifyPage: () => void;
  onPolishPage: () => void;
  disabled?: boolean;
}) {
  const entries = useMemo<MenuEntry[]>(
    () => [
      { type: "section", label: "Check this page now" },
      { label: "Verify content", hint: "figures against sources", icon: "shield", disabled, onSelect: onVerifyPage },
      { label: "Polish the look", hint: "desktop, tablet, phone", icon: "sparkle", disabled, onSelect: onPolishPage },
    ],
    [onVerifyPage, onPolishPage, disabled],
  );
  return (
    <Menu
      label="Check this page now"
      side="top"
      align="start"
      entries={entries}
      trigger={(props) => (
        <button
          type="button"
          className="icon-btn attach-btn page-checks-btn"
          aria-label="Check this page now"
          data-tip="Check this page now: verify its content or polish its look"
          data-tip-side="top"
          data-tip-align="start"
          {...props}
        >
          <Icon name="sparkle" size={16} />
        </button>
      )}
    />
  );
}

/**
 * "Involve me": a slider of five stops, from the agent working on its own to checking in after
 * each part. It names the stop it is on and says what the agent will do there.
 */
export function InvolveSlider({ value, onChange }: { value: number; onChange: (next: number) => void }) {
  // What the thumb shows while the choice is on its way to the server.
  const [shown, setShown] = useState(value);
  useEffect(() => setShown(value), [value]);
  const stop = involveStop(shown);
  return (
    <div className="involve">
      <div className="involve-head">
        <label htmlFor="involve-range">Involve me</label>
        <span className="involve-value">
          {stop.name} · {stop.value}%
        </span>
      </div>
      <input
        id="involve-range"
        className="involve-range"
        type="range"
        min={0}
        max={100}
        step={25}
        value={stop.value}
        data-menu-focus
        aria-valuetext={`${stop.name}, ${stop.value} percent`}
        style={{ "--involve": `${stop.value}%` } as CSSProperties}
        onChange={(event) => {
          const next = involveStop(Number(event.target.value)).value;
          setShown(next);
          if (next !== value) onChange(next);
        }}
      />
      <div className="involve-ticks" aria-hidden="true">
        {INVOLVE_STOPS.map((each) => (
          <span key={each.value} className={each.value === stop.value ? "is-on" : undefined} />
        ))}
      </div>
      <div className="involve-ends" aria-hidden="true">
        <span>On its own</span>
        <span>Step by step</span>
      </div>
      <p className="involve-line" aria-live="polite">
        {stop.line}
      </p>
    </div>
  );
}

/** The `verification` card: counts first, findings expandable, failed ones first. */
export function VerificationCard({ item }: { item: VerificationItem }) {
  const [open, setOpen] = useState(item.failed > 0);
  const tone = verificationTone(item);
  return (
    <section className={`verification verification-${tone}`} aria-label="Content check" tabIndex={0}>
      <p className="verification-title">
        <strong>Content check</strong> {verificationHeadline(item)}
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
