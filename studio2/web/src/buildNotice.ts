/** Which code the studio's server runs, and what to say when it is older than this interface
 *  (CONTRACT.md, "Which code a studio is running"). */

export interface Build {
  commit: string | null;
  committed_at: string | null;
  started_at: string;
  stale: boolean;
  newer: string | null;
}

export interface BuildNotice {
  /** Changes when the server is restarted or newer code lands: a put-away notice comes back then. */
  key: string;
  text: string;
  detail: string;
}

const day = (iso: string | null | undefined): string => {
  const at = iso ? new Date(iso) : null;
  if (!at || Number.isNaN(at.getTime())) return "";
  return at.toLocaleDateString("en-GB", { day: "numeric", month: "long" });
};

/**
 * What to tell the person, or null when the server and its interface match. A server that sends no
 * `build` at all was started before servers reported it, so it is older than this interface.
 */
export function buildNotice(health: { build?: Build | null; shared?: boolean } | null | undefined): BuildNotice | null {
  if (!health) return null;
  const build = health.build;
  if (build && !build.stale) return null;
  const who = health.shared ? "Its owner restarts it." : "Restart the studio to get them.";
  if (!build) {
    return {
      key: "no-build",
      text: "This studio’s server is older than what you are looking at.",
      detail: `Newer features stay hidden until it is restarted. ${who}`,
    };
  }
  const since = day(build.started_at);
  return {
    key: `${build.started_at}|${build.newer ?? ""}`,
    text: `This studio’s server has been running since ${since || "an earlier start"}, and its code has changed since.`,
    detail: `Newer features stay hidden until it is restarted. ${who}`,
  };
}

/** The foot of the Home page: which code the server runs and since when. Empty for an older server. */
export function buildLine(build: Build | null | undefined): string {
  if (!build) return "";
  const since = day(build.started_at);
  const code = build.commit ? `code ${build.commit}` : "its own copy of the code";
  return `Server running ${code}${since ? ` since ${since}` : ""}${build.stale ? ", older than the code on disk" : ""}.`;
}
