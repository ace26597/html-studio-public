// Where the studio looks for skills and connectors, and the person's other places.

import { useEffect, useState } from "react";
import { api, errorMessage } from "./api";
import { placeNote, withPath, type LibraryPlacesInfo } from "./libraryPlaces";

type Kind = "skill_dirs" | "connector_files";

export function PlacesList({
  info,
  busy,
  onChange,
}: {
  info: LibraryPlacesInfo;
  busy: boolean;
  onChange: (kind: Kind, paths: string[]) => void;
}) {
  const [typed, setTyped] = useState<Record<Kind, string>>({ skill_dirs: "", connector_files: "" });
  const groups: { kind: Kind; title: string; hint: string; rows: { path: string; note: string }[] }[] = [
    {
      kind: "skill_dirs",
      title: "Other folders of skills",
      hint: "A folder with one folder per skill, e.g. ~/work/skills",
      rows: info.skill_dirs.map((item) => ({ path: item.path, note: placeNote(item.found, item.skills, "skill", "skills", "no such folder") })),
    },
    {
      kind: "connector_files",
      title: "Other files of connectors",
      hint: "A JSON file with mcpServers, e.g. ~/mcp/servers.json",
      rows: info.connector_files.map((item) => ({ path: item.path, note: placeNote(item.found, item.connectors, "connector", "connectors", "no such file") })),
    },
  ];
  return (
    <div className="places">
      <p className="places-always">Always read: {info.always.join("; ")}.</p>
      {groups.map((group) => (
        <div key={group.kind} className="places-group">
          <h3>{group.title}</h3>
          {info.from_env[group.kind] && <p className="places-note">Set by an environment variable on this computer, which wins over what is saved here.</p>}
          <ul>
            {group.rows.map((row) => (
              <li key={row.path}>
                <span className="places-path">{row.path}</span>
                <span className="places-note">{row.note}</span>
                <button type="button" className="btn btn-small" disabled={busy} onClick={() => onChange(group.kind, withPath(group.rows.map((r) => r.path), row.path, false))}>
                  Remove
                </button>
              </li>
            ))}
          </ul>
          <form
            className="places-add"
            onSubmit={(event) => {
              event.preventDefault();
              if (!typed[group.kind].trim()) return;
              onChange(group.kind, withPath(group.rows.map((r) => r.path), typed[group.kind], true));
              setTyped((all) => ({ ...all, [group.kind]: "" }));
            }}
          >
            <label className="sr-only" htmlFor={`place-${group.kind}`}>
              {group.title}
            </label>
            <input
              id={`place-${group.kind}`}
              placeholder={group.hint}
              value={typed[group.kind]}
              onChange={(event) => setTyped((all) => ({ ...all, [group.kind]: event.target.value }))}
            />
            <button type="submit" className="btn" disabled={busy || !typed[group.kind].trim()}>
              Add
            </button>
          </form>
        </div>
      ))}
    </div>
  );
}

export function LibraryPlacesBlock({ onChanged }: { onChanged?: () => void }) {
  const [info, setInfo] = useState<LibraryPlacesInfo | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api.libraryPlaces().then(setInfo, (cause) => setError(errorMessage(cause)));
  }, []);

  if (!info) return error ? <p role="alert">{error}</p> : null;
  return (
    <>
      {error && <p role="alert">{error}</p>}
      <PlacesList
        info={info}
        busy={busy}
        onChange={(kind, paths) => {
          setBusy(true);
          setError(null);
          api
            .saveLibraryPlaces({ [kind]: paths })
            .then((saved) => {
              setInfo(saved);
              onChanged?.();
            })
            .catch((cause) => setError(errorMessage(cause)))
            .finally(() => setBusy(false));
        }}
      />
    </>
  );
}
