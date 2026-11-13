// Types mirrored from studio2/CONTRACT.md. Keep the two in step.

export interface Project {
  id: string;
  name: string;
  created_at: string;
  updated_at: string;
  port: number;
  preview_url: string;
  connection: string;
  model: string | null;
  /** The remembered depth and verify choice (absent = standard, off). */
  turn_options?: { depth?: string; verify?: boolean } | null;
  session_id: string | null;
  running_turn: string | null;
  head: string;
  /** A name the studio offers while the page is still called "Untitled"; null when none is on offer. */
  name_suggestion?: string | null;
  /** Step 2. Absent on a Step 1 server. */
  app?: ProjectApp;
  /** Step 2: whether risky shell commands wait for the user. Default `ask`. */
  approvals?: Approvals;
  /** Step 2b: ids of the data sources attached to this project. */
  data_sources?: string[];
  /** The template this project follows (its files are in `template/`), or null. */
  uses_template?: { id: string; label: string; kind: string } | null;
  /** The example that comes with the studio this project is a copy of. */
  from_example?: string | null;
  /** Step 3: only the person's deviations from the default skills / MCP servers. */
  library?: { skills: Record<string, boolean>; mcp_servers: Record<string, boolean> };
  /** Step 3k: where an app brought in by its folder came from; null otherwise. */
  attach?: AttachRecord | null;
  /** Step 3k: an in-place app's declaration (replaces studio.json); null otherwise. */
  app_config?: { run?: string; restart?: string; address?: string } | null;
  /** Step 3l: how this attached project is made live; null for other projects. */
  ship?: ShipView | null;
}

// ---- Step 3l: make it live -------------------------------------------------------

export type ShipMethod = "branch" | "merge" | "files" | "none";

export interface ShipRecipe {
  checks: string[];
  method: ShipMethod;
  after: string[];
  address: string | null;
  protected: string[];
  /** Commands after going back; null = the after-commands again. */
  back: string[] | null;
  timeout: number;
  notes: string;
}

export interface ShipCommandResult {
  command: string;
  exit_code: number | null;
  output: string;
  duration_ms: number;
  timed_out: boolean;
}

export interface ShipCard {
  ship_id: string;
  method: ShipMethod;
  method_label: string;
  where: string;
  head: string;
  base: string | null;
  files: FileChange[];
  files_more: number;
  checks: ShipCommandResult[];
  writes: string[];
  commands: { command: string; where: string }[];
  address: string | null;
  protected: string[];
  needs_name: boolean;
  project_name: string;
  going_back: string;
  limits: string[];
  merge_help: string | null;
}

export interface ShipLast {
  ship_id: string;
  result: ShipResult;
  at: string;
  head: string;
  method: ShipMethod;
}

export interface ShipView {
  available: boolean;
  methods: ShipMethod[];
  recipe: ShipRecipe | null;
  confirmed: boolean;
  confirmed_by: string | null;
  confirmed_at: string | null;
  last: ShipLast | null;
  running: string | null;
  card: ShipCard | null;
}

export type ShipResult =
  | "shipped"
  | "denied"
  | "checks_failed"
  | "refused"
  | "rolled_back"
  | "back_failed"
  | "failed"
  | "interrupted";

export interface ShipRecord {
  id: string;
  project: string;
  by: "person" | "agent" | string;
  turn_id: string | null;
  started_at: string;
  finished_at: string | null;
  phase: string;
  result: ShipResult | null;
  message: string | null;
  recipe: ShipRecipe;
  attach: { mode: AttachMode; path: string; kind: string; branch: string | null; studio_branch: string | null };
  plan: { method: ShipMethod; base: string | null; head: string; where: string; writes: string[] };
  files: FileChange[];
  files_more: number;
  checks: ShipCommandResult[];
  decision: { decision: "allow" | "deny"; at: string; by: string; protected: string[] } | null;
  applied: string[];
  after: ShipCommandResult[];
  address: { ok: boolean; address: string; status: number | null; attempts: string[] } | null;
  back: string[];
  back_after: ShipCommandResult[];
}

// ---- Step 3k: bring an app by its folder ------------------------------------------

export type AttachMode = "copy" | "in_place";

export interface FileChange {
  path: string;
  status: "added" | "modified" | "deleted" | "untracked" | string;
}

export interface AttachRecord {
  mode: AttachMode;
  path: string;
  kind: "git" | "folder";
  framework: string | null;
  branch: string | null;
  started_from: string | null;
  studio_branch: string | null;
  import_commit: string;
  uncommitted: FileChange[];
  included_uncommitted: boolean;
  env_files: string[];
  install: string | null;
  address: string | null;
  attached_at: string;
}

/** `POST /api/apps/inspect`. */
export interface Inspection {
  path: string;
  name: string;
  exists: boolean;
  readable: boolean;
  writable: boolean;
  git: { branch: string | null; head: string | null; subject: string; uncommitted: FileChange[]; uncommitted_more: number } | null;
  framework: { id: string; label: string } | null;
  run: string | null;
  package_manager: string | null;
  install: string | null;
  env_files: string[];
  size: { files: number; bytes: number; history_bytes: number; partial: boolean };
  refused: { copy: string | null; in_place: string | null };
  notes: string[];
}

/** `POST /api/projects/attach`. */
export interface AttachRequest {
  path: string;
  mode: AttachMode;
  name?: string;
  run?: string;
  address?: string;
  env_files?: string[];
  include_uncommitted?: boolean;
}

// ---- Step 2b: library and data sources ------------------------------------------

export interface LibraryTool {
  id: string;
  label: string;
  description: string;
  available: boolean;
  note: string | null;
}

export type SkillSource = "studio" | "yours" | "machine" | "built-in";

export interface LibrarySkill {
  /** Step 3: `studio:deep-research`, `yours:…`, `machine:…`. */
  id: string;
  name?: string;
  label: string;
  description: string;
  source: SkillSource | string;
  /** On by default in every project (studio and yours). */
  default?: boolean;
  /** Step 3o: where it comes from ("Plugin pdf@anthropic", …). */
  origin?: string | null;
}

export interface LibraryAgent {
  name: string;
  description: string;
}

/** A tool of the studio's own MCP server (`mcp__studio__<name>`). */
export interface StudioTool {
  name: string;
  description: string;
  available: boolean;
  note: string | null;
}

export type McpSource = "yours" | "machine" | "suggested";

export interface McpTestResult {
  ok: boolean;
  message: string;
  tools: { name: string; description: string }[];
}

export interface McpServer {
  id: string;
  name: string;
  label: string | null;
  transport: "http" | "stdio" | string;
  url: string | null;
  command: string | null;
  args: string[];
  /** Header (http) or environment (stdio) names; the values are never sent to the browser. */
  secret_names: string[];
  source: McpSource | string;
  description: string | null;
  last_test: { ok: boolean; at: string; message: string; tools: { name: string; description: string }[] } | null;
  /** Step 3o. */
  origin?: string | null;
  state?: ConnectorState;
  fix?: string | null;
  plugin_enabled?: boolean;
}

/** Body of `POST /api/mcp-servers`; PATCH takes any subset. */
export interface McpServerInput {
  name: string;
  label?: string;
  transport: "http" | "stdio";
  url?: string;
  command?: string;
  args?: string[];
  headers?: Record<string, string>;
  env?: Record<string, string>;
  description?: string;
}

/** Step 3o: this project's word about an item (true always, false never, null left to the studio). */
export type ProjectChoice = boolean | null;
/** Step 3o: the whole machine's word about an item. */
export type MachinePref = "always" | "never" | null;

export interface ProjectLibrary {
  skills: (LibrarySkill & { enabled: boolean; choice?: ProjectChoice; pref?: MachinePref })[];
  mcp_servers: (McpServer & { enabled: boolean; choice?: ProjectChoice; pref?: MachinePref })[];
  /** Step 3o: connectors the assistant asked for in an earlier request. */
  requested?: string[];
  /** Step 3o: the last `library_picked` of this project, with `at`. */
  last_pick?: (LibraryPick & { at?: string }) | null;
}

// ---- Step 3o: a library that picks for itself ----------------------------------------

export type ConnectorState = "ok" | "needs-auth" | "failed" | "needs-setting" | null;

/** `index` in `GET /api/library`, `GET /api/library/items` and `POST /api/library/refresh`. */
export interface LibraryIndex {
  skills: number;
  agents: number;
  connectors: number;
  by_source: {
    skills: Record<string, number>;
    agents: Record<string, number>;
    connectors: Record<string, number>;
  };
  plugins: number;
  plugins_enabled: number;
  duplicates: number;
  built_at: string | null;
  build_ms: number | null;
}

export interface LibraryItem {
  id: string;
  name: string;
  label: string;
  description: string | null;
  source: string;
  origin?: string | null;
  /** Skills and agents: false when their plugin is switched off in Claude Code. */
  enabled?: boolean;
  size?: number;
  files?: number;
  pref?: MachinePref;
  choice?: ProjectChoice;
  picked?: number;
  used?: number;
  benched?: boolean;
  // connectors
  transport?: string;
  url?: string | null;
  command?: string | null;
  state?: ConnectorState;
  fix?: string | null;
  missing?: string[];
  plugin_enabled?: boolean;
}

export type LibraryItemKind = "skills" | "agents" | "connectors";

/** `GET /api/library/items`. */
export interface LibraryItemsPage {
  items: LibraryItem[];
  total: number;
  offset: number;
  limit: number;
  counts: Record<string, number>;
  index: LibraryIndex;
}

export interface LibraryPrefs {
  skills: Record<string, "always" | "never">;
  mcp_servers: Record<string, "always" | "never">;
}

export interface PickedSkill {
  id: string;
  name: string;
  label: string;
  source: string;
  origin: string | null;
  how: "pinned" | "earlier" | "all" | "picked" | string;
  reason: string | null;
}

export interface PickedConnector {
  id: string;
  name: string;
  label: string;
  source: string;
  origin: string | null;
  how: "pinned" | "requested" | "picked" | string;
  reason: string | null;
  state?: ConnectorState;
  fix?: string | null;
}

/** The body of a `library_picked` event (and a project's `last_pick`). */
export interface LibraryPick {
  mode: "model" | "words" | "all" | "none" | string;
  skills: PickedSkill[];
  connectors: PickedConnector[];
  library_skills?: number;
  considered?: number;
  loaded_skills?: number;
  left_out?: number;
  list_chars?: number;
  budget_chars?: number;
  description_cap?: number | null;
  ms?: number;
  total_ms?: number;
  note?: string | null;
}

export type DataKind = "postgres" | "redshift" | "mysql";

export interface DataKindInfo {
  kind: DataKind | string;
  label: string;
  default_port: number;
  available: boolean;
  note: string | null;
}

/** `GET /api/library`. */
export interface Library {
  connections: Connection[];
  tools: LibraryTool[];
  skills: LibrarySkill[];
  /** Step 3. */
  agents?: LibraryAgent[];
  /** Step 3. */
  studio_tools?: StudioTool[];
  mcp_servers: McpServer[];
  templates: Template[];
  data_kinds: DataKindInfo[];
  /** When the last agent session reported its built-in skills and MCP servers; null before the first run. */
  observed_at: string | null;
  /** Step 3o: what was found on this computer. */
  index?: LibraryIndex;
  /** Step 3o: the whole machine's always / never. */
  prefs?: LibraryPrefs;
}

export type SslMode = "disable" | "prefer" | "require" | "verify-full";

export interface DataSourceTestResult {
  ok: boolean;
  message: string;
  server_version?: string | null;
  tables?: number | null;
}

export interface DataSource {
  id: string;
  name: string;
  kind: DataKind | string;
  host: string;
  port: number;
  database: string;
  user: string;
  ssl: SslMode | string;
  has_password: boolean;
  created_at: string;
  last_test: { ok: boolean; at: string; message: string } | null;
}

export interface ProjectDataSource extends DataSource {
  attached: boolean;
}

/** Body of create / test-before-saving; PATCH takes any subset. `password: ""` on PATCH removes it. */
export interface DataSourceInput {
  name: string;
  kind: string;
  host: string;
  port?: number;
  database: string;
  user: string;
  password?: string;
  ssl?: string;
}

export type Approvals = "ask" | "allow";

// ---- Step 2: the project's own app (`studio.json`) -------------------------------

export type AppState = "none" | "starting" | "running" | "stopped" | "crashed" | "external";

/** `Project.app`: the short form carried on every project record. */
export interface ProjectApp {
  configured: boolean;
  state: AppState | string;
}

/** `GET /api/projects/{id}/app`. */
export interface AppInfo {
  configured: boolean;
  run: string | null;
  state: AppState | string;
  pid: number | null;
  port: number | null;
  exit_code: number | null;
  started_at: string | null;
  error: string | null;
  last_line: number | null;
  /** Step 3k: a server the studio did not start (state `external`). */
  address?: string | null;
  /** Step 3k: true while an attached app's first start installs its packages. */
  installing?: boolean;
}

export interface AppLogLine {
  n: number;
  text: string;
}

/** `GET /api/projects/{id}/app/logs?after=<n>`. */
export interface AppLogs {
  lines: AppLogLine[];
  last: number;
}

export type TemplateKind = "page" | "app" | "deck" | "workbook" | "document";

export interface Template {
  id: string;
  label: string;
  description: string;
  /** True when the template has a back end. */
  app: boolean;
  /** `studio` (built in) or `yours` (saved by the person). Absent on older servers: built in. */
  source?: "studio" | "yours" | string;
  kind?: TemplateKind | string;
  /** `copy`: the project starts as these files. `pattern`: the agent follows them; the page starts empty. */
  mode?: "copy" | "pattern" | string;
  created_at?: string;
  from_project?: string | null;
  files?: number;
  bytes?: number;
  /** A studio-origin URL of the preview image, or null. */
  preview?: string | null;
  main?: string | null;
  imported?: boolean;
  has_code?: boolean;
}

export interface ModelOption {
  id: string;
  label: string;
  /** Step 3n. Not a Claude model, reached through something that translates for it. */
  experimental?: boolean;
}

export interface Connection {
  id: string;
  label: string;
  kind: "claude-subscription" | "anthropic-gateway" | "litellm" | "fake" | string;
  available: boolean;
  reason: string | null;
  models: ModelOption[];
  default_model: string | null;
  /** Step 3n. Some model here is not Claude's: Claude models work best; these are experimental. */
  experimental?: boolean;
}

export interface Health {
  ok: boolean;
  version: string;
  fake: boolean;
  connections: Connection[];
  /** Voice input: whether this studio can turn a recording into text. */
  transcription?: { available: boolean; reason: string | null };
  /** Step 3m. `needed`: nothing is configured yet, so the first-run screen opens by itself. */
  setup?: { needed: boolean };
  /** Step 3m. Shared with other people: the Claude plan is switched off. */
  shared?: boolean;
}

export interface FileEntry {
  path: string;
  size: number;
  modified_at: string;
}

export interface FileContent {
  path: string;
  content: string;
  hash: string;
}

export interface FileChange {
  path: string;
  status: string;
}

export type VersionKind = "init" | "agent" | "user" | "restore";

export interface Version {
  sha: string;
  kind: VersionKind | string;
  message: string;
  created_at: string;
  files: FileChange[];
}

export interface VersionDiff {
  sha: string;
  diff: string;
  truncated: boolean;
}

export interface Selection {
  path: string;
  selector: string;
  tag: string;
  text: string;
  html: string;
  /** Where the app's framework says the element comes from (a component or a file); "" when unknown. */
  source?: string;
}

export type Mode = "browse" | "select" | "edit" | "draw";

// ---- Draw on the page (bridge `drawing`, `POST …/drawings`) ----------------------------

/** `pan` lets everything through (scroll and use the page); `move` drags an element to a new place. */
export type DrawTool = "pen" | "box" | "note" | "move" | "pan";

/** A mark the person drew. Only the fields the shell reads are typed; the rest travels as is. */
export interface DrawingMark {
  n: number;
  kind: "stroke" | "note" | string;
  color?: string;
  text?: string;
  [key: string]: unknown;
}

/** The bridge's Drawing: sent back to the server unchanged. */
export interface Drawing {
  path: string;
  viewport: { width: number; height: number };
  dark?: boolean;
  page?: { width: number; height: number };
  marks: DrawingMark[];
  [key: string]: unknown;
}

/** `POST /api/projects/{id}/drawings`. */
export interface DrawingSaved {
  path: string;
  images: string[];
  marks: number;
  rendered: boolean;
  note: string | null;
}

// ---- Events ---------------------------------------------------------------

interface EventBase {
  seq: number;
  ts: string;
  turn_id: string | null;
}

export type ToolKind = "read" | "edit" | "execute" | "search" | "fetch" | "agent" | "think" | "other";

export interface PlanEntry {
  content: string;
  status: "pending" | "in_progress" | "completed" | string;
}

export interface Usage {
  input_tokens: number | null;
  output_tokens: number | null;
  cost_usd: number | null;
  duration_ms: number | null;
  /**
   * Step 2. True: `cost_usd` is what the tokens would have cost at API prices
   * (subscription), not a charge. Absent on events written before Step 2.
   */
  cost_estimated?: boolean;
  /**
   * The conversation's running total after this turn, as the agent program reported it. When it is
   * present `cost_usd` is this turn's own part; turns written before it carry the running total in
   * `cost_usd` itself.
   */
  session_cost_usd?: number;
  /** A gateway turn whose price is still being read from the gateway's spend log. */
  cost_pending?: boolean;
  /** Where the figure is from: `reported` by the provider, `calculated` from a price list, or `plan`. */
  cost_basis?: string | null;
  /** Requests the route reported no price for. */
  unpriced_requests?: number;
  /** Where a gateway turn's price came from (`gateway`, `recovered`) and, when there is none, why. */
  cost_source?: string | null;
  cost_reason?: string | null;
}

export interface TurnStartedEvent extends EventBase {
  type: "turn_started";
  text: string;
  selection: Selection | null;
  connection: string | null;
  model: string | null;
  /** Step 2: workspace paths attached to the message. */
  attachments?: string[];
  /** Step 2: set when this turn was a queued message. */
  queued_id?: string | null;
  /** How much to do and whether to verify (absent in older logs = standard, off). */
  options?: { depth?: string; verify?: boolean; check_only?: boolean };
}
/** The result of a verification the person asked for (`report_verification`). */
export interface VerificationEvent extends EventBase {
  type: "verification";
  checked: number;
  passed: number;
  fixed: number;
  failed: number;
  unverifiable: number;
  summary?: string;
  findings: { what: string; where: string; status: string; note: string }[];
  findings_total?: number;
  findings_cut?: number;
}
/** Chips under a finished request (CONTRACT.md, "Suggested next steps"); a tap only fills the chat box. */
export interface NextStepsEvent extends EventBase {
  type: "next_steps";
  steps: { id: string; from: string; label: string; text: string; options?: Record<string, unknown> }[];
}
/** The studio's own page check, run once after a reported verification (never sends the agent back). */
export interface VerificationRecheckEvent extends EventBase {
  type: "verification_recheck";
  pages: {
    page: string;
    checked: number;
    verified: number;
    derived: number;
    failed: number;
    cited_not_listed: number;
    source_list: boolean;
    error: string | null;
  }[];
  problems: { page: string; what: string; where: string; why: string }[];
  problems_total: number;
  problems_cut: number;
  agrees: boolean;
}
/** Step 3o: what the studio picked from the library for this request. */
export interface LibraryPickedEvent extends EventBase, LibraryPick {
  type: "library_picked";
}
export interface MessageChunkEvent extends EventBase {
  type: "message_chunk";
  message_id: string;
  text: string;
}
export interface ThoughtChunkEvent extends EventBase {
  type: "thought_chunk";
  message_id: string;
  text: string;
}
export interface ToolCallEvent extends EventBase {
  type: "tool_call";
  tool_id: string;
  name: string;
  kind: ToolKind | string;
  title: string;
  input: unknown;
  parent_tool_id: string | null;
}
export interface ToolResultEvent extends EventBase {
  type: "tool_result";
  tool_id: string;
  status: "ok" | "error" | string;
  summary: string | null;
}
export interface PlanEvent extends EventBase {
  type: "plan";
  entries: PlanEntry[];
}
export interface FilesChangedEvent extends EventBase {
  type: "files_changed";
  paths: string[];
}
export interface CheckpointEvent extends EventBase {
  type: "checkpoint";
  sha: string;
  kind: VersionKind | string;
  message: string;
  files: FileChange[];
}
export interface TurnFinishedEvent extends EventBase {
  type: "turn_finished";
  status: "completed" | "stopped" | "failed" | string;
  error: string | null;
  usage: Partial<Usage> | null;
  session_id: string | null;
}
/** Written after `turn_finished`: what the gateway's own spend log says the turn cost. */
export interface TurnCostEvent extends EventBase {
  type: "turn_cost";
  cost_usd: number | null;
  requests: number;
  unpriced: number;
  source: "gateway" | "recovered" | "unavailable" | string;
  reason: string | null;
  basis?: string | null;
}
export interface NoticeEvent extends EventBase {
  type: "notice";
  level: "info" | "warning" | "error" | string;
  message: string;
}

export interface TurnQueuedEvent extends EventBase {
  type: "turn_queued";
  queued_id: string;
  text: string;
  selection: Selection | null;
  attachments: string[];
  connection: string | null;
  model: string | null;
}
export interface TurnUnqueuedEvent extends EventBase {
  type: "turn_unqueued";
  queued_id: string;
  reason: "cancelled" | "restart" | string;
}
export interface ApprovalRequestEvent extends EventBase {
  type: "approval_request";
  approval_id: string;
  tool: string;
  title: string;
  detail: string;
  reason: string;
}
export interface ApprovalResolvedEvent extends EventBase {
  type: "approval_resolved";
  approval_id: string;
  decision: "allow" | "deny" | string;
  by: "user" | "turn_end" | string;
}
export interface AppStatusEvent extends EventBase {
  type: "app_status";
  state: AppState | string;
  exit_code: number | null;
  run: string | null;
}

export type ShipStepName = "check" | "apply" | "after" | "address" | "back" | "back_after";

export interface ShipRecipeEvent extends EventBase {
  type: "ship_recipe";
  recipe: ShipRecipe;
  confirmed_by: string;
}
export interface ShipStartedEvent extends EventBase {
  type: "ship_started";
  ship_id: string;
  by: string;
  method: ShipMethod;
  base: string | null;
  head: string;
  files: FileChange[];
  files_more: number;
}
export interface ShipStepEvent extends EventBase {
  type: "ship_step";
  ship_id: string;
  step: ShipStepName;
  state: "running" | "ok" | "failed";
  index?: number;
  command?: string;
  exit_code?: number | null;
  output?: string;
  duration_ms?: number;
  lines?: string[];
  address?: string;
  attempts?: string[];
}
export interface ShipReadyEvent extends EventBase {
  type: "ship_ready";
  ship_id: string;
  card: ShipCard;
}
export interface ShipDecidedEvent extends EventBase {
  type: "ship_decided";
  ship_id: string;
  decision: "allow" | "deny";
}
export interface ShipFinishedEvent extends EventBase {
  type: "ship_finished";
  ship_id: string;
  result: ShipResult;
  message: string;
  merge_help: string | null;
}
export type ShipEvent =
  | ShipRecipeEvent
  | ShipStartedEvent
  | ShipStepEvent
  | ShipReadyEvent
  | ShipDecidedEvent
  | ShipFinishedEvent;

export type StudioEvent =
  | TurnStartedEvent
  | VerificationEvent
  | VerificationRecheckEvent
  | NextStepsEvent
  | LibraryPickedEvent
  | MessageChunkEvent
  | ThoughtChunkEvent
  | ToolCallEvent
  | ToolResultEvent
  | PlanEvent
  | FilesChangedEvent
  | CheckpointEvent
  | TurnFinishedEvent
  | TurnCostEvent
  | NoticeEvent
  | TurnQueuedEvent
  | TurnUnqueuedEvent
  | ApprovalRequestEvent
  | ApprovalResolvedEvent
  | AppStatusEvent
  | ShipEvent;

// ---- Bridge (preview iframe ⇄ shell) ---------------------------------------

export interface BridgeReady {
  source: "studio-bridge";
  type: "ready";
  path: string;
  title: string;
}
export interface BridgeSelect extends Selection {
  source: "studio-bridge";
  type: "select";
}
export interface BridgeTextEdit {
  source: "studio-bridge";
  type: "text-edit";
  path: string;
  selector: string;
  tag?: string;
  before_text: string;
  after_text: string;
}
export interface BridgeNavigate {
  source: "studio-bridge";
  type: "navigate";
  path: string;
}
export type BridgeMessage = BridgeReady | BridgeSelect | BridgeTextEdit | BridgeNavigate;

export interface TextEdit {
  path: string;
  selector: string;
  tag: string;
  before_text: string;
  after_text: string;
}

// ---- What the requests cost (CONTRACT.md, "Cost per request and per project") ----

export type CostKind = "charged" | "plan" | "unpriced" | "pending";

export interface CostTurn {
  turn_id: string;
  at: string;
  connection: string | null;
  model: string | null;
  kind: CostKind;
  /** Only for `charged` and `plan`. */
  cost_usd: number | null;
  /** `reported` by the provider, `calculated` from token counts and a price list, `plan`, or unknown. */
  basis: string | null;
  requests: number;
  unpriced_requests: number;
  source: string | null;
  reason: string | null;
}

export interface ProjectCost {
  charged_usd: number;
  /** The part of `charged_usd` that was calculated from a price list, not reported by the provider. */
  calculated_usd: number;
  plan_equivalent_usd: number;
  unpriced_turns: number;
  unpriced_requests: number;
  pending_turns: number;
  turns: CostTurn[];
}

export interface CostRecovery {
  recovered: number;
  unmatched: number;
  reason: string | null;
}
