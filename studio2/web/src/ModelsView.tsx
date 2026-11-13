// The Library's "Models": every connection and model, the result of "Check this model" beside
// each, the studio's own LiteLLM, and what is said about models that are not Claude's.

import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, errorMessage } from "./api";
import { Icon, Spinner } from "./icons";
import {
  checkChip,
  checkFacts,
  costWords,
  hasOtherModels,
  isRunning,
  modelsApi,
  showsLitellm,
  type CheckedModel,
  type ModelsConnection,
  type ModelsInfo,
  pickingLine,
} from "./models";
import { setupApi, type LitellmStatus, type OtherModelsNote } from "./setup";
import { useConfirm, useToast } from "./ui";

/** Said wherever a model that is not Claude's is chosen: the headline and the four limits. */
export function OtherModelsNotice({ note }: { note: OtherModelsNote }) {
  return (
    <div className="notice notice-warning other-models" role="note">
      <Icon name="warning" size={14} />
      <div>
        <strong>{note.headline}</strong>
        <ul>
          {note.limits.map((limit) => (
            <li key={limit}>{limit}</li>
          ))}
        </ul>
      </div>
    </div>
  );
}

/** A server sentence with its `commands` set in code. */
function Ticks({ text }: { text: string }) {
  return <>{text.split("`").map((part, index) => (index % 2 === 1 ? <code key={index}>{part}</code> : part))}</>;
}

/**
 * The studio's own LiteLLM: whether it is installed (with the button that installs it, which is
 * the person's yes), running, or stopped by itself. `onChanged` is called when an install ends.
 */
export function LitellmNotice({
  status,
  onInstall,
  quiet = false,
}: {
  status: Pick<LitellmStatus, "installed" | "version" | "size" | "install_command"> & Partial<LitellmStatus>;
  onInstall: () => void;
  /** Say nothing when it is installed and well (setup); the Library shows its state. */
  quiet?: boolean;
}) {
  if (status.installing) {
    return (
      <p className="notice litellm-notice">
        <Spinner size={14} />
        <span>
          Installing LiteLLM {status.pinned ?? status.version} ({status.size}). Every package is checked against a
          known hash; this takes a minute or two.
        </span>
      </p>
    );
  }
  if (!status.installed) {
    return (
      <div className="notice notice-warning litellm-notice">
        <Icon name="warning" size={14} />
        <div>
          <span>
            These accounts run through LiteLLM, which the studio starts beside itself on this computer only. It is
            not installed yet: version {status.pinned ?? status.version}, {status.size}, every package checked
            against a hash kept in the studio’s own files.
          </span>
          {status.install_error && <span className="litellm-error">{status.install_error}</span>}
          <span className="litellm-actions">
            <button type="button" className="btn btn-small" onClick={onInstall}>
              Install LiteLLM
            </button>
            <span>
              or in a terminal: <code>{status.install_command}</code>
            </span>
          </span>
        </div>
      </div>
    );
  }
  if (quiet) return null;
  const stopped = status.state === "stopped";
  return (
    <p className={`notice litellm-notice${stopped ? " notice-warning" : ""}`}>
      <Icon name={stopped ? "warning" : "info"} size={14} />
      <span>
        <strong>The studio’s own LiteLLM.</strong> {status.detail ? <Ticks text={status.detail} /> : `Version ${status.version}.`}
      </span>
    </p>
  );
}

function ModelRow({
  connection,
  model,
  info,
  busy,
  onCheck,
  onStop,
}: {
  connection: ModelsConnection;
  model: CheckedModel;
  info: ModelsInfo;
  busy: boolean;
  onCheck: () => void;
  onStop: () => void;
}) {
  const [open, setOpen] = useState(false);
  const running = isRunning(info, connection.id, model.id);
  const result = model.check;
  const chip = checkChip(result);
  const cost = costWords(model.cost);
  const detailsId = `check-${connection.id}-${model.id}`.replace(/[^A-Za-z0-9_-]/g, "-");
  return (
    <li className="lib-row models-row">
      <div className="lib-row-main">
        <span className="lib-row-title models-name">
          <span className="models-id">{model.label}</span>
          {model.experimental && <span className="badge badge-experimental">Experimental</span>}
          {model.id === connection.default_model && connection.models.length > 1 && <span className="badge">Default</span>}
          {model.id === connection.small_model && <span className="badge">Background work</span>}
        </span>
        <span className="lib-row-detail">
          {running ? (
            <span className="models-running">
              <Spinner size={12} /> Checking. {info.running?.stopping ? "Stopping…" : info.running?.phase}
            </span>
          ) : result ? (
            checkFacts(result)
          ) : (
            <>Not checked yet{cost ? ` · ${cost}` : ""}</>
          )}
        </span>
        {!running && result && result.status !== "passed" && result.reason && (
          <span className="lib-row-note models-reason">{result.reason}</span>
        )}
        {!running && result && (
          <button
            type="button"
            className="lib-more models-more"
            aria-expanded={open}
            aria-controls={detailsId}
            onClick={() => setOpen((value) => !value)}
          >
            {open ? "Hide what was checked" : "What was checked"}
          </button>
        )}
        {!running && result && open && (
          <div className="models-details" id={detailsId}>
            <ul className="check-parts">
              {result.parts.map((part) => (
                <li key={part.id} className={part.ok ? "is-ok" : "is-off"}>
                  <Icon name={part.ok ? "check" : "x"} size={13} strokeWidth={2.4} />
                  <span>
                    {part.label}
                    {part.detail && <span className="check-detail"> {part.detail}</span>}
                  </span>
                </li>
              ))}
            </ul>
            {result.notes.map((note) => (
              <p key={note} className="field-hint">
                {note}
              </p>
            ))}
            {result.trace.length > 0 && (
              <p className="field-hint">
                What it did: {result.trace.map((step) => step.title || step.name).join(" → ")}
              </p>
            )}
          </div>
        )}
      </div>
      <div className="models-actions">
        <span className={`lib-status is-${chip.tone}`}>{running ? "Checking" : chip.text}</span>
        {running ? (
          <button type="button" className="btn btn-small" onClick={onStop} disabled={info.running?.stopping === true}>
            Stop
          </button>
        ) : (
          <button
            type="button"
            className="btn btn-small"
            onClick={onCheck}
            disabled={busy || !connection.available}
            aria-label={`${result ? "Check again" : "Check"} ${model.label}`}
          >
            {result ? "Check again" : "Check"}
          </button>
        )}
      </div>
    </li>
  );
}

export function ModelsView() {
  const [info, setInfo] = useState<ModelsInfo | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const toast = useToast();
  const confirm = useConfirm();
  const alive = useRef(true);

  const load = useCallback(() => {
    modelsApi.get().then(
      (result) => {
        if (alive.current) {
          setInfo(result);
          setError(null);
        }
      },
      (cause) => {
        if (alive.current) setError(errorMessage(cause));
      },
    );
  }, []);

  useEffect(() => {
    alive.current = true;
    load();
    return () => {
      alive.current = false;
    };
  }, [load]);

  // While a check or an install runs, ask again every second and a half.
  const active = info?.running != null || info?.litellm.installing === true;
  useEffect(() => {
    if (!active) return;
    const timer = window.setInterval(load, 1500);
    return () => window.clearInterval(timer);
  }, [active, load]);

  const check = async (connection: ModelsConnection, model: CheckedModel) => {
    const ok = await confirm({
      title: `Check ${model.label}?`,
      body: (
        <div className="check-confirm">
          <p>
            The studio runs a fixed request through the assistant on a scratch page: two files edited in place, two
            tool calls in one step, one of the studio’s own tools, and a second message that continues the first.
            It usually takes under two minutes.
          </p>
          <p>{model.warning ?? "It costs nothing on this model."}</p>
        </div>
      ),
      confirmLabel: model.warning && model.cost !== "plan" ? "Run it, at my cost" : "Run the check",
    });
    if (!ok) return;
    setStarting(true);
    try {
      await modelsApi.check(connection.id, model.id, true);
      load();
    } catch (cause) {
      toast({ message: cause instanceof ApiError ? cause.message : errorMessage(cause), kind: "error" });
    } finally {
      setStarting(false);
    }
  };

  const stop = () => {
    modelsApi.stop().then(load, (cause) => toast({ message: errorMessage(cause), kind: "error" }));
  };

  const install = () => {
    setupApi.installLitellm().then(load, (cause) => toast({ message: errorMessage(cause), kind: "error" }));
  };

  if (error && !info) {
    return (
      <div className="lib-error" role="alert">
        <Icon name="alert" size={15} />
        <span>The models couldn’t be loaded: {error}</span>
        <button type="button" className="btn btn-small" onClick={load}>
          Try again
        </button>
      </div>
    );
  }
  if (!info) {
    return (
      <div className="panel-empty">
        <Spinner />
      </div>
    );
  }

  const busy = starting || info.running !== null;
  return (
    <div className="models">
      <p className="lib-intro">
        “Check” runs a fixed request on a model and keeps what happened: {info.parts.map((part) => part.toLowerCase()).join("; ")}.
      </p>
      {hasOtherModels(info) && <OtherModelsNotice note={info.note} />}
      {showsLitellm(info.litellm) && <LitellmNotice status={info.litellm} onInstall={install} />}
      {info.connections.map((connection) => (
        <section key={connection.id} className="models-connection" aria-label={connection.label}>
          <div className="lib-section-head">
            <h3 className="lib-title">
              {connection.label}
              <span className="lib-count">{connection.models.length}</span>
            </h3>
            <span className={`lib-status ${connection.available ? "is-ok" : "is-off"}`}>
              {connection.available ? "Available" : "Not available"}
            </span>
          </div>
          {!connection.available && connection.reason && (
            <p className="lib-row-note models-unavailable">
              <Ticks text={connection.reason} />
            </p>
          )}
          {pickingLine(connection) && <p className="lib-row-note models-picking">{pickingLine(connection)}</p>}
          <ul className="lib-rows">
            {connection.models.map((model) => (
              <ModelRow
                key={model.id}
                connection={connection}
                model={model}
                info={info}
                busy={busy}
                onCheck={() => void check(connection, model)}
                onStop={stop}
              />
            ))}
          </ul>
        </section>
      ))}
    </div>
  );
}
