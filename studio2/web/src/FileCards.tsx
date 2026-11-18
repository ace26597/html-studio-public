// The files a finished request made or changed, one small card each, under its answer
// (CONTRACT.md, "Files in the chat, and files shown as what they are").

import { useState } from "react";
import { cardMeta, deliverableKind, deliverablesOf, fileNow, moreLine } from "./deliverables";
import { baseName, formatBytes } from "./format";
import { Icon, type IconName } from "./icons";
import type { ChatFiles } from "./chatFiles";
import type { CheckpointItem, Turn } from "./thread";

function Thumb({ path, href }: { path: string; href: string | null }) {
  const [broken, setBroken] = useState(false);
  const kind = deliverableKind(path);
  if (kind === "picture" && href && !broken) {
    return <img className="file-card-thumb" src={href} alt="" loading="lazy" onError={() => setBroken(true)} />;
  }
  const icon: IconName = kind === "video" || kind === "sound" ? "play" : "file";
  return (
    <span className="file-card-icon" aria-hidden="true">
      <Icon name={icon} size={16} />
    </span>
  );
}

export function FileCards({
  turn,
  files,
  onOpen,
  onOpenVersion,
  fileHref,
}: {
  turn: Turn;
  files: ChatFiles | undefined;
  onOpen: (path: string | null) => void;
  onOpenVersion: (sha: string) => void;
  fileHref: (path: string) => string | null;
}) {
  const checkpoints = turn.items.filter((item): item is CheckpointItem => item.type === "checkpoint");
  const { shown, more } = deliverablesOf(checkpoints, turn.attachments);
  if (!shown.length) return null;
  const versionOf = (path: string) =>
    [...checkpoints].reverse().find((item) => item.kind === "agent" && item.files.some((file) => file.path === path))?.sha ?? null;
  const rest = moreLine(more);
  return (
    <section className="file-cards" aria-label={shown.length + more === 1 ? "A file this request made" : "Files this request made"}>
      <ul className="file-card-list">
        {shown.map((change) => {
          const name = baseName(change.path);
          const now = fileNow(files?.sizes ?? null, change.path, files?.complete ?? false);
          const meta = cardMeta(change, now, formatBytes);
          if (now.state === "gone") {
            const sha = versionOf(change.path);
            return (
              <li key={change.path} className="file-card is-gone" title={change.path}>
                <Thumb path={change.path} href={null} />
                <span className="file-card-text">
                  <span className="file-card-name">{name}</span>
                  <span className="file-card-meta">{meta}</span>
                </span>
                {sha && (
                  <button
                    type="button"
                    className="file-card-action"
                    aria-label={`Show the version that made ${name}`}
                    onClick={() => onOpenVersion(sha)}
                  >
                    Show version
                  </button>
                )}
              </li>
            );
          }
          return (
            <li key={change.path}>
              <button
                type="button"
                className="file-card"
                title={`${change.path} — open in Files`}
                aria-label={`Open ${name} in Files. ${meta}`}
                onClick={() => onOpen(change.path)}
              >
                <Thumb path={change.path} href={fileHref(change.path)} />
                <span className="file-card-text">
                  <span className="file-card-name">{name}</span>
                  <span className="file-card-meta">{meta}</span>
                </span>
                <span className="file-card-action" aria-hidden="true">
                  Open
                </span>
              </button>
            </li>
          );
        })}
      </ul>
      {rest && (
        <button type="button" className="file-cards-more" onClick={() => onOpen(null)}>
          {rest}
        </button>
      )}
    </section>
  );
}
