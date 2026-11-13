// Versions: a floating list on the right. Choosing a version opens its diff
// beside the list; restoring creates a new version (history is never rewritten).

import { useEffect, useMemo, useState } from "react";
import { api, errorMessage } from "./api";
import { fileStatus, relativeTime } from "./format";
import { Spinner } from "./icons";
import type { Version, VersionDiff } from "./types";
import { IconButton, useConfirm, useToast } from "./ui";

interface VersionsDrawerProps {
  projectId: string;
  versionsRev: number;
  running: boolean;
  selectedSha: string | null;
  onSelect: (sha: string | null) => void;
  onClose: () => void;
}

const KIND_LABEL: Record<string, string> = {
  init: "Created",
  agent: "Agent",
  user: "You",
  restore: "Restore",
};

function useNow(intervalMs: number): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), intervalMs);
    return () => window.clearInterval(timer);
  }, [intervalMs]);
  return now;
}

function filesSummary(version: Version): string {
  const count = version.files.length;
  if (!count) return "No file changes";
  const names = version.files.slice(0, 2).map((file) => file.path.split("/").pop());
  const rest = count - names.length;
  return rest > 0 ? `${names.join(", ")} and ${rest} more` : names.join(", ");
}

interface DiffLine {
  kind: "file" | "hunk" | "add" | "del" | "ctx" | "meta";
  text: string;
}

/** Classify the lines of a unified diff for colouring. Pure. */
export function parseDiff(diff: string): DiffLine[] {
  const lines: DiffLine[] = [];
  // Hunk bodies are consumed by the line counts in their `@@` header, so a
  // removed line that itself starts with "-- " is never mistaken for a header.
  let oldLeft = 0;
  let newLeft = 0;
  let gitHeader = false;
  for (const raw of diff.split("\n")) {
    if (oldLeft > 0 || newLeft > 0) {
      if (raw.startsWith("+")) {
        newLeft--;
        lines.push({ kind: "add", text: raw.slice(1) });
      } else if (raw.startsWith("-")) {
        oldLeft--;
        lines.push({ kind: "del", text: raw.slice(1) });
      } else if (raw.startsWith("\\")) {
        lines.push({ kind: "meta", text: raw.slice(2) });
      } else {
        oldLeft--;
        newLeft--;
        lines.push({ kind: "ctx", text: raw.startsWith(" ") ? raw.slice(1) : raw });
      }
      continue;
    }
    const hunk = /^@@ -\d+(?:,(\d+))? \+\d+(?:,(\d+))? @@/.exec(raw);
    if (hunk) {
      oldLeft = hunk[1] === undefined ? 1 : Number(hunk[1]);
      newLeft = hunk[2] === undefined ? 1 : Number(hunk[2]);
      lines.push({ kind: "hunk", text: raw });
    } else if (raw.startsWith("diff --git ")) {
      gitHeader = true;
      const match = / b\/(.+)$/.exec(raw);
      lines.push({ kind: "file", text: match ? match[1] : raw.slice(11) });
    } else if (raw.startsWith("+++ ") && !gitHeader) {
      lines.push({ kind: "file", text: raw.slice(4).replace(/^b\//, "") });
    } else if (raw.startsWith("\\")) {
      lines.push({ kind: "meta", text: raw.slice(2) });
    } else if (/^(Binary files|rename |new file|deleted file)/.test(raw)) {
      lines.push({ kind: "meta", text: raw });
    }
    // Anything else between hunks (index …, --- a/x, +++ b/x) is noise.
  }
  return lines;
}

const SIGN: Record<DiffLine["kind"], string> = { add: "+", del: "−", ctx: "", hunk: "", file: "", meta: "" };

function DiffView({ diff }: { diff: VersionDiff }) {
  const lines = useMemo(() => parseDiff(diff.diff), [diff.diff]);
  if (!lines.length) return <div className="panel-empty">No changes to text files in this version.</div>;
  return (
    <div className="diff" role="group" aria-label="Changes in this version">
      {lines.map((line, index) =>
        line.kind === "file" ? (
          <div key={index} className="diff-file">
            {line.text}
          </div>
        ) : (
          <div key={index} className={`diff-line is-${line.kind}`}>
            <span className="diff-sign" aria-hidden="true">
              {SIGN[line.kind]}
            </span>
            <span className="diff-text">{line.text || " "}</span>
          </div>
        ),
      )}
      {diff.truncated && <div className="diff-line is-meta">The rest of this diff is too long to show.</div>}
    </div>
  );
}

export function VersionsDrawer({
  projectId,
  versionsRev,
  running,
  selectedSha,
  onSelect,
  onClose,
}: VersionsDrawerProps) {
  const toast = useToast();
  const confirm = useConfirm();
  const now = useNow(30_000);
  const [versions, setVersions] = useState<Version[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [diff, setDiff] = useState<{ sha: string; data: VersionDiff | null; error: string | null } | null>(null);
  const [restoring, setRestoring] = useState(false);

  useEffect(() => {
    let cancelled = false;
    api
      .listVersions(projectId)
      .then((list) => {
        if (cancelled) return;
        setVersions(list);
        setError(null);
      })
      .catch((cause) => {
        if (!cancelled) setError(errorMessage(cause));
      });
    return () => {
      cancelled = true;
    };
  }, [projectId, versionsRev]);

  useEffect(() => {
    if (!selectedSha) {
      setDiff(null);
      return;
    }
    let cancelled = false;
    setDiff({ sha: selectedSha, data: null, error: null });
    api
      .getDiff(projectId, selectedSha)
      .then((data) => {
        if (!cancelled) setDiff({ sha: selectedSha, data, error: null });
      })
      .catch((cause) => {
        if (!cancelled) setDiff({ sha: selectedSha, data: null, error: errorMessage(cause) });
      });
    return () => {
      cancelled = true;
    };
  }, [projectId, selectedSha]);

  const selected = versions?.find((version) => version.sha === selectedSha) ?? null;
  const isCurrent = versions?.[0]?.sha === selectedSha;

  const restore = async (version: Version) => {
    const ok = await confirm({
      title: "Restore this version?",
      body: (
        <>
          The page goes back to how it was <strong>{relativeTime(version.created_at)}</strong>. Nothing is lost: the
          restore is saved as a new version, so you can return to today’s page the same way.
        </>
      ),
      confirmLabel: "Restore",
    });
    if (!ok) return;
    setRestoring(true);
    try {
      await api.restore(projectId, version.sha);
      toast({ message: "Version restored", kind: "success" });
      onSelect(null);
    } catch (cause) {
      toast({ message: errorMessage(cause), kind: "error" });
    } finally {
      setRestoring(false);
    }
  };

  const onEscape = (event: React.KeyboardEvent) => {
    if (event.key !== "Escape" || event.defaultPrevented) return;
    event.stopPropagation();
    if (selectedSha) onSelect(null);
    else onClose();
  };

  return (
    <>
      <aside className="panel panel-right" aria-label="Versions" onKeyDown={onEscape}>
        <header className="panel-header">
          <h2>Versions</h2>
          <span className="panel-spacer" />
          <IconButton icon="x" label="Close versions" tip="Close (Esc)" tipAlign="end" onClick={onClose} />
        </header>
        <div className="panel-body">
          {versions === null && !error && (
            <div className="panel-empty">
              <Spinner />
            </div>
          )}
          {error && <div className="panel-empty is-error">{error}</div>}
          {versions && versions.length === 0 && <div className="panel-empty">No versions yet.</div>}
          {versions && versions.length > 0 && (
            <ol className="version-list">
              {versions.map((version, index) => (
                <li key={version.sha}>
                  <button
                    type="button"
                    className={`version${version.sha === selectedSha ? " is-selected" : ""}`}
                    aria-pressed={version.sha === selectedSha}
                    onClick={() => onSelect(version.sha === selectedSha ? null : version.sha)}
                  >
                    <span className="version-top">
                      <span className={`badge badge-${version.kind}`}>{KIND_LABEL[version.kind] ?? version.kind}</span>
                      {index === 0 && <span className="badge badge-current">Current</span>}
                      <span className="version-time" title={new Date(version.created_at).toLocaleString()}>
                        {relativeTime(version.created_at, now)}
                      </span>
                    </span>
                    <span className="version-message">{version.message || "Untitled change"}</span>
                    <span className="version-files">{filesSummary(version)}</span>
                  </button>
                </li>
              ))}
            </ol>
          )}
        </div>
      </aside>

      {selectedSha && (
        <section className="viewer viewer-right" aria-label="Version changes" onKeyDown={onEscape}>
          <header className="panel-header">
            <div className="viewer-heading">
              <h2 className="viewer-path">{selected?.message || "Changes"}</h2>
              {selected && (
                <span className="viewer-sub">
                  {selected.files.length === 0
                    ? "No file changes"
                    : selected.files
                        .slice(0, 4)
                        .map((file) => `${file.path} (${fileStatus(file.status)})`)
                        .join(" · ") + (selected.files.length > 4 ? ` · +${selected.files.length - 4}` : "")}
                </span>
              )}
            </div>
            <span className="panel-spacer" />
            {selected && !isCurrent && (
              <button
                type="button"
                className="btn btn-small"
                disabled={running || restoring}
                data-tip={running ? "Wait for the agent to finish before restoring" : undefined}
                aria-description={running ? "Wait for the agent to finish before restoring" : undefined}
                data-tip-align="end"
                onClick={() => void restore(selected)}
              >
                {restoring ? "Restoring…" : "Restore this version"}
              </button>
            )}
            <IconButton icon="x" label="Close changes" tip="Close" tipAlign="end" onClick={() => onSelect(null)} />
          </header>
          <div className="viewer-body">
            {diff?.error && <div className="panel-empty is-error">{diff.error}</div>}
            {diff && !diff.data && !diff.error && (
              <div className="panel-empty">
                <Spinner />
              </div>
            )}
            {diff?.data && <DiffView diff={diff.data} />}
          </div>
        </section>
      )}
    </>
  );
}
