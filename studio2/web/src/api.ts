import type { Example } from "./examples";
import type { TurnOptions } from "./effort";
import type {
  AppInfo,
  AttachRequest,
  CostRecovery,
  Inspection,
  AppLogs,
  Approvals,
  DataSource,
  DataSourceInput,
  DataSourceTestResult,
  Library,
  LibraryIndex,
  LibraryItemKind,
  LibraryItemsPage,
  LibraryPrefs,
  LibrarySkill,
  MachinePref,
  ProjectChoice,
  McpServer,
  McpServerInput,
  McpTestResult,
  Drawing,
  DrawingSaved,
  ProjectDataSource,
  ProjectLibrary,
  FileContent,
  FileEntry,
  Health,
  Project,
  ProjectCost,
  Selection,
  ShipRecipe,
  ShipRecord,
  ShipView,
  Template,
  Version,
  VersionDiff,
} from "./types";

/**
 * The `checkpoint` in a write response: the version that the write created, or
 * null when nothing changed. Same shape as an entry of `GET …/versions`.
 */
export type Checkpoint = Version;

/** What `GET /api/library/items` is asked for. */
export interface LibraryItemsQuery {
  kind: LibraryItemKind;
  q?: string;
  source?: string | null;
  state?: string | null;
  offset?: number;
  limit?: number;
  project?: string | null;
}

/** The query string of `GET /api/library/items` (empty values left out). */
export function libraryItemsSearch(query: LibraryItemsQuery): string {
  const params = new URLSearchParams({ kind: query.kind });
  const q = query.q?.trim();
  if (q) params.set("q", q);
  if (query.source) params.set("source", query.source);
  if (query.state) params.set("state", query.state);
  if (query.offset) params.set("offset", String(query.offset));
  if (query.limit) params.set("limit", String(query.limit));
  if (query.project) params.set("project", query.project);
  return params.toString();
}

/** An error from the studio API, carrying the contract's `error.code` and `error.message`. */
export class ApiError extends Error {
  status: number;
  code: string;
  constructor(status: number, code: string, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
  }
}

/** Human sentence for any thrown value, preferring the contract's message. */
export function errorMessage(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  if (error instanceof Error && error.message) return error.message;
  return "Something went wrong.";
}

export async function request<T>(method: string, path: string, body?: unknown, signal?: AbortSignal): Promise<T> {
  const init: RequestInit = { method, signal, headers: { Accept: "application/json" } };
  if (body instanceof FormData) {
    init.body = body;
  } else if (body !== undefined) {
    init.body = JSON.stringify(body);
    (init.headers as Record<string, string>)["Content-Type"] = "application/json";
  }
  let response: Response;
  try {
    response = await fetch(`/api${path}`, init);
  } catch (cause) {
    if ((cause as Error)?.name === "AbortError") throw cause;
    throw new ApiError(0, "network_error", "Could not reach the studio server.");
  }
  if (response.status === 204) return undefined as T;
  const text = await response.text();
  let data: unknown = null;
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      data = null;
    }
  }
  if (!response.ok) {
    const err = (data as { error?: { code?: string; message?: string } } | null)?.error;
    throw new ApiError(
      response.status,
      err?.code ?? `http_${response.status}`,
      err?.message ?? `The studio server answered with an error (${response.status}).`,
    );
  }
  return data as T;
}

const enc = encodeURIComponent;

export interface TurnInput {
  text: string;
  selection?: Selection | null;
  connection?: string | null;
  model?: string | null;
  /** Workspace paths, uploaded first (normally with `dir=attachments`). */
  attachments?: string[];
  /** Wait behind the running turn instead of failing with 409 `turn_running`. */
  queue?: boolean;
  /** How much to do and whether to verify; left out = standard, off. */
  options?: TurnOptions;
}

/** `POST …/turns` answers with `turn_id` when the turn started, `queued_id` when it is waiting. */
export interface TurnAccepted {
  turn_id?: string;
  queued_id?: string;
}

export type AppAction = "start" | "stop" | "restart";

export const api = {
  health: (signal?: AbortSignal) => request<Health>("GET", "/health", undefined, signal),

  listProjects: () => request<Project[]>("GET", "/projects"),
  createProject: (options: { name?: string; template?: string; example?: string } = {}) => {
    const body: Record<string, string> = {};
    if (options.name) body.name = options.name;
    if (options.template) body.template = options.template;
    if (options.example) body.example = options.example;
    return request<Project>("POST", "/projects", body);
  },
  listExamples: () => request<Example[]>("GET", "/examples"),
  getShip: (id: string) => request<ShipView>("GET", `/projects/${enc(id)}/ship`),
  setShipRecipe: (id: string, recipe: ShipRecipe) =>
    request<ShipView>("PUT", `/projects/${enc(id)}/ship/recipe`, { recipe }),
  startShip: (id: string) => request<{ ship_id: string }>("POST", `/projects/${enc(id)}/ships`),
  listShips: (id: string) => request<ShipRecord[]>("GET", `/projects/${enc(id)}/ships`),
  getShipRecord: (id: string, shipId: string) =>
    request<ShipRecord>("GET", `/projects/${enc(id)}/ships/${enc(shipId)}`),
  decideShip: (id: string, shipId: string, decision: "allow" | "deny", confirm = "") =>
    request<void>("POST", `/projects/${enc(id)}/ships/${enc(shipId)}/decision`, { decision, confirm }),
  updateCopy: (id: string) =>
    request<{ updated: boolean; message: string }>("POST", `/projects/${enc(id)}/ship/update-copy`),
  inspectFolder: (path: string, signal?: AbortSignal) => request<Inspection>("POST", "/apps/inspect", { path }, signal),
  attachProject: (body: AttachRequest) => request<Project>("POST", "/projects/attach", body),
  listTemplates: (signal?: AbortSignal) => request<Template[]>("GET", "/templates", undefined, signal),
  saveTemplate: (id: string, body: { name: string; description?: string; kind?: string; file?: string }) =>
    request<Template>("POST", `/projects/${enc(id)}/template`, body),
  patchTemplate: (templateId: string, patch: { label?: string; description?: string }) =>
    request<Template>("PATCH", `/templates/${enc(templateId)}`, patch),
  deleteTemplate: (templateId: string) => request<void>("DELETE", `/templates/${enc(templateId)}`),
  templateExportUrl: (templateId: string) => `/api/templates/${enc(templateId)}/export`,
  importTemplate: (file: File, confirm: boolean) => {
    const form = new FormData();
    form.append("file", file, file.name);
    if (confirm) form.append("confirm", "1");
    return request<Template>("POST", "/templates/import", form);
  },
  useTemplate: (id: string, templateId: string) =>
    request<Project>("PUT", `/projects/${enc(id)}/uses-template`, { template: templateId }),
  stopUsingTemplate: (id: string) => request<Project>("DELETE", `/projects/${enc(id)}/uses-template`),
  getProject: (id: string) => request<Project>("GET", `/projects/${enc(id)}`),
  getCost: (id: string, signal?: AbortSignal) => request<ProjectCost>("GET", `/projects/${enc(id)}/cost`, undefined, signal),
  recoverCost: (id: string) => request<CostRecovery>("POST", `/projects/${enc(id)}/cost/recover`),
  patchProject: (
    id: string,
    patch: {
      name?: string;
      connection?: string;
      model?: string | null;
      approvals?: Approvals;
      name_suggestion?: null;
      turn_options?: TurnOptions;
    },
  ) =>
    request<Project>("PATCH", `/projects/${enc(id)}`, patch),
  deleteProject: (id: string) => request<void>("DELETE", `/projects/${enc(id)}`),

  listFiles: (id: string) => request<FileEntry[]>("GET", `/projects/${enc(id)}/files`),
  getFile: (id: string, path: string) => request<FileContent>("GET", `/projects/${enc(id)}/file?path=${enc(path)}`),
  putFile: (id: string, path: string, content: string, baseHash?: string) =>
    request<{ path: string; hash: string; checkpoint: Checkpoint | null }>(
      "PUT",
      `/projects/${enc(id)}/file`,
      baseHash ? { path, content, base_hash: baseHash } : { path, content },
    ),
  upload: (id: string, file: File, dir?: string) => {
    const form = new FormData();
    form.append("file", file, file.name);
    if (dir) form.append("dir", dir);
    return request<{ path: string; size: number; checkpoint: Checkpoint | null }>(
      "POST",
      `/projects/${enc(id)}/upload`,
      form,
    );
  },
  transcribe: (recording: Blob, filename: string, language?: string) => {
    const form = new FormData();
    form.append("file", recording, filename);
    if (language) form.append("language", language);
    return request<{ text: string; seconds: number }>("POST", "/transcribe", form);
  },
  domEdit: (id: string, edit: { path: string; tag?: string; before_text: string; after_text: string }) =>
    request<{ path: string; checkpoint: Checkpoint | null }>("POST", `/projects/${enc(id)}/dom-edits`, edit),

  listVersions: (id: string) => request<Version[]>("GET", `/projects/${enc(id)}/versions`),
  getDiff: (id: string, sha: string) => request<VersionDiff>("GET", `/projects/${enc(id)}/versions/${enc(sha)}/diff`),
  restore: (id: string, sha: string) =>
    request<{ checkpoint: Checkpoint | null }>("POST", `/projects/${enc(id)}/restore`, { sha }),

  startTurn: (id: string, input: TurnInput) => {
    const body: Record<string, unknown> = { text: input.text };
    if (input.selection) body.selection = input.selection;
    if (input.connection) body.connection = input.connection;
    if (input.model) body.model = input.model;
    if (input.attachments?.length) body.attachments = input.attachments;
    if (input.queue) body.queue = true;
    if (input.options && (input.options.depth !== "standard" || input.options.verify)) body.options = input.options;
    return request<TurnAccepted>("POST", `/projects/${enc(id)}/turns`, body);
  },
  unqueue: (id: string, queuedId: string) => request<void>("DELETE", `/projects/${enc(id)}/queue/${enc(queuedId)}`),
  resolveApproval: (id: string, turnId: string, approvalId: string, decision: "allow" | "deny") =>
    request<void>("POST", `/projects/${enc(id)}/turns/${enc(turnId)}/approvals/${enc(approvalId)}`, { decision }),

  getApp: (id: string, signal?: AbortSignal) => request<AppInfo>("GET", `/projects/${enc(id)}/app`, undefined, signal),
  appAction: (id: string, action: AppAction) => request<AppInfo>("POST", `/projects/${enc(id)}/app/${action}`),
  appLogs: (id: string, after: number, signal?: AbortSignal) =>
    request<AppLogs>("GET", `/projects/${enc(id)}/app/logs?after=${after}`, undefined, signal),

  downloadUrl: (id: string) => `/api/projects/${enc(id)}/download`,

  saveDrawing: (id: string, drawing: Drawing) => request<DrawingSaved>("POST", `/projects/${enc(id)}/drawings`, drawing),

  getLibrary: (signal?: AbortSignal) => request<Library>("GET", "/library", undefined, signal),

  createSkill: (input: { name: string; description: string; body: string }) =>
    request<LibrarySkill>("POST", "/skills", input),
  deleteSkill: (name: string) => request<void>("DELETE", `/skills/${enc(name)}`),
  createMcpServer: (input: McpServerInput) => request<McpServer>("POST", "/mcp-servers", input),
  patchMcpServer: (mcpId: string, patch: Partial<McpServerInput>) =>
    request<McpServer>("PATCH", `/mcp-servers/${enc(mcpId)}`, patch),
  deleteMcpServer: (mcpId: string) => request<void>("DELETE", `/mcp-servers/${enc(mcpId)}`),
  testMcpServer: (mcpId: string) => request<McpTestResult>("POST", `/mcp-servers/${enc(mcpId)}/test`),
  getProjectLibrary: (id: string, signal?: AbortSignal) =>
    request<ProjectLibrary>("GET", `/projects/${enc(id)}/library`, undefined, signal),
  putProjectLibrary: (
    id: string,
    change: { skills?: Record<string, ProjectChoice>; mcp_servers?: Record<string, ProjectChoice> },
  ) => request<ProjectLibrary>("PUT", `/projects/${enc(id)}/library`, change),
  libraryItems: (query: LibraryItemsQuery, signal?: AbortSignal) =>
    request<LibraryItemsPage>("GET", `/library/items?${libraryItemsSearch(query)}`, undefined, signal),
  putLibraryPrefs: (change: { skills?: Record<string, MachinePref>; mcp_servers?: Record<string, MachinePref> }) =>
    request<LibraryPrefs>("PUT", "/library/prefs", change),
  refreshLibrary: () => request<LibraryIndex>("POST", "/library/refresh"),

  listDataSources: (signal?: AbortSignal) => request<DataSource[]>("GET", "/data-sources", undefined, signal),
  createDataSource: (input: DataSourceInput) => request<DataSource>("POST", "/data-sources", input),
  patchDataSource: (dsId: string, patch: Partial<DataSourceInput>) =>
    request<DataSource>("PATCH", `/data-sources/${enc(dsId)}`, patch),
  deleteDataSource: (dsId: string) => request<void>("DELETE", `/data-sources/${enc(dsId)}`),
  testDataSource: (dsId: string) => request<DataSourceTestResult>("POST", `/data-sources/${enc(dsId)}/test`),
  testDataSourceDraft: (input: DataSourceInput) => request<DataSourceTestResult>("POST", "/data-sources/test", input),
  listProjectDataSources: (id: string, signal?: AbortSignal) =>
    request<ProjectDataSource[]>("GET", `/projects/${enc(id)}/data-sources`, undefined, signal),
  attachDataSource: (id: string, dsId: string) =>
    request<void>("PUT", `/projects/${enc(id)}/data-sources/${enc(dsId)}`),
  detachDataSource: (id: string, dsId: string) =>
    request<void>("DELETE", `/projects/${enc(id)}/data-sources/${enc(dsId)}`),
  stopTurn: (id: string, turnId: string) => request<unknown>("POST", `/projects/${enc(id)}/turns/${enc(turnId)}/stop`),

  eventsUrl: (id: string, after = 0) => `/api/projects/${enc(id)}/events?after=${after}`,
};
