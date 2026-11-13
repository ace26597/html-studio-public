// Export: take a page or an app out of the studio (CONTRACT.md, "Export").

import { useEffect, useState } from "react";
import { api } from "./api";
import { exportWays, type ExportOptions, type ExportWay } from "./exportInfo";
import { errorMessage } from "./api";
import { Icon, Spinner } from "./icons";
import { Modal } from "./Modal";

export function ExportWays({ ways, href }: { ways: ExportWay[]; href: (key: ExportWay["key"]) => string }) {
  return (
    <ul className="export-ways">
      {ways.map((way) => (
        <li key={way.key} className={`export-way${way.available ? "" : " is-unavailable"}`}>
          <div className="export-way-text">
            <h3>{way.title}</h3>
            <p>{way.what}</p>
            {way.facts && <p className="export-facts">{way.facts}</p>}
            {way.notes.map((note) => (
              <p key={note} className="export-note">
                {note}
              </p>
            ))}
            {!way.available && way.reason && <p className="export-reason">Not available: {way.reason}</p>}
          </div>
          {way.available && (
            <a className="btn export-get" href={href(way.key)} download>
              <Icon name="download" size={14} /> {way.button}
            </a>
          )}
        </li>
      ))}
    </ul>
  );
}

export function ExportDialog({ projectId, onClose }: { projectId: string; onClose: () => void }) {
  const [options, setOptions] = useState<ExportOptions | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    api.exportOptions(projectId).then(
      (found) => live && setOptions(found),
      (cause) => live && setError(errorMessage(cause)),
    );
    return () => {
      live = false;
    };
  }, [projectId]);

  const href = (key: ExportWay["key"]) =>
    key === "html" ? api.exportPageUrl(projectId) : key === "folder" ? api.exportFolderUrl(projectId) : api.downloadUrl(projectId);

  return (
    <Modal title="Export" onClose={onClose} wide>
      <p className="dialog-body export-lead">
        Take this out of the studio. You get a file; nothing is published, and where it goes is up to you.
      </p>
      {error && <p className="dialog-body" role="alert">{error}</p>}
      {!options && !error && (
        <p className="dialog-body">
          <Spinner /> Looking at what the page needs…
        </p>
      )}
      {options && <ExportWays ways={exportWays(options)} href={href} />}
    </Modal>
  );
}
