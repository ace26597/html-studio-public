// One notice, on every page, when this studio's server is older than its interface.

import { useState } from "react";
import { Icon } from "./icons";
import { buildNotice, type Build } from "./buildNotice";
import { loadString, saveString } from "./storage";

export function BuildBanner({ health }: { health: { build?: Build | null; shared?: boolean } | null }) {
  const notice = buildNotice(health);
  // Put away for this notice only: a restart, or newer code, brings it back.
  const [away, setAway] = useState(() => loadString("build-notice-away"));
  if (!notice || away === notice.key) return null;
  return (
    <div className="build-banner" role="status">
      <Icon name="info" size={15} className="build-banner-icon" />
      <p>
        <strong>{notice.text}</strong> {notice.detail}
      </p>
      <button
        type="button"
        className="icon-btn build-banner-close"
        aria-label="Put this notice away"
        onClick={() => {
          saveString("build-notice-away", notice.key);
          setAway(notice.key);
        }}
      >
        <Icon name="x" size={14} />
      </button>
    </div>
  );
}
