import type { LibraryPlacesInfo } from "./libraryPlaces";
import type { ServiceKey } from "./serviceKeys";
import type { ExportOptions } from "./exportInfo";
import type { Example } from "./examples";
import type { TurnOptions } from "./effort";
import type { SkillImportOptions } from "./skillMaking";
import type {
  AppInfo,
  AttachRequest,
  CostRecovery,
  Inspection,
  AppLogs,
  Approvals,
  ContextInspection,
  ContextItem,
  ContextItemWithState,
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
  McpSuggestAnswer,
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
  ProjectFind,
  StudioFind,
  Selection,
  ShipRecipe,
  ShipRecord,
  ShipView,
  SkillAskAccepted,
  SkillDetail,
  SkillFileContent,
  SkillFull,
  SkillImportPreview,
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

/** The query string of `POST /api/skills/import` (what is not asked for is left out). */
export function skillImportSearch(options: SkillImportOptions & { dryRun?: boolean }): string {
  const params = new URLSearchParams();
  if (options.dryRun) params.set("dry_run", "1");
  if (options.replace) params.set("replace", "1");
  if (options.allowScripts) params.set("allow_scripts", "1");
  if (options.name) params.set("name", options.name);
  const query = params.toString();
  return query ? `?${query}` : "";
}

/** An error from the studio API, carrying the contract's `error.code` and `error.message`. */
export class ApiError extends Error {
  status: number;
  code: string;
  /** The contract's `error.details`, when the answer carries any (an import's preview on a 409). */
  details?: unknown;
  constructor(status: number, code: string, message: string, details?: unknown) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    if (details !== undefined) this.details = details;
  }
}

/** Human sentence for any thrown value, preferring the contract's message. */
export function errorMessage(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  if (error instanceof Error && error.message) return error.message;
  return "Something went wrong.";
}

let accessDenied: (() => void) | null = null;

/**
 * What to do when any call is answered 401 `access_denied`: the access cookie expired or the key
 * changed in the middle of a session (CONTRACT.md, "Access from other devices"). Set once at start
 * (`main.tsx`: go to the sign-in page); null clears it.
 */
export function whenAccessDenied(handler: (() => void) | null): void {
  accessDenied = handler;
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
    const err = (data as { error?: { code?: string; message?: string; details?: unknown } } | null)?.error;
    if (response.status === 401 && err?.code === "access_denied") accessDenied?.();
    throw new ApiError(
      response.status,
      err?.code ?? `http_${response.status}`,
      err?.message ?? `The studio server answered with an error (${response.status}).`,
      err?.details,
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
  /** Words in every project the caller sees: names, pages and chat (CONTRACT.md, "Find words in a project's pages and across projects"). */
  findEverywhere: (q: string, signal?: AbortSignal) =>
    request<StudioFind>("GET", `/find?q=${enc(q)}`, undefined, signal),
  /** Words in one project's pages and notes. */
  findInProject: (id: string, q: string, signal?: AbortSignal) =>
    request<ProjectFind>("GET", `/projects/${enc(id)}/find?q=${enc(q)}`, undefined, signal),
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
  /** The person's instructions for every project. Refused on a shared studio; absent on an older server. */
  everywhereInstructions: (signal?: AbortSignal) => request<{ text: string }>("GET", "/instructions", undefined, signal),
  saveEverywhereInstructions: (text: string) => request<{ text: string }>("PUT", "/instructions", { text }),
  patchProject: (
    id: string,
    patch: {
      name?: string;
      connection?: string;
      model?: string | null;
      approvals?: Approvals;
      name_suggestion?: null;
      turn_options?: TurnOptions;
      signed_in_looks?: boolean;
      instructions?: string;
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
    if (input.options && (input.options.depth !== "standard" || input.options.verify || input.options.involve))
      body.options = input.options;
    return request<TurnAccepted>("POST", `/projects/${enc(id)}/turns`, body);
  },
  unqueue: (id: string, queuedId: string) => request<void>("DELETE", `/projects/${enc(id)}/queue/${enc(queuedId)}`),
  resolveApproval: (id: string, turnId: string, approvalId: string, decision: "allow" | "deny") =>
    request<void>("POST", `/projects/${enc(id)}/turns/${enc(turnId)}/approvals/${enc(approvalId)}`, { decision }),
  /** The person's answers to the agent's questions, or `{skip: true}`. */
  answerQuestion: (
    id: string,
    turnId: string,
    questionId: string,
    body: { answers: { id: string; chosen: string[]; text: string }[] } | { skip: true },
  ) => request<void>("POST", `/projects/${enc(id)}/turns/${enc(turnId)}/questions/${enc(questionId)}`, body),

  getApp: (id: string, signal?: AbortSignal) => request<AppInfo>("GET", `/projects/${enc(id)}/app`, undefined, signal),
  appAction: (id: string, action: AppAction) => request<AppInfo>("POST", `/projects/${enc(id)}/app/${action}`),
  appLogs: (id: string, after: number, signal?: AbortSignal) =>
    request<AppLogs>("GET", `/projects/${enc(id)}/app/logs?after=${after}`, undefined, signal),

  downloadUrl: (id: string) => `/api/projects/${enc(id)}/download`,
  libraryPlaces: () => request<LibraryPlacesInfo>("GET", "/library/places"),
  saveLibraryPlaces: (body: { skill_dirs?: string[]; connector_files?: string[] }) =>
    request<LibraryPlacesInfo>("PUT", "/library/places", body),
  listServices: () => request<ServiceKey[]>("GET", "/services"),
  saveServiceKey: (name: string, key: string) => request<ServiceKey>("PUT", `/services/${enc(name)}`, { key }),
  removeServiceKey: (name: string) => request<ServiceKey>("DELETE", `/services/${enc(name)}`),
  exportOptions: (id: string) => request<ExportOptions>("GET", `/projects/${enc(id)}/export`),
  exportPageUrl: (id: string) => `/api/projects/${enc(id)}/export/page`,
  exportFolderUrl: (id: string) => `/api/projects/${enc(id)}/export/folder`,

  saveDrawing: (id: string, drawing: Drawing) => request<DrawingSaved>("POST", `/projects/${enc(id)}/drawings`, drawing),

  getLibrary: (signal?: AbortSignal) => request<Library>("GET", "/library", undefined, signal),

  createSkill: (input: { name: string; description: string; body: string }) =>
    request<LibrarySkill>("POST", "/skills", input),
  deleteSkill: (name: string) => request<void>("DELETE", `/skills/${enc(name)}`),
  getSkill: (name: string, signal?: AbortSignal) => request<SkillDetail>("GET", `/skills/${enc(name)}`, undefined, signal),
  updateSkill: (name: string, change: { description?: string; body?: string }) =>
    request<SkillDetail>("PUT", `/skills/${enc(name)}`, change),
  skillExportUrl: (id: string) => `/api/library/skills/export?id=${enc(id)}`,
  skillDetail: (id: string, signal?: AbortSignal) =>
    request<SkillFull>("GET", `/library/skills/detail?id=${enc(id)}`, undefined, signal),
  skillFile: (id: string, path: string, signal?: AbortSignal) =>
    request<SkillFileContent>("GET", `/library/skills/file?id=${enc(id)}&path=${enc(path)}`, undefined, signal),
  /** A picture of a skill, as the picture itself (only pictures are served this way). */
  skillFileRawUrl: (id: string, path: string) => `/api/library/skills/file?id=${enc(id)}&path=${enc(path)}&raw=1`,
  copySkill: (input: { from: string; name: string; allow_scripts?: boolean; replace?: boolean }) =>
    request<LibrarySkill>("POST", "/skills/copy", input),
  askAboutSkill: (input: { id: string; about: "explain" | "change"; text?: string }) =>
    request<SkillAskAccepted>("POST", "/library/skills/ask", input),
  previewSkillImport: (file: File) => {
    const form = new FormData();
    form.append("file", file, file.name);
    return request<SkillImportPreview>("POST", `/skills/import${skillImportSearch({ dryRun: true })}`, form);
  },
  importSkill: (file: File, options: SkillImportOptions) => {
    const form = new FormData();
    form.append("file", file, file.name);
    return request<LibrarySkill>("POST", `/skills/import${skillImportSearch(options)}`, form);
  },
  createMcpServer: (input: McpServerInput) => request<McpServer>("POST", "/mcp-servers", input),
  suggestMcpServers: (input: { text: string; connection?: string }, signal?: AbortSignal) =>
    request<McpSuggestAnswer>("POST", "/mcp-servers/suggest", input.connection ? input : { text: input.text }, signal),
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
  inspectContext: (value: string, signal?: AbortSignal) =>
    request<ContextInspection>("POST", "/context/inspect", { value }, signal),
  listProjectContext: (id: string, signal?: AbortSignal) =>
    request<ContextItemWithState[]>("GET", `/projects/${enc(id)}/context`, undefined, signal),
  addProjectContext: (id: string, body: { value: string; label?: string; note?: string }) =>
    request<ContextItem>("POST", `/projects/${enc(id)}/context`, body),
  updateProjectContext: (id: string, itemId: string, patch: { label?: string; note?: string }) =>
    request<ContextItem>("PATCH", `/projects/${enc(id)}/context/${enc(itemId)}`, patch),
  removeProjectContext: (id: string, itemId: string) =>
    request<void>("DELETE", `/projects/${enc(id)}/context/${enc(itemId)}`),
  stopTurn: (id: string, turnId: string) => request<unknown>("POST", `/projects/${enc(id)}/turns/${enc(turnId)}/stop`),

  eventsUrl: (id: string, after = 0) => `/api/projects/${enc(id)}/events?after=${after}`,
};
