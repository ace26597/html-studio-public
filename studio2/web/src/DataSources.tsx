// Data sources: the person's own databases, saved in the studio (not in a project).
// The password is write-only here: the browser can set, replace or remove it, never read it.

import { useCallback, useEffect, useId, useState } from "react";
import { api, errorMessage } from "./api";
import {
  CA_CHANGED_NOTE,
  changeKind,
  emptyForm,
  formFromSource,
  inputFromForm,
  needsPasswordAgain,
  patchFromForm,
  SSL_HELP,
  sslWarning,
  SSL_MODES,
  testMode,
  validateForm,
  type DataSourceForm,
  type FieldError,
} from "./dataSourceForm";
import { relativeTime } from "./format";
import { Icon, Spinner } from "./icons";
import { Modal } from "./Modal";
import type { DataKindInfo, DataSource, DataSourceTestResult } from "./types";
import { useConfirm, useToast } from "./ui";

export const KEYCHAIN_NOTE = "Stored in your Mac's Keychain, not in project files. The assistant never sees it.";

/** The contract's three kinds, named, for when the library could not be loaded (availability then unknown). */
const KNOWN_KINDS: DataKindInfo[] = [
  { kind: "postgres", label: "PostgreSQL", default_port: 5432, available: true, note: null },
  { kind: "redshift", label: "Amazon Redshift", default_port: 5439, available: true, note: null },
  { kind: "mysql", label: "MySQL", default_port: 3306, available: true, note: null },
];

export function kindsOrKnown(kinds: DataKindInfo[] | null | undefined): DataKindInfo[] {
  return kinds && kinds.length ? kinds : KNOWN_KINDS;
}

export function kindLabel(kind: string, kinds: DataKindInfo[]): string {
  return kinds.find((item) => item.kind === kind)?.label ?? kind;
}

export function sourceAddress(source: DataSource): string {
  return `${source.user}@${source.host}:${source.port}/${source.database}`;
}

export function TestResult({ result }: { result: DataSourceTestResult }) {
  const extra: string[] = [];
  if (result.server_version) extra.push(result.server_version);
  if (typeof result.tables === "number") extra.push(`${result.tables} ${result.tables === 1 ? "table" : "tables"} visible`);
  return (
    <p className={`ds-test ${result.ok ? "is-ok" : "is-fail"}`} role={result.ok ? "status" : "alert"}>
      <Icon name={result.ok ? "check" : "alert"} size={14} strokeWidth={2} />
      <span>
        <strong>{result.ok ? "Connected" : "Could not connect"}</strong>
        {result.message ? ` · ${result.message}` : ""}
        {extra.length > 0 && ` · ${extra.join(" · ")}`}
      </span>
    </p>
  );
}

function LastTest({ source, now }: { source: DataSource; now: number }) {
  if (!source.last_test) return <span className="ds-last is-none">Not tested yet</span>;
  const when = relativeTime(source.last_test.at, now);
  return (
    <span className={`ds-last ${source.last_test.ok ? "is-ok" : "is-fail"}`} title={source.last_test.message}>
      {source.last_test.ok ? `Connected ${when}` : `Failed ${when}: ${source.last_test.message}`}
    </span>
  );
}

// ---- the encryption choice --------------------------------------------------------------

interface SslChoiceProps {
  id: string;
  kind: string;
  ssl: string;
  caFile: string;
  invalid?: boolean;
  describedBy?: string;
  onChange: (patch: { ssl?: string; caFile?: string }) => void;
}

/** The four TLS modes, each with its sentence, and the CA file under verify-full. */
export function SslChoice({ id, kind, ssl, caFile, invalid, describedBy, onChange }: SslChoiceProps) {
  const warning = sslWarning(kind, ssl);
  return (
    <fieldset className="field ds-span-2 ds-ssl">
      <legend>Encryption (SSL)</legend>
      {SSL_MODES.map((mode) => (
        <label key={mode} className={`save-choice ds-ssl-choice${ssl === mode ? " is-on" : ""}`}>
          <input
            type="radio"
            name={`${id}-mode`}
            value={mode}
            checked={ssl === mode}
            aria-describedby={`${id}-${mode}-help`}
            onChange={() => onChange({ ssl: mode })}
          />
          <span className="ds-ssl-text">
            <code>{mode}</code>
            <span id={`${id}-${mode}-help`}>{SSL_HELP[mode]}</span>
          </span>
        </label>
      ))}
      {warning && (
        <p className="ds-ssl-hint ds-ssl-warning" role="note">
          {warning}
        </p>
      )}
      {ssl === "verify-full" && (
        <label className="field ds-ssl-ca" htmlFor={`${id}-ca`}>
          <span>CA file (optional)</span>
          <input
            id={`${id}-ca`}
            value={caFile}
            autoComplete="off"
            spellCheck={false}
            placeholder="/path/to/ca.pem"
            aria-invalid={invalid}
            aria-describedby={describedBy ?? `${id}-ca-help`}
            onChange={(event) => onChange({ caFile: event.target.value })}
          />
          <span className="ds-ssl-hint" id={`${id}-ca-help`}>
            For a server whose certificate is not from a public authority: the full path of the PEM file, on the
            computer the studio runs on, that signed it (your own, or the bundle your cloud provider publishes). Leave
            empty to trust only public authorities.
          </span>
        </label>
      )}
    </fieldset>
  );
}

// ---- add / edit dialog --------------------------------------------------------------

interface DataSourceDialogProps {
  kinds: DataKindInfo[];
  /** The source being edited, or null to add one. */
  source: DataSource | null;
  /** Open with the password field ready: the CA file changed since the password was saved. */
  retypePassword?: boolean;
  onSaved: (source: DataSource) => void;
  onClose: () => void;
}

export function DataSourceDialog({ kinds, source, retypePassword = false, onSaved, onClose }: DataSourceDialogProps) {
  const [form, setForm] = useState<DataSourceForm>(() =>
    source ? formFromSource(source, { retypePassword }) : emptyForm(kinds),
  );
  const [fieldError, setFieldError] = useState<FieldError | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [test, setTest] = useState<DataSourceTestResult | null>(null);
  const ids = useId();
  const id = (field: string) => `${ids}-${field}`;

  const update = (patch: Partial<DataSourceForm>) => {
    setForm((current) => ({ ...current, ...patch }));
    setTest(null);
    setFieldError(null);
  };

  const mode = testMode(form, source);

  const runTest = async () => {
    const problem = validateForm(form);
    if (problem) {
      setFieldError(problem);
      return;
    }
    setTesting(true);
    setTest(null);
    setError(null);
    try {
      setTest(
        mode === "saved" && source
          ? await api.testDataSource(source.id)
          : await api.testDataSourceDraft(inputFromForm(form)),
      );
    } catch (cause) {
      setError(errorMessage(cause));
    } finally {
      setTesting(false);
    }
  };

  const save = async () => {
    const problem = validateForm(form);
    if (problem) {
      setFieldError(problem);
      return;
    }
    setSaving(true);
    setError(null);
    try {
      if (source) {
        const patch = patchFromForm(form, source);
        onSaved(Object.keys(patch).length ? await api.patchDataSource(source.id, patch) : source);
      } else {
        onSaved(await api.createDataSource(inputFromForm(form)));
      }
    } catch (cause) {
      setError(errorMessage(cause));
      setSaving(false);
    }
  };

  const invalid = (field: FieldError["field"]) => (fieldError?.field === field ? true : undefined);
  const describe = (field: FieldError["field"]) => (fieldError?.field === field ? id("field-error") : undefined);
  const chosenKind = kinds.find((item) => item.kind === form.kind);

  return (
    <Modal title={source ? `Edit ${source.name}` : "Add a data source"} onClose={onClose} busy={saving} wide>
      <form
        className="ds-form"
        onSubmit={(event) => {
          event.preventDefault();
          void save();
        }}
      >
        <div className="ds-grid">
          <label className="field" htmlFor={id("name")}>
            <span>Name</span>
            <input
              id={id("name")}
              data-autofocus={retypePassword ? undefined : true}
              value={form.name}
              maxLength={60}
              aria-invalid={invalid("name")}
              aria-describedby={describe("name")}
              onChange={(event) => update({ name: event.target.value })}
            />
          </label>
          <label className="field" htmlFor={id("kind")}>
            <span>Type</span>
            <select
              id={id("kind")}
              value={form.kind}
              onChange={(event) => setForm((current) => changeKind(current, event.target.value, kinds))}
            >
              {kinds.map((kind) => (
                <option key={kind.kind} value={kind.kind}>
                  {kind.label}
                  {kind.available ? "" : " (driver not installed)"}
                </option>
              ))}
            </select>
          </label>
          <label className="field" htmlFor={id("host")}>
            <span>Host</span>
            <input
              id={id("host")}
              value={form.host}
              autoComplete="off"
              spellCheck={false}
              aria-invalid={invalid("host")}
              aria-describedby={describe("host")}
              onChange={(event) => update({ host: event.target.value })}
            />
          </label>
          <label className="field" htmlFor={id("port")}>
            <span>Port</span>
            <input
              id={id("port")}
              value={form.port}
              inputMode="numeric"
              aria-invalid={invalid("port")}
              aria-describedby={describe("port")}
              onChange={(event) => update({ port: event.target.value })}
            />
          </label>
          <label className="field" htmlFor={id("database")}>
            <span>Database</span>
            <input
              id={id("database")}
              value={form.database}
              autoComplete="off"
              spellCheck={false}
              aria-invalid={invalid("database")}
              aria-describedby={describe("database")}
              onChange={(event) => update({ database: event.target.value })}
            />
          </label>
          <label className="field" htmlFor={id("user")}>
            <span>User</span>
            <input
              id={id("user")}
              value={form.user}
              autoComplete="off"
              spellCheck={false}
              aria-invalid={invalid("user")}
              aria-describedby={describe("user")}
              onChange={(event) => update({ user: event.target.value })}
            />
          </label>

          <SslChoice
            id={id("ssl")}
            kind={form.kind}
            ssl={form.ssl}
            caFile={form.caFile}
            invalid={invalid("caFile")}
            describedBy={describe("caFile")}
            onChange={update}
          />

          <div className="field ds-span-2">
            <span id={id("pw-label")}>Password</span>
            {retypePassword && <p className="ds-note is-warn">{CA_CHANGED_NOTE}</p>}
            {form.passwordAction === "keep" && (
              <div className="ds-password-row">
                <span className="ds-password-state">A password is stored.</span>
                <button type="button" className="btn btn-small" onClick={() => update({ passwordAction: "set", password: "" })}>
                  Replace
                </button>
                <button type="button" className="btn btn-small" onClick={() => update({ passwordAction: "remove", password: "" })}>
                  Remove
                </button>
              </div>
            )}
            {form.passwordAction === "remove" && (
              <div className="ds-password-row">
                <span className="ds-password-state is-warn">The stored password is removed when you save.</span>
                <button type="button" className="btn btn-small" onClick={() => update({ passwordAction: "keep" })}>
                  Keep it
                </button>
              </div>
            )}
            {(form.passwordAction === "set" || form.passwordAction === "none") && (
              <div className="ds-password-row">
                <input
                  id={id("password")}
                  type="password"
                  data-autofocus={retypePassword ? "" : undefined}
                  aria-labelledby={id("pw-label")}
                  autoComplete="new-password"
                  value={form.password}
                  placeholder={source?.has_password ? "New password" : "Optional"}
                  aria-invalid={invalid("password")}
                  aria-describedby={describe("password") ?? id("keychain")}
                  onChange={(event) =>
                    update({
                      password: event.target.value,
                      // Typing sets it; clearing an optional field means "no password".
                      passwordAction: event.target.value || source?.has_password ? "set" : "none",
                    })
                  }
                />
                {source?.has_password && (
                  <button type="button" className="btn btn-small" onClick={() => update({ passwordAction: "keep", password: "" })}>
                    Keep the stored one
                  </button>
                )}
              </div>
            )}
            <span className="ds-keychain" id={id("keychain")}>
              <Icon name="shield" size={12} /> {KEYCHAIN_NOTE}
            </span>
          </div>
        </div>

        {chosenKind && !chosenKind.available && (
          <p className="ds-note is-warn">
            The {chosenKind.label} driver is not installed on this Mac{chosenKind.note ? `: ${chosenKind.note}` : ""}. You can
            save the source, but it can’t connect until the driver is installed.
          </p>
        )}
        {fieldError && (
          <p className="ds-field-error" id={id("field-error")} role="alert">
            {fieldError.message}
          </p>
        )}
        {mode === "draft-without-stored-password" && !test && (
          <p className="ds-note">
            Testing unsaved changes sends only what is in this form, and the stored password can’t be sent back from
            the browser. To include a password, choose Replace and type it, or save first and test from the list.
          </p>
        )}
        {test && <TestResult result={test} />}
        {error && (
          <p className="ds-error" role="alert">
            <Icon name="alert" size={14} /> {error}
          </p>
        )}

        <div className="dialog-actions">
          <button type="button" className="btn ds-test-btn" disabled={testing || saving} onClick={() => void runTest()}>
            {testing ? <Spinner size={13} /> : <Icon name="refresh" size={14} />}
            {testing ? "Testing…" : mode === "saved" ? "Test connection" : "Test before saving"}
          </button>
          <span className="composer-spacer" />
          <button type="button" className="btn" disabled={saving} onClick={onClose}>
            Cancel
          </button>
          <button type="submit" className="btn btn-primary" disabled={saving}>
            {saving && <Spinner size={13} />}
            {source ? "Save" : "Add"}
          </button>
        </div>
      </form>
    </Modal>
  );
}

// ---- the list ---------------------------------------------------------------------------

interface DataSourcesManagerProps {
  kinds: DataKindInfo[];
}

export function DataSourcesManager({ kinds }: DataSourcesManagerProps) {
  const toast = useToast();
  const confirm = useConfirm();
  const [sources, setSources] = useState<DataSource[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);
  const [editing, setEditing] = useState<DataSource | "new" | null>(null);
  const [retype, setRetype] = useState(false);
  const [tests, setTests] = useState<Record<string, { busy: boolean; result: DataSourceTestResult | null; error: string | null }>>({});
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    const controller = new AbortController();
    setError(null);
    api.listDataSources(controller.signal).then(
      (list) => {
        setSources(list);
        setNow(Date.now());
      },
      (cause) => {
        if ((cause as Error)?.name !== "AbortError") setError(errorMessage(cause));
      },
    );
    return () => controller.abort();
  }, [attempt]);

  const refresh = useCallback(() => setAttempt((n) => n + 1), []);

  const runTest = async (source: DataSource) => {
    setTests((map) => ({ ...map, [source.id]: { busy: true, result: null, error: null } }));
    try {
      const result = await api.testDataSource(source.id);
      setTests((map) => ({ ...map, [source.id]: { busy: false, result, error: null } }));
      refresh(); // last_test changed on the server
    } catch (cause) {
      setTests((map) => ({ ...map, [source.id]: { busy: false, result: null, error: errorMessage(cause) } }));
    }
  };

  const remove = async (source: DataSource) => {
    const ok = await confirm({
      title: `Delete “${source.name}”?`,
      body: "It is detached from every project that uses it, and its stored password is deleted from the Keychain. The database itself is not touched.",
      confirmLabel: "Delete",
      danger: true,
    });
    if (!ok) return;
    try {
      await api.deleteDataSource(source.id);
      setSources((list) => list?.filter((item) => item.id !== source.id) ?? list);
      toast({ message: `Deleted “${source.name}”` });
    } catch (cause) {
      toast({ message: errorMessage(cause), kind: "error" });
    }
  };

  return (
    <div className="ds">
      <div className="ds-head">
        <p className="ds-lede">
          Your own databases, saved in the studio. A project can use one only after you attach it there. Passwords are{" "}
          {KEYCHAIN_NOTE.charAt(0).toLowerCase() + KEYCHAIN_NOTE.slice(1)}
        </p>
        <button type="button" className="btn btn-primary btn-small" onClick={() => setEditing("new")}>
          <Icon name="plus" size={14} strokeWidth={2.2} /> Add a data source
        </button>
      </div>

      {error && (
        <div className="lib-error" role="alert">
          <Icon name="alert" size={15} />
          <span>Data sources couldn’t be loaded: {error}</span>
          <button type="button" className="btn btn-small" onClick={refresh}>
            Try again
          </button>
        </div>
      )}
      {!sources && !error && (
        <div className="panel-empty">
          <Spinner />
        </div>
      )}
      {sources && sources.length === 0 && <p className="lib-empty">No data sources yet.</p>}
      {sources && sources.length > 0 && (
        <ul className="ds-list">
          {sources.map((source) => {
            const test = tests[source.id];
            return (
              <li key={source.id} className="ds-item">
                <div className="ds-item-main">
                  <span className="ds-item-name">
                    <Icon name="database" size={14} /> {source.name}
                  </span>
                  <span className="ds-item-meta">
                    {kindLabel(source.kind, kinds)} · <code>{sourceAddress(source)}</code> · SSL {source.ssl}
                    {source.ssl === "verify-full" && source.ca_file ? " (own CA file)" : ""}
                    {source.has_password ? " · password stored" : " · no password"}
                  </span>
                  <LastTest source={source} now={now} />
                  {test?.result && <TestResult result={test.result} />}
                  {test?.error && (
                    <p className="ds-error" role="alert">
                      {test.error}
                    </p>
                  )}
                  {needsPasswordAgain(source, test?.result) && (
                    <button
                      type="button"
                      className="btn btn-small ds-retype"
                      onClick={() => {
                        setRetype(true);
                        setEditing(source);
                      }}
                    >
                      <Icon name="shield" size={13} /> Type the password again
                    </button>
                  )}
                </div>
                <div className="ds-item-actions">
                  <button
                    type="button"
                    className="btn btn-small"
                    disabled={test?.busy}
                    aria-label={`Test the connection to ${source.name}`}
                    onClick={() => void runTest(source)}
                  >
                    {test?.busy ? <Spinner size={12} /> : null}
                    {test?.busy ? "Testing…" : "Test"}
                  </button>
                  <button
                    type="button"
                    className="btn btn-small"
                    aria-label={`Edit ${source.name}`}
                    onClick={() => {
                      setRetype(false);
                      setEditing(source);
                    }}
                  >
                    Edit
                  </button>
                  <button
                    type="button"
                    className="icon-btn"
                    aria-label={`Delete ${source.name}`}
                    data-tip="Delete"
                    data-tip-align="end"
                    onClick={() => void remove(source)}
                  >
                    <Icon name="trash" size={15} />
                  </button>
                </div>
              </li>
            );
          })}
        </ul>
      )}

      {editing && (
        <DataSourceDialog
          kinds={kinds}
          source={editing === "new" ? null : editing}
          retypePassword={retype && editing !== "new"}
          onClose={() => setEditing(null)}
          onSaved={(saved) => {
            setEditing(null);
            toast({ message: editing === "new" ? `Added “${saved.name}”` : `Saved “${saved.name}”`, kind: "success" });
            refresh();
          }}
        />
      )}
    </div>
  );
}
