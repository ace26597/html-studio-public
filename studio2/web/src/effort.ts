/** How much a request does (depth), whether its content is verified (verify), the polish of its look, and the result card. */

export type Depth = "quick" | "standard" | "deep";
export interface TurnOptions {
  depth: Depth;
  verify: boolean;
  /** "Ask me first". Left out when the server does not know the switch (it would refuse the option). */
  ask?: boolean;
  /** Only on a "Verify content" request: the studio picks no skills or connectors by itself. */
  check_only?: boolean;
  /** Only on a "Polish the look" request: about how the page looks, not what it says. */
  polish?: boolean;
}

/** `short` is what the composer's menu says beside the step; `line` is the full sentence. */
export const DEPTHS: { id: Depth; label: string; short: string; line: string }[] = [
  { id: "quick", label: "Quick", short: "short, few sources", line: "Quick: the smallest thing that answers, few sources, short." },
  { id: "standard", label: "Standard", short: "the usual", line: "Standard: the usual amount of work." },
  {
    id: "deep",
    label: "Deep",
    short: "slower, costs more",
    line: "Deep: plans first, many more sources, a fuller result and a check at the end. Takes longer and uses more of your model allowance or money.",
  },
];

export const VERIFY_LINE =
  "Verify content: before the request ends, an independent check compares every figure with its saved source and recomputes totals. A pass does not prove the source is right.";

/** Sent by "Verify content": verify only, no new content (the server adds the instructions). */
export const VERIFY_ONLY_TEXT =
  "Verify content: check every figure and statement on this page against its sources, and change nothing except certain fixes.";

/** Sent by "Polish the look": how the page looks and reads, no new content (the server adds the instructions). */
export const POLISH_TEXT =
  "Polish the look: see how this page looks on a desktop first, then on a tablet and a phone, fix what is clearly off, and suggest the rest.";

export const DEFAULT_OPTIONS: TurnOptions = { depth: "standard", verify: false };

/** The options "Verify content" sends (CONTRACT.md, depth and verify). */
export const CHECK_ONLY_OPTIONS: TurnOptions = { depth: "standard", verify: true, check_only: true };

/** The options "Polish the look" sends (CONTRACT.md, "Polishing the look"). */
export const POLISH_OPTIONS: TurnOptions = { depth: "standard", verify: false, polish: true };

/** A project's remembered choice (`turn_options`), anything odd read as the default. */
export function projectOptions(raw: unknown): TurnOptions {
  const value = (raw && typeof raw === "object" ? raw : {}) as Record<string, unknown>;
  const depth = DEPTHS.some((d) => d.id === value.depth) ? (value.depth as Depth) : "standard";
  // `ask` only when the server sent it: an older server refuses an option it does not know.
  return { depth, verify: value.verify === true, ...(typeof value.ask === "boolean" ? { ask: value.ask } : {}) };
}

export function depthLine(depth: Depth): string {
  return (DEPTHS.find((d) => d.id === depth) ?? DEPTHS[1]).line;
}

/** Arrow keys move along the three steps (for the segmented control). */
export function stepDepth(depth: Depth, key: string): Depth | null {
  const at = DEPTHS.findIndex((d) => d.id === depth);
  if (key === "ArrowLeft" || key === "ArrowUp") return DEPTHS[Math.max(0, at - 1)].id;
  if (key === "ArrowRight" || key === "ArrowDown") return DEPTHS[Math.min(DEPTHS.length - 1, at + 1)].id;
  if (key === "Home") return DEPTHS[0].id;
  if (key === "End") return DEPTHS[DEPTHS.length - 1].id;
  return null;
}

// -- the verification card ------------------------------------------------------------------
export type FindingStatus = "failed" | "unverifiable" | "fixed" | "passed";
const ORDER: FindingStatus[] = ["failed", "unverifiable", "fixed", "passed"];

export interface Finding {
  what: string;
  where: string;
  status: FindingStatus;
  note: string;
}

export interface VerificationItem {
  type: "verification";
  key: string;
  checked: number;
  passed: number;
  fixed: number;
  failed: number;
  unverifiable: number;
  summary: string;
  findings: Finding[];
  /** Findings the studio left out because there were too many (`findings_cut`). */
  cut: number;
  /** The studio's own re-check after the report (`verification_recheck`), when it has arrived. */
  recheck?: Recheck;
  ts: string;
}

export interface RecheckProblem {
  /** The page's path, or the file's (`kind: "file"`). */
  page: string;
  kind: "page" | "file";
  what: string;
  /** For a file: where in it ("Slide 3, title", "Summary!B4 (Revenue, 2025)", "Page 2"). */
  where: string;
  why: string;
}

export interface Recheck {
  pages: { page: string; kind: "page" | "file"; checked: number; error: string | null }[];
  problems: RecheckProblem[];
  total: number;
  cut: number;
  /** Pages and files past the re-check's caps (10 of each), which it did not read. */
  notRechecked: string[];
  agrees: boolean;
}

const count = (value: unknown): number =>
  typeof value === "number" && Number.isFinite(value) && value >= 0 ? Math.floor(value) : 0;
const text = (value: unknown): string => (typeof value === "string" ? value : "");

/** A `verification` event → the card's item; findings failed first, then unverifiable, fixed, passed. */
export function verificationItem(event: Record<string, unknown>, key: string, ts: string): VerificationItem {
  const raw = Array.isArray(event.findings) ? event.findings : [];
  const findings: Finding[] = raw
    .filter((f): f is Record<string, unknown> => !!f && typeof f === "object")
    .map((f) => ({
      what: text(f.what),
      where: text(f.where),
      status: (ORDER.includes(f.status as FindingStatus) ? f.status : "unverifiable") as FindingStatus,
      note: text(f.note),
    }));
  findings.sort((a, b) => ORDER.indexOf(a.status) - ORDER.indexOf(b.status));
  return {
    type: "verification",
    key,
    checked: count(event.checked),
    passed: count(event.passed),
    fixed: count(event.fixed),
    failed: count(event.failed),
    unverifiable: count(event.unverifiable),
    summary: text(event.summary),
    findings,
    cut: count(event.findings_cut),
    ts,
  };
}

/** The card's first line: counts first, the worst news first. */
export function verificationHeadline(item: VerificationItem): string {
  const parts = [`Checked ${item.checked}`];
  if (item.failed) parts.push(`${item.failed} wrong and not fixed`);
  if (item.fixed) parts.push(`${item.fixed} fixed`);
  if (item.unverifiable) parts.push(`${item.unverifiable} could not be checked`);
  parts.push(`${item.passed} matched`);
  return parts.join(" · ");
}

export function verificationTone(item: VerificationItem): "error" | "warning" | "ok" {
  return item.failed ? "error" : item.unverifiable ? "warning" : "ok";
}

export const STATUS_LABEL: Record<FindingStatus, string> = {
  failed: "Wrong, not fixed",
  unverifiable: "Could not be checked",
  fixed: "Fixed",
  passed: "Matched",
};

export const MATCH_MEANS =
  "Matched means the figure is in the saved source or was recomputed from the page’s own data. It does not prove the source is right.";

// -- the studio's own re-check (`verification_recheck`) ------------------------------------------
/** A `verification_recheck` event → what the card shows above the agent's own counts. */
export function recheckFrom(event: Record<string, unknown>): Recheck {
  const rows = (value: unknown) =>
    (Array.isArray(value) ? value : []).filter((r): r is Record<string, unknown> => !!r && typeof r === "object");
  const kind = (value: unknown): "page" | "file" => (value === "file" ? "file" : "page");
  const problems = rows(event.problems).map((p) => ({
    page: text(p.page),
    kind: kind(p.kind),
    what: text(p.what),
    where: text(p.where),
    why: text(p.why),
  }));
  return {
    pages: rows(event.pages).map((p) => ({
      page: text(p.page),
      kind: kind(p.kind),
      checked: count(p.checked),
      error: typeof p.error === "string" && p.error ? p.error : null,
    })),
    problems,
    total: Math.max(count(event.problems_total), problems.length),
    cut: count(event.problems_cut),
    notRechecked: (Array.isArray(event.not_rechecked) ? event.not_rechecked : []).filter(
      (name): name is string => typeof name === "string" && !!name,
    ),
    agrees: event.agrees === true,
  };
}

/** The re-check's one line: what is still wrong first, else that it agrees, else what it could not do. */
export function recheckHeadline(recheck: Recheck): string {
  if (recheck.total > 0)
    return `${recheck.total} ${recheck.total === 1 ? "item" : "items"} the studio’s own check still finds wrong`;
  const read = recheck.pages.filter((p) => !p.error);
  const files = recheck.pages.some((p) => p.kind === "file");
  if (!read.length) return `The studio’s own check could not read the ${files ? "page or its files" : "page"}.`;
  if (read.every((p) => p.checked === 0))
    return `The studio’s own check found no marked figures on the page${files ? " and no figures in its files" : ""} to compare.`;
  if (recheck.notRechecked.length)
    return `The studio’s own check found nothing wrong in what it read, but ${recheck.notRechecked.length} more ${
      recheck.notRechecked.length === 1 ? "page or file was" : "pages or files were"
    } not re-checked.`;
  return "The studio’s own check agrees: nothing it can see is wrong.";
}

export const RECHECK_COVERS =
  "The studio’s re-check covers only what its checks can see: marked figures on the page against their saved sources and project data, cited sources against the page’s source list, and the figures in the project’s Office and PDF files against checked page figures, data and saved sources. It does not look at charts, unmarked totals or how an app behaves.";

/** A problem the re-check found in a file, as one line: its place, then the figure ("Slide 3, title: $4.3 billion"),
 * with the file's name first when the re-check covered more than one page or file. */
export function fileProblemLine(problem: RecheckProblem, withFile: boolean): string {
  const name = problem.page.split("/").pop() || problem.page;
  const place = problem.where ? `${problem.where}: ` : "";
  return `${withFile ? `${name} · ` : ""}${place}${problem.what}`;
}
