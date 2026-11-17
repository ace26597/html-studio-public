// The project's own app (a `studio.json` in the workspace): a status pill in
// the top bar whose menu starts, stops and restarts it and opens its logs.

import type { AppAction } from "./api";
import { appStateLabel, truncate } from "./format";
import { Icon, Spinner } from "./icons";
import type { AppStatus } from "./thread";
import type { AppInfo } from "./types";
import { Menu, type MenuEntry } from "./ui";

interface AppControlProps {
  /** Latest state from events (seeded from `GET …/app`). */
  app: AppStatus | null;
  /** The last `GET …/app` answer: carries `error` for an invalid studio.json. */
  info: AppInfo | null;
  /** Why `GET …/app` failed, if it did. */
  error: string | null;
  projectSaysConfigured: boolean;
  busy: AppAction | null;
  logsOpen: boolean;
  onAction: (action: AppAction) => void;
  onShowLogs: () => void;
  onRetry: () => void;
}

const BUSY_WORD: Record<AppAction, string> = { start: "Starting", stop: "Stopping", restart: "Restarting" };

export function AppControl({
  app,
  info,
  error,
  projectSaysConfigured,
  busy,
  logsOpen,
  onAction,
  onShowLogs,
  onRetry,
}: AppControlProps) {
  const configured = app?.configured ?? info?.configured ?? false;

  // studio.json exists but cannot be used: say why instead of showing nothing.
  if (!configured && info?.error) {
    return (
      <Menu
        label="studio.json"
        align="end"
        entries={[
          { type: "section", label: "studio.json can’t be used", note: info.error },
          { label: "Check again", icon: "refresh", onSelect: onRetry },
        ]}
        trigger={(props) => (
          <button
            type="button"
            className="app-pill is-invalid"
            aria-label={`The app is not set up: ${info.error}`}
            data-tip={info.error ?? undefined}
            data-tip-align="end"
            {...props}
          >
            <Icon name="warning" size={13} />
            <span>studio.json problem</span>
          </button>
        )}
      />
    );
  }

  // The project record says there is an app, but its status could not be read.
  if (!configured && !app && error && projectSaysConfigured) {
    return (
      <Menu
        label="App"
        align="end"
        entries={[
          { type: "section", label: "App status unavailable", note: error },
          { label: "Try again", icon: "refresh", onSelect: onRetry },
        ]}
        trigger={(props) => (
          <button
            type="button"
            className="app-pill is-invalid"
            aria-label={`App status unavailable: ${error}`}
            data-tip-align="end"
            {...props}
          >
            <Icon name="alert" size={13} />
            <span>App status unavailable</span>
          </button>
        )}
      />
    );
  }

  if (!configured || !app) return null;

  const { label: stateLabel, tone } = appStateLabel(app);
  const run = app.run ?? info?.run ?? null;
  const live = app.state === "running" || app.state === "starting";
  const external = app.state === "external";
  const starting = app.state === "starting";
  const label =
    starting && info?.installing
      ? "Installing packages"
      : starting && info?.not_answering
        ? "Not answering"
        : starting && info?.slow
          ? "Still starting"
          : stateLabel;
  const entries: MenuEntry[] = external
    ? [
        { type: "section", label: "A server the studio did not start", note: info?.address ?? null },
        { type: "section", label: "The studio shows it and never starts, stops or restarts it." },
      ]
    : [
    { type: "section", label: "App", note: run ? truncate(run, 80) : null },
    {
      label: "Start",
      icon: "play",
      disabled: busy !== null || live,
      onSelect: () => onAction("start"),
    },
    {
      label: "Stop",
      icon: "stop",
      disabled: busy !== null || !live,
      onSelect: () => onAction("stop"),
    },
    {
      label: "Restart",
      icon: "refresh",
      disabled: busy !== null,
      onSelect: () => onAction("restart"),
    },
    { type: "separator" },
    { label: logsOpen ? "Logs are open" : "Show logs", icon: "terminal", disabled: logsOpen, onSelect: onShowLogs },
  ];

  const shown = busy ? `${BUSY_WORD[busy]}…` : label;
  return (
    <Menu
      label="App"
      align="end"
      entries={entries}
      trigger={(props) => (
        <button
          type="button"
          className={`app-pill is-${tone}`}
          aria-label={external ? `App: ${shown}, at ${info?.address ?? "its own address"}` : `App: ${shown}. Start, stop, restart or show logs`}
          data-tip={external ? `Your server: ${info?.address ?? ""}` : run ? `App: ${truncate(run, 60)}` : "App"}
          data-tip-align="end"
          {...props}
        >
          {busy || app.state === "starting" ? (
            <Spinner size={11} />
          ) : (
            <span className="app-dot" aria-hidden="true" />
          )}
          <span>{shown}</span>
          <Icon name="chevron-down" size={12} strokeWidth={2} />
        </button>
      )}
    />
  );
}
