// Keys for other services the studio itself calls (web search). Listed, saved, removed; never shown.

import { useEffect, useState } from "react";
import { api, errorMessage } from "./api";
import { canRemove, keyState, type ServiceKey } from "./serviceKeys";

export function ServiceKeyRows({
  services,
  busy,
  onSave,
  onRemove,
}: {
  services: ServiceKey[];
  busy: string | null;
  onSave: (name: string, key: string) => void;
  onRemove: (name: string) => void;
}) {
  const [typed, setTyped] = useState<Record<string, string>>({});
  return (
    <ul className="service-keys">
      {services.map((service) => (
        <li key={service.name} className="service-key">
          <div className="service-key-text">
            <h3>
              {service.label} <span className={`service-key-state${service.configured ? " is-set" : ""}`}>{service.configured ? "Key set" : "No key"}</span>
            </h3>
            <p>{service.used_for}</p>
            <p className="service-key-where">{keyState(service)}</p>
          </div>
          <form
            className="service-key-form"
            onSubmit={(event) => {
              event.preventDefault();
              const key = (typed[service.name] ?? "").trim();
              if (!key) return;
              onSave(service.name, key);
              setTyped((all) => ({ ...all, [service.name]: "" }));
            }}
          >
            <label className="sr-only" htmlFor={`service-key-${service.name}`}>
              {service.label} key
            </label>
            <input
              id={`service-key-${service.name}`}
              type="password"
              autoComplete="off"
              placeholder={service.configured ? "Replace the key…" : "Paste the key…"}
              value={typed[service.name] ?? ""}
              onChange={(event) => setTyped((all) => ({ ...all, [service.name]: event.target.value }))}
            />
            <button type="submit" className="btn" disabled={busy !== null || !(typed[service.name] ?? "").trim()}>
              Save
            </button>
            {canRemove(service) && (
              <button type="button" className="btn" disabled={busy !== null} onClick={() => onRemove(service.name)}>
                Remove
              </button>
            )}
          </form>
        </li>
      ))}
    </ul>
  );
}

export function ServiceKeys() {
  const [services, setServices] = useState<ServiceKey[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  useEffect(() => {
    api.listServices().then(setServices, (cause) => setError(errorMessage(cause)));
  }, []);

  const change = (name: string, call: Promise<ServiceKey>) => {
    setBusy(name);
    setError(null);
    call
      .then((updated) => setServices((all) => all?.map((item) => (item.name === updated.name ? updated : item)) ?? all))
      .catch((cause) => setError(errorMessage(cause)))
      .finally(() => setBusy(null));
  };

  if (error && !services) return <p role="alert">{error}</p>;
  if (!services) return null;
  return (
    <>
      <p className="service-keys-lead">
        Optional. A key you save here stays on this computer and is never shown again, sent to a model or written into a project.
      </p>
      {error && <p role="alert">{error}</p>}
      <ServiceKeyRows
        services={services}
        busy={busy}
        onSave={(name, key) => change(name, api.saveServiceKey(name, key))}
        onRemove={(name) => change(name, api.removeServiceKey(name))}
      />
    </>
  );
}
