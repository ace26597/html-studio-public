#!/usr/bin/env node
// ─────────────────────────────────────────────────────────────────────────────
// MOCK SERVER — a development tool for the Studio 2 web UI. NOT the product.
//
// It keeps everything in memory and implements just enough of
// studio2/CONTRACT.md for the UI to be built and checked without the real
// Python server: projects, files, versions/restore, scripted turns over
// server-sent events, Stop, and one preview port per project with a bridge.
// No model is ever called; the "agent" is a script.
//
//   node mock/server.mjs [--seed]
//
//   MOCK_PORT           API + built UI port          (default 8200)
//   MOCK_PREVIEW_PORT   first preview port            (default 8291)
//   MOCK_SPEED          delay multiplier, 0 = instant (default 1)
//
// Words in a message that change the script: "fail" (the turn fails),
// "research" (adds a sub-agent), a "quoted phrase" with a selection (replaces
// the selected element's text), "risky" (asks for approval before a sudo
// command), "backend" (writes a studio.json, so the project gets an app; a run
// command containing "crash" makes that app exit with code 1), "check" (runs a
// scripted mcp__studio__check_page with made-up counts), "render" (a scripted
// mcp__studio__render_document that writes a stand-in preview for files/notes.docx),
// "save this as a template" (a scripted mcp__studio__save_template).
//
// Step 2 in the mock: templates, a SIMULATED app (no process is started: the
// state machine and log lines are scripted, and the project URL keeps serving
// static files), queued messages, approvals, attachments and zip download.
//
// Step 2b in the mock: a library whose entries are all labelled "(mock)", and
// data sources kept in memory. Passwords are never stored, only "has_password".
// "Test connection" is scripted: a host containing "fail" fails, MySQL reports
// its driver as not installed, anything else "connects" without connecting.
// ─────────────────────────────────────────────────────────────────────────────

import crypto from "node:crypto";
import fs from "node:fs";
import http from "node:http";
import path from "node:path";
import { fileURLToPath } from "node:url";
import os from "node:os";

const HOST = "127.0.0.1";
const API_PORT = Number(process.env.MOCK_PORT ?? 8200);
const PREVIEW_BASE = Number(process.env.MOCK_PREVIEW_PORT ?? 8291);
const SPEED = Number(process.env.MOCK_SPEED ?? 1);
const DIST = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../dist");
const ALLOWED_ORIGINS = new Set(
  [API_PORT, 5200].flatMap((port) => [`http://127.0.0.1:${port}`, `http://localhost:${port}`]),
);

const projects = new Map();
let nextPreviewPort = PREVIEW_BASE;

const now = () => new Date().toISOString();
const sha256 = (data) => crypto.createHash("sha256").update(data).digest("hex");
const escapeHtml = (text) =>
  String(text).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");

const BLANK_PAGE = `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Untitled</title>
</head>
<body>
</body>
</html>
`;

class HttpError extends Error {
  constructor(status, code, message, details) {
    super(message);
    this.status = status;
    this.code = code;
    this.details = details;
  }
}

// ---- projects, files, versions ------------------------------------------------

// ---- step 2b: library and data sources (mock) --------------------------------------------

const DATA_KINDS = [
  { kind: "postgres", label: "PostgreSQL", default_port: 5432, available: true, note: null },
  { kind: "redshift", label: "Amazon Redshift", default_port: 5439, available: true, note: null },
  { kind: "mysql", label: "MySQL", default_port: 3306, available: false, note: "(mock) The MySQL driver is not installed." },
];
const SSL = ["disable", "prefer", "require", "verify-full"];
const dataSources = new Map();
let libraryObservedAt = null; // set once the first scripted turn has run

function library() {
  const observed = libraryObservedAt !== null;
  return {
    connections: health().connections,
    tools: [
      { id: "files", label: "Files (mock)", description: "Read and write files in the project.", available: true, note: null },
      { id: "shell", label: "Shell (mock)", description: "Run commands in the project folder.", available: true, note: null },
      { id: "web_search", label: "Web search (mock)", description: "Search the web.", available: true, note: "(mock) Only on the Claude subscription connection." },
      { id: "look", label: "Look at the page (mock)", description: "Screenshot the page in a headless browser.", available: false, note: "(mock) No cached browser on this machine." },
      { id: "databases", label: "Databases (mock)", description: "Read-only queries on attached data sources.", available: true, note: null },
      { id: "motion", label: "Motion and 3D pages (mock)", description: "Animated, 3D and navigable graphics as ordinary pages, and pictures of a page.", available: true, note: null },
      { id: "video", label: "Video from a page (mock)", description: "Turn a page into an MP4, WebM or GIF file in media/.", available: false, note: "(mock) No ffmpeg found; set STUDIO2_FFMPEG or install ffmpeg." },
    ],
    skills: [...allSkills(), ...(observed ? BUILT_IN_SKILLS : [])],
    agents: [{ name: "mock-researcher", description: "(mock) A sub-agent from library/agents/." }],
    studio_tools: [
      { name: "search_web", description: "(mock) Keyless web search.", available: true, note: null },
      { name: "fetch_source", description: "(mock) Download a page or PDF and save it as a source.", available: true, note: null },
      { name: "read_source", description: "(mock) Read a saved source.", available: true, note: null },
      { name: "query_files", description: "(mock) Read-only DuckDB over project files.", available: true, note: null },
      { name: "look_at_page", description: "(mock) Screenshot the page.", available: false, note: "(mock) No cached browser on this machine." },
      { name: "check_page", description: "(mock) Check citations and numbers on a page.", available: true, note: null },
    ],
    mcp_servers: allServers(),
    templates: allTemplates(),
    data_kinds: DATA_KINDS,
    observed_at: libraryObservedAt,
  };
}

// ---- step 3: skills and MCP servers (mock) -------------------------------------------------

const STUDIO_SKILLS = [
  { id: "studio:mock-deep-research", name: "mock-deep-research", label: "Mock deep research", description: "(mock) A skill shipped in library/skills/.", source: "studio", default: true },
  { id: "studio:mock-dashboard", name: "mock-dashboard", label: "Mock dashboard", description: "(mock) Another shipped skill.", source: "studio", default: true },
];
// A long list on purpose, like a real ~/.claude/skills folder, to exercise search.
const MACHINE_SKILLS = Array.from({ length: 110 }, (_, i) => {
  const n = String(i + 1).padStart(3, "0");
  return { id: `machine:mock-machine-${n}`, name: `mock-machine-${n}`, label: `mock-machine-${n}`, description: `(mock) Machine skill number ${n}.`, source: "machine", default: false };
});
const BUILT_IN_SKILLS = [{ id: "built-in:mock-built-in", name: "mock-built-in", label: "mock-built-in", description: "", source: "built-in", default: true }];
const yourSkills = new Map(); // name -> skill
const NAME_RE = /^[a-z0-9_-]+$/;

function allSkills() {
  return [...STUDIO_SKILLS, ...yourSkills.values(), ...MACHINE_SKILLS];
}

// ---- reading any skill (mock): stand-in folders, every one marked "(mock)" -----------------------

const yourSkillFolders = new Map(); // name -> [{path, text?, data?, script}]
// A 1×1 PNG, so that a picture can be shown.
const MOCK_PNG = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==", "base64");
const SKILL_USE = {
  "studio:mock-deep-research": { picked: 6, used: 4, last_used: new Date(Date.now() - 2 * 864e5).toISOString() },
  "studio:mock-dashboard": { picked: 2, used: 0, last_used: null },
};

function skillMarkdown(skill, body) {
  return `---\nname: ${skill.name}\ndescription: ${skill.description}\nlicense: (mock) MIT\n---\n${body}`;
}

function mockSkillFolder(skill) {
  if (skill.source === "yours") return yourSkillFolders.get(skill.name) ?? [{ path: "SKILL.md", text: skillMarkdown(skill, "(mock) No instructions.\n"), script: false }];
  const body = `# ${skill.label}\n\n(mock) Stand-in instructions for ${skill.name}.\n\n1. Read reference/notes.md.\n2. Do the job.\n3. Check the result before finishing.\n`;
  const files = [
    { path: "SKILL.md", text: skillMarkdown(skill, body), script: false },
    { path: "reference/notes.md", text: "(mock) Notes the skill reads.\n\n<b>This tag is shown as text, never drawn.</b>\n", script: false },
  ];
  if (skill.id === "studio:mock-deep-research") {
    files.push(
      { path: "scripts/fetch_sources.py", text: "# (mock) A script the agent may run.\nprint('fetching sources')\n", script: true },
      { path: "assets/flow.png", data: MOCK_PNG, script: false },
      { path: "reference/sources.pdf", data: Buffer.from("%PDF-1.4 (mock)"), script: false },
    );
  }
  return files;
}

const SKILL_TEXT_EXT = /\.(md|txt|html|htm|css|json|csv|tsv|xml|yaml|yml|toml|ini|cfg|svg|py|js|mjs|cjs|ts|sh|bash|zsh|rb|pl|php|ps1|bat|cmd)$/i;
const SKILL_PICTURE_EXT = /\.(png|jpg|jpeg|gif|webp)$/i;

function skillFileKind(path) {
  if (SKILL_PICTURE_EXT.test(path)) return "picture";
  return SKILL_TEXT_EXT.test(path) ? "text" : "other";
}

function skillFileSize(file) {
  return file.data ? file.data.length : Buffer.byteLength(file.text ?? "");
}

function skillFileInfo(file) {
  return { path: file.path, size: skillFileSize(file), kind: skillFileKind(file.path), script: Boolean(file.script) };
}

function indexedSkill(id) {
  const skill = allSkills().find((item) => item.id === id);
  if (!skill) throw new HttpError(404, "unknown_skill", "There is no skill with that id in the library.");
  return skill;
}

function skillFull(skill) {
  const folder = mockSkillFolder(skill);
  const files = folder.map(skillFileInfo).sort((a, b) => (a.path === "SKILL.md" ? -1 : b.path === "SKILL.md" ? 1 : a.path < b.path ? -1 : 1));
  const markdown = folder.find((file) => file.path === "SKILL.md")?.text ?? "";
  const match = /^---\n([\s\S]*?)\n---\n?/.exec(markdown);
  const front = {};
  for (const line of (match?.[1] ?? "").split("\n")) {
    const at = line.indexOf(":");
    const key = line.slice(0, at).trim();
    if (at > 0 && key !== "name" && key !== "description") front[key] = line.slice(at + 1).trim();
  }
  const use = SKILL_USE[skill.id] ?? { picked: 0, used: 0, last_used: null };
  return {
    ...skill,
    enabled: true,
    body: match ? markdown.slice(match[0].length) : markdown,
    body_cut: false,
    front,
    files,
    scripts: files.filter((file) => file.script).map((file) => file.path),
    size: files.reduce((sum, file) => sum + file.size, 0),
    yours: skill.source === "yours",
    ...use,
  };
}

const MOCK_TOOLS = [
  { name: "search_docs", description: "(mock) Search the documentation." },
  { name: "read_page", description: "(mock) Read one documentation page." },
  { name: "list_topics", description: "(mock) List the topics." },
];
const MACHINE_SERVERS = [
  { id: "machine:mock-local-files", name: "mock-local-files", label: null, transport: "stdio", url: null, command: "npx", args: ["-y", "mock-files-server@1.0.0"], secret_names: [], source: "machine", description: "(mock) Read from ~/.claude.json.", last_test: null },
];
const SUGGESTED_SERVERS = [
  { id: "suggested:mock-docs", name: "mock-docs", label: "Mock public docs", transport: "http", url: "https://mock-docs.example/mcp", command: null, args: [], secret_names: [], source: "suggested", description: "(mock) A public server that needs no account.", last_test: null },
  { id: "suggested:mock-broken", name: "mock-broken", label: "Mock broken server", transport: "http", url: "https://fail.example/mcp", command: null, args: [], secret_names: [], source: "suggested", description: "(mock) Its test always fails.", last_test: null },
];
const yourServers = new Map(); // id -> server (secret values are never kept, only names)

function allServers() {
  return [...yourServers.values(), ...MACHINE_SERVERS, ...SUGGESTED_SERVERS];
}

function readServerFields(body, current = null) {
  const out = current ? { ...current } : { args: [], secret_names: [], label: null, description: null, url: null, command: null };
  const fail = (message) => {
    throw new HttpError(400, "invalid_request", message);
  };
  if ("name" in body || !current) {
    const name = typeof body.name === "string" ? body.name.trim() : "";
    if (!name || name.length > 40 || !NAME_RE.test(name)) fail("name: lower-case letters, digits, - and _, 1–40 characters.");
    if (name === "studio") fail("name: “studio” is reserved.");
    if (allServers().some((item) => item.name === name && item.id !== out.id)) throw new HttpError(409, "name_taken", "A server with that name already exists.");
    out.name = name;
  }
  if ("transport" in body || !current) {
    if (body.transport !== "http" && body.transport !== "stdio") fail("transport: must be http or stdio.");
    out.transport = body.transport;
  }
  if ("label" in body) out.label = body.label || null;
  if ("description" in body) out.description = body.description || null;
  if ("url" in body) out.url = body.url || null;
  if ("command" in body) out.command = body.command || null;
  if ("args" in body) out.args = Array.isArray(body.args) ? body.args.map(String) : [];
  if (out.transport === "http") {
    if (!/^https?:\/\//.test(out.url ?? "")) fail("url: must start with http:// or https://.");
    out.command = null;
    out.args = [];
  } else if (!out.command) {
    fail("command: is required for a command server.");
  } else {
    out.url = null;
  }
  const secrets = out.transport === "http" ? body.headers : body.env;
  if (secrets && typeof secrets === "object") out.secret_names = Object.keys(secrets);
  return out;
}

function scriptedMcpTest(server) {
  const where = `${server.url ?? ""} ${server.command ?? ""} ${(server.args ?? []).join(" ")}`;
  if (/fail/i.test(where)) return { ok: false, message: "(mock) Could not connect: the server did not answer within 20 s.", tools: [] };
  return { ok: true, message: "(mock) Connected; no real server was contacted.", tools: MOCK_TOOLS };
}

function projectLibrary(project) {
  const deviations = project.record.library;
  return {
    skills: allSkills().map((skill) => ({ ...skill, enabled: deviations.skills[skill.id] ?? skill.default })),
    mcp_servers: allServers().map((server) => ({ ...server, enabled: deviations.mcp_servers[server.id] ?? false })),
  };
}

function publicSource(source) {
  const { password_set, ...rest } = source;
  return { ...rest, has_password: password_set };
}

function readSourceFields(body, current = null, { checkName = true } = {}) {
  const out = current ? { ...current } : {};
  const fail = (field, message) => {
    throw new HttpError(400, "invalid_request", `${field}: ${message}`);
  };
  for (const field of ["name", "kind", "host", "database", "user"]) {
    if (field in body || !current) {
      const value = typeof body[field] === "string" ? body[field].trim() : "";
      if (!value) fail(field, "is required.");
      out[field] = value;
    }
  }
  if (out.name.length > 60) fail("name", "must be at most 60 characters.");
  if (!DATA_KINDS.some((item) => item.kind === out.kind)) fail("kind", "must be postgres, redshift or mysql.");
  if ("port" in body || !current) {
    const port = body.port ?? DATA_KINDS.find((item) => item.kind === out.kind).default_port;
    if (!Number.isInteger(port) || port < 1 || port > 65535) fail("port", "must be a number from 1 to 65535.");
    out.port = port;
  }
  if ("ssl" in body || !current) {
    const ssl = body.ssl ?? (out.kind === "redshift" ? "require" : "prefer");
    if (!SSL.includes(ssl)) fail("ssl", "must be disable, prefer, require or verify-full.");
    out.ssl = ssl;
  }
  if ("password" in body) out.password_set = typeof body.password === "string" && body.password !== "";
  else if (!current) out.password_set = false;
  for (const other of checkName ? dataSources.values() : []) {
    if (other.name === out.name && other.id !== out.id) throw new HttpError(409, "name_taken", "A data source with that name already exists.");
  }
  return out;
}

function scriptedTest(source) {
  const kind = DATA_KINDS.find((item) => item.kind === source.kind);
  if (kind && !kind.available) return { ok: false, message: "(mock) The driver for this database type is not installed." };
  if (/fail/i.test(source.host)) return { ok: false, message: `(mock) could not connect to server: Connection refused (${source.host}:${source.port})` };
  return { ok: true, message: "(mock) Connected; no real database was contacted.", server_version: "(mock) 16.0", tables: 0 };
}

const TEMPLATES = [
  { id: "blank", label: "Blank page", description: "An empty page. Describe what it should become.", app: false, files: {} },
  {
    id: "report",
    label: "Report",
    description: "A readable long-form page with headings, figures and sources.",
    app: false,
    files: { "index.html": PAGE_SHELL("Report", "<h1>Report</h1>\n<p>Ask the agent to write it.</p>") },
  },
  {
    id: "dashboard",
    label: "Dashboard",
    description: "A grid of charts and tables for data you upload.",
    app: false,
    files: { "index.html": PAGE_SHELL("Dashboard", "<h1>Dashboard</h1>\n<p>Upload a CSV and ask for charts.</p>") },
  },
  {
    id: "whiteboard",
    label: "Whiteboard",
    description: "A canvas you draw on; it saves itself into the project.",
    app: false,
    files: {
      "index.html": PAGE_SHELL("Whiteboard", "<h1>Whiteboard</h1>\n<p>(Mock: no real canvas.)</p>", '<meta name="studio-reload" content="off">'),
    },
  },
  {
    id: "vite-app",
    label: "Vite app",
    description: "A front-end app with hot reload, served by its own dev server.",
    app: true,
    files: {
      "studio.json": JSON.stringify({ run: "npx vite --host 127.0.0.1 --port $PORT" }, null, 2) + "\n",
      "index.html": PAGE_SHELL("Vite app", "<h1>Vite app</h1>", '<meta name="studio-reload" content="off">'),
    },
  },
  {
    id: "fastapi-app",
    label: "FastAPI app",
    description: "A page with a Python back end (FastAPI) for forms, data and APIs.",
    app: true,
    files: {
      "studio.json": JSON.stringify({ run: "uv run --with fastapi --with uvicorn uvicorn server:app --host 127.0.0.1 --port $PORT" }, null, 2) + "\n",
      "server.py": "from fastapi import FastAPI\n\napp = FastAPI()\n",
      "index.html": PAGE_SHELL("FastAPI app", "<h1>FastAPI app</h1>"),
    },
  },
];

function PAGE_SHELL(title, body, head = "") {
  return `<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n${head ? head + "\n" : ""}<title>${title}</title>\n</head>\n<body>\n${body}\n</body>\n</html>\n`;
}

function createProject(name, { createdAt = now(), template = "blank" } = {}) {
  const id = `p_${crypto.randomBytes(4).toString("hex")}`;
  const port = nextPreviewPort++;
  const project = {
    record: {
      id,
      name: name || "Untitled",
      created_at: createdAt,
      updated_at: createdAt,
      port,
      preview_url: `http://${HOST}:${port}/`,
      connection: "fake",
      model: null,
      session_id: null,
      running_turn: null,
      head: "",
      app: { configured: false, state: "none" },
      approvals: "ask",
      data_sources: [],
      context: [],
      instructions: "",
      turn_options: { depth: "standard", verify: false, ask: false },
      library: { skills: {}, mcp_servers: {} },
      uses_template: null,
    },
    app: { run: null, state: "none", pid: null, exit_code: null, started_at: null, error: null, lines: [], timers: [] },
    queue: [],
    approvalsWaiting: new Map(),
    questionsWaiting: new Map(),
    files: new Map(),
    versions: [],
    events: [],
    listeners: new Set(),
    turn: null,
    turnCount: 0,
    server: null,
  };
  project.files.set("index.html", { data: Buffer.from(BLANK_PAGE), modified_at: createdAt });
  const saved = yourTemplates.get(template);
  const chosen = saved ?? TEMPLATES.find((item) => item.id === template) ?? TEMPLATES[0];
  if (saved && saved.kind !== "app") {
    // A pattern: its files go in template/ and the page starts empty.
    for (const [filePath, content] of Object.entries(saved.files)) {
      project.files.set(`template/${filePath}`, { data: Buffer.from(content), modified_at: createdAt });
    }
    project.record.uses_template = { id: saved.id, label: saved.label, kind: saved.kind };
  } else {
    for (const [filePath, content] of Object.entries(chosen.files)) {
      project.files.set(filePath, { data: Buffer.from(content), modified_at: createdAt });
    }
  }
  commit(project, "init", chosen.id === "blank" ? "Blank page" : `New ${chosen.label}`, null, { silent: true, at: createdAt });
  project.server = http.createServer((req, res) => servePreview(project, req, res));
  project.server.listen(port, HOST);
  projects.set(id, project);
  syncApp(project, { start: true });
  return project;
}

// ---- the simulated app ----------------------------------------------------------------

function readStudioJson(project) {
  const file = project.files.get("studio.json");
  if (!file) return { run: null, error: null };
  try {
    const parsed = JSON.parse(file.data.toString("utf8"));
    if (!parsed || typeof parsed.run !== "string" || !parsed.run.trim()) {
      return { run: null, error: "studio.json needs a \"run\" string." };
    }
    return { run: parsed.run, error: null };
  } catch (error) {
    return { run: null, error: `studio.json is not valid JSON: ${error.message}` };
  }
}

function appObject(project) {
  const app = project.app;
  return {
    configured: Boolean(app.run),
    run: app.run,
    state: app.state,
    pid: app.pid,
    port: app.run ? project.record.port + 100 : null,
    exit_code: app.exit_code,
    started_at: app.started_at,
    error: app.error,
    last_line: app.lines.length ? app.lines[app.lines.length - 1].n : 0,
  };
}

function setAppState(project, state, exitCode = null) {
  const app = project.app;
  app.state = state;
  app.exit_code = exitCode;
  project.record.app = { configured: Boolean(app.run), state };
  emit(project, "app_status", { state, exit_code: exitCode, run: app.run }, project.turn?.id ?? null);
}

function appLog(project, text) {
  const app = project.app;
  const n = (app.lines.length ? app.lines[app.lines.length - 1].n : 0) + 1;
  app.lines.push({ n, text: `[mock] ${text}` });
  if (app.lines.length > 2000) app.lines.splice(0, app.lines.length - 2000);
}

function clearAppTimers(project) {
  for (const timer of project.app.timers) clearTimeout(timer);
  project.app.timers = [];
}

function later(project, ms, fn) {
  project.app.timers.push(setTimeout(fn, ms * Math.max(SPEED, 0.05)));
}

function startApp(project) {
  const app = project.app;
  if (!app.run) throw new HttpError(409, "no_app", "This project has no app. Add a studio.json with a run command.");
  clearAppTimers(project);
  app.lines = [];
  app.pid = 40000 + Math.floor(Math.random() * 9999);
  app.started_at = now();
  setAppState(project, "starting");
  appLog(project, `$ ${app.run}`);
  appLog(project, "This is the mock server: no process runs; these lines are scripted.");
  later(project, 600, () => appLog(project, "Installing dependencies…"));
  later(project, 1200, () => appLog(project, "Starting the server…"));
  if (/crash/i.test(app.run)) {
    later(project, 1800, () => {
      appLog(project, "Traceback (most recent call last): ModuleNotFoundError: No module named 'server' (scripted)");
      app.pid = null;
      setAppState(project, "crashed", 1);
    });
    return;
  }
  later(project, 2000, () => {
    appLog(project, `Listening on http://127.0.0.1:${project.record.port + 100}`);
    setAppState(project, "running");
    let tick = 0;
    const heartbeat = () => {
      if (project.app.state !== "running") return;
      appLog(project, `GET / 200 (scripted request ${++tick})`);
      later(project, 3000, heartbeat);
    };
    later(project, 3000, heartbeat);
  });
}

function stopApp(project) {
  const app = project.app;
  if (!app.run) throw new HttpError(409, "no_app", "This project has no app. Add a studio.json with a run command.");
  clearAppTimers(project);
  if (app.state === "running" || app.state === "starting") {
    appLog(project, "Stopped on request.");
    app.pid = null;
    setAppState(project, "stopped");
  }
}

/** Re-read studio.json; start the app when it appears or its run changed (or when asked). */
function syncApp(project, { start = false, restart = false } = {}) {
  const { run, error } = readStudioJson(project);
  const app = project.app;
  const changed = run !== app.run;
  app.error = error;
  if (!run) {
    if (app.run) {
      clearAppTimers(project);
      app.run = null;
      app.pid = null;
      setAppState(project, "none");
    }
    project.record.app = { configured: false, state: "none" };
    return;
  }
  app.run = run;
  project.record.app = { configured: true, state: app.state };
  if (changed || start || restart) startApp(project);
}

// ---- templates the person saves (mock) ------------------------------------------------------

const yourTemplates = new Map(); // id -> {id, label, description, kind, mode, created_at, from_project, imported, has_code, files: {path: text}, preview: svg|null}

function mockPreviewSvg(label, accent) {
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 320 180"><rect width="320" height="180" fill="#ffffff"/><rect x="24" y="22" width="90" height="8" rx="3" fill="${accent}"/><rect x="24" y="40" width="210" height="16" rx="4" fill="#1a1a1f"/><rect x="24" y="66" width="250" height="7" rx="3" fill="#c8c8d0"/><rect x="24" y="80" width="200" height="7" rx="3" fill="#c8c8d0"/><rect x="24" y="102" width="82" height="50" rx="6" fill="#f1f1f4"/><rect x="118" y="102" width="82" height="50" rx="6" fill="#f1f1f4"/><rect x="212" y="102" width="82" height="50" rx="6" fill="#f1f1f4"/><text x="296" y="172" font-family="system-ui" font-size="9" fill="#888" text-anchor="end">(mock) ${escapeHtml(label)}</text></svg>`;
}

function addTemplate(fields) {
  const id = `tpl_${crypto.randomBytes(4).toString("hex")}`;
  const record = {
    id,
    description: "",
    from_project: null,
    imported: false,
    has_code: false,
    preview: null,
    created_at: now(),
    ...fields,
    mode: fields.kind === "app" ? "copy" : "pattern",
  };
  yourTemplates.set(id, record);
  return record;
}

function publicTemplate(record) {
  const files = Object.keys(record.files);
  return {
    id: record.id,
    label: record.label,
    description: record.description,
    app: record.kind === "app",
    source: "yours",
    kind: record.kind,
    mode: record.mode,
    created_at: record.created_at,
    from_project: record.from_project,
    files: files.length,
    bytes: files.reduce((sum, path) => sum + Buffer.byteLength(record.files[path]), 0),
    preview: record.preview ? `/api/templates/${record.id}/preview` : null,
    main: record.kind === "app" ? "index.html" : files[0] ?? null,
    imported: record.imported,
    has_code: record.has_code,
  };
}

function allTemplates() {
  return [
    ...TEMPLATES.map(({ id, label, description, app }) => ({ id, label, description, app, source: "studio", kind: app ? "app" : "page", mode: "copy", preview: null })),
    ...[...yourTemplates.values()].map(publicTemplate),
  ];
}

// Two mock templates of the person's own, so the New page dialog and the Library have something to show.
addTemplate({
  label: "Mock report look",
  description: "(mock) Header, three figure cards, a comparison table and takeaways.",
  kind: "page",
  from_project: "Coffee subscription market",
  files: { "TEMPLATE.md": "# (mock) Report look\n", "index.html": "<!doctype html><title>(mock) template</title>" },
  preview: mockPreviewSvg("Report look", "#0f8f6f"),
});
addTemplate({
  label: "Mock tracker app",
  description: "(mock) A small app with its own back end.",
  kind: "app",
  imported: true,
  has_code: true,
  files: { "TEMPLATE.md": "# (mock) Tracker app\n", "studio.json": JSON.stringify({ run: "python app.py --port $PORT" }), "index.html": "<!doctype html><title>(mock) tracker</title>" },
});

// ---- a store-only zip (no compression) for the download -----------------------------------

const CRC_TABLE = Array.from({ length: 256 }, (_, n) => {
  let c = n;
  for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
  return c >>> 0;
});

function crc32(buffer) {
  let crc = 0xffffffff;
  for (const byte of buffer) crc = CRC_TABLE[(crc ^ byte) & 0xff] ^ (crc >>> 8);
  return (crc ^ 0xffffffff) >>> 0;
}

function zip(entries) {
  const locals = [];
  const centrals = [];
  let offset = 0;
  for (const [name, data] of entries) {
    const nameBuffer = Buffer.from(name, "utf8");
    const crc = crc32(data);
    const local = Buffer.alloc(30);
    local.writeUInt32LE(0x04034b50, 0);
    local.writeUInt16LE(20, 4);
    local.writeUInt16LE(0x0800, 6);
    local.writeUInt32LE(crc, 14);
    local.writeUInt32LE(data.length, 18);
    local.writeUInt32LE(data.length, 22);
    local.writeUInt16LE(nameBuffer.length, 26);
    locals.push(local, nameBuffer, data);
    const central = Buffer.alloc(46);
    central.writeUInt32LE(0x02014b50, 0);
    central.writeUInt16LE(20, 4);
    central.writeUInt16LE(20, 6);
    central.writeUInt16LE(0x0800, 8);
    central.writeUInt32LE(crc, 16);
    central.writeUInt32LE(data.length, 20);
    central.writeUInt32LE(data.length, 24);
    central.writeUInt16LE(nameBuffer.length, 28);
    central.writeUInt32LE(offset, 42);
    centrals.push(central, nameBuffer);
    offset += 30 + nameBuffer.length + data.length;
  }
  const centralSize = centrals.reduce((sum, part) => sum + part.length, 0);
  const end = Buffer.alloc(22);
  end.writeUInt32LE(0x06054b50, 0);
  end.writeUInt16LE(entries.length, 8);
  end.writeUInt16LE(entries.length, 10);
  end.writeUInt32LE(centralSize, 12);
  end.writeUInt32LE(offset, 16);
  return Buffer.concat([...locals, ...centrals, end]);
}

function getProject(id) {
  const project = projects.get(id);
  if (!project) throw new HttpError(404, "project_not_found", "That project does not exist.");
  return project;
}

function checkPath(raw) {
  const value = String(raw ?? "").replace(/\\/g, "/").replace(/^\/+/, "");
  const parts = value.split("/");
  if (!value || parts.some((part) => !part || part === "." || part === "..") || parts[0] === ".git") {
    throw new HttpError(400, "bad_path", "That file path is not allowed.");
  }
  return value;
}

function writeFile(project, filePath, data) {
  project.files.set(filePath, { data: Buffer.isBuffer(data) ? data : Buffer.from(data), modified_at: now() });
}

function emit(project, type, fields, turnId = null) {
  const event = { seq: project.events.length + 1, ts: now(), type, turn_id: turnId, ...fields };
  project.events.push(event);
  const frame = `id: ${event.seq}\ndata: ${JSON.stringify(event)}\n\n`;
  for (const res of project.listeners) res.write(frame);
  return event;
}

/** Record the working files as a new version if they differ from the last one. */
function commit(project, kind, message, turnId, { silent = false, at = now() } = {}) {
  const previous = project.versions[project.versions.length - 1]?.tree ?? new Map();
  const files = [];
  for (const [filePath, file] of project.files) {
    const before = previous.get(filePath);
    if (!before) files.push({ path: filePath, status: "A" });
    else if (!before.equals(file.data)) files.push({ path: filePath, status: "M" });
  }
  for (const filePath of previous.keys()) {
    if (!project.files.has(filePath)) files.push({ path: filePath, status: "D" });
  }
  if (!files.length && kind !== "init") return null;
  files.sort((a, b) => a.path.localeCompare(b.path));
  const tree = new Map([...project.files].map(([filePath, file]) => [filePath, file.data]));
  const sha = sha256(`${project.versions.length}:${at}:${message}:${Math.random()}`).slice(0, 40);
  project.versions.push({ sha, kind, message, created_at: at, files, tree });
  project.record.head = sha;
  project.record.updated_at = at;
  if (!silent) emit(project, "checkpoint", { sha, kind, message, created_at: at, files }, turnId);
  // Like the real server: the checkpoint object, not just its sha.
  return { sha, kind, message, created_at: at, files };
}

// ---- a small unified diff (line LCS) ---------------------------------------------

function diffLines(a, b) {
  const n = a.length;
  const m = b.length;
  const table = Array.from({ length: n + 1 }, () => new Uint32Array(m + 1));
  for (let i = n - 1; i >= 0; i--) {
    for (let j = m - 1; j >= 0; j--) {
      table[i][j] = a[i] === b[j] ? table[i + 1][j + 1] + 1 : Math.max(table[i + 1][j], table[i][j + 1]);
    }
  }
  const ops = [];
  let i = 0;
  let j = 0;
  while (i < n && j < m) {
    if (a[i] === b[j]) ops.push([" ", a[i++], j++]);
    else if (table[i + 1][j] >= table[i][j + 1]) ops.push(["-", a[i++]]);
    else ops.push(["+", b[j++]]);
  }
  while (i < n) ops.push(["-", a[i++]]);
  while (j < m) ops.push(["+", b[j++]]);
  return ops;
}

function unifiedDiff(filePath, before, after) {
  const header = [`diff --git a/${filePath} b/${filePath}`];
  if (before === null) header.push("new file mode 100644");
  if (after === null) header.push("deleted file mode 100644");
  const isText = (buffer) => buffer === null || !buffer.includes(0);
  if (!isText(before) || !isText(after)) return [...header, `Binary files differ`].join("\n");
  header.push(`--- ${before === null ? "/dev/null" : `a/${filePath}`}`, `+++ ${after === null ? "/dev/null" : `b/${filePath}`}`);
  const split = (buffer) => (buffer === null || !buffer.length ? [] : buffer.toString("utf8").replace(/\n$/, "").split("\n"));
  const ops = diffLines(split(before), split(after));
  const out = [...header];
  const CONTEXT = 3;
  let index = 0;
  let oldLine = 1;
  let newLine = 1;
  while (index < ops.length) {
    if (ops[index][0] === " ") {
      index++;
      oldLine++;
      newLine++;
      continue;
    }
    // Start a hunk CONTEXT lines back and extend it until CONTEXT*2 unchanged lines pass.
    let start = index;
    let back = 0;
    while (start > 0 && back < CONTEXT && ops[start - 1][0] === " ") {
      start--;
      back++;
    }
    let end = index;
    let quiet = 0;
    for (let k = index; k < ops.length; k++) {
      if (ops[k][0] === " ") {
        quiet++;
        if (quiet > CONTEXT * 2) break;
      } else {
        quiet = 0;
        end = k;
      }
    }
    end = Math.min(ops.length - 1, end + CONTEXT);
    const body = ops.slice(start, end + 1);
    const oldCount = body.filter((op) => op[0] !== "+").length;
    const newCount = body.filter((op) => op[0] !== "-").length;
    out.push(`@@ -${oldCount ? oldLine - back : 0},${oldCount} +${newCount ? newLine - back : 0},${newCount} @@`);
    for (const op of body) out.push(op[0] + op[1]);
    for (let k = index; k <= end; k++) {
      if (ops[k][0] !== "+") oldLine++;
      if (ops[k][0] !== "-") newLine++;
    }
    index = end + 1;
  }
  return out.join("\n");
}

function versionDiff(project, sha) {
  const at = project.versions.findIndex((version) => version.sha === sha);
  if (at === -1) throw new HttpError(404, "version_not_found", "That version does not exist.");
  const version = project.versions[at];
  const parent = at > 0 ? project.versions[at - 1].tree : new Map();
  const parts = version.files.map((file) =>
    unifiedDiff(file.path, parent.get(file.path) ?? null, version.tree.get(file.path) ?? null),
  );
  let diff = parts.join("\n");
  const truncated = diff.length > 200_000;
  if (truncated) diff = diff.slice(0, 200_000);
  return { sha, diff, truncated };
}

// ---- the scripted "agent" ------------------------------------------------------------

class Stopped extends Error {}

function pageTitle(text) {
  const words = text.replace(/["“”]/g, "").replace(/\s+/g, " ").trim().split(" ").slice(0, 7).join(" ");
  const title = words.replace(/[.,;:!?]+$/, "");
  return title ? title.charAt(0).toUpperCase() + title.slice(1) : "Untitled page";
}

const STYLES = (accent) => `:root {
  --accent: ${accent};
  --ink: #17171c;
  --muted: #62626e;
  --line: #e6e6eb;
}

* { box-sizing: border-box; }

body {
  margin: 0;
  font-family: system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
  color: var(--ink);
  background: #fff;
  line-height: 1.55;
}

main { max-width: 880px; margin: 0 auto; padding: 72px 32px 96px; }

.eyebrow {
  margin: 0 0 12px;
  color: var(--accent);
  font-size: 13px;
  font-weight: 600;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}

h1 { margin: 0; font-size: 44px; line-height: 1.08; letter-spacing: -0.03em; }
.lede { margin: 18px 0 0; max-width: 620px; color: var(--muted); font-size: 19px; }

section { margin-top: 56px; }
h2 { margin: 0 0 18px; font-size: 22px; letter-spacing: -0.015em; }

.cards { display: grid; grid-template-columns: repeat(3, 1fr); gap: 16px; }
.card { padding: 20px; border: 1px solid var(--line); border-radius: 14px; }
.card .label { color: var(--muted); font-size: 13px; }
.card .value { margin-top: 6px; font-size: 30px; font-weight: 650; letter-spacing: -0.02em; }
.card .note { margin-top: 2px; color: var(--muted); font-size: 13px; }

table { width: 100%; border-collapse: collapse; font-size: 15px; }
th, td { padding: 12px 10px; border-bottom: 1px solid var(--line); text-align: left; }
th { color: var(--muted); font-size: 13px; font-weight: 600; }
td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; }

.takeaways { margin: 0; padding-left: 20px; }
.takeaways li + li { margin-top: 8px; }

.is-highlighted { background: linear-gradient(transparent 58%, color-mix(in srgb, var(--accent) 24%, transparent) 58%); }

footer { margin-top: 72px; padding-top: 20px; border-top: 1px solid var(--line); color: var(--muted); font-size: 13px; }
`;

const PAGE = (title, body, withStyles) => `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>${escapeHtml(title)}</title>
${withStyles ? '<link rel="stylesheet" href="styles.css">\n' : ""}</head>
<body>
<main>
${body.join("\n")}
</main>
</body>
</html>
`;

const SECTIONS = {
  header: (title) => `<header>
  <p class="eyebrow">Sample analysis</p>
  <h1>${escapeHtml(title)}</h1>
  <p class="lede">A one-page view of where the market stands, who leads it, and what to watch next quarter.</p>
</header>`,
  cards: `<section id="overview">
  <h2>At a glance</h2>
  <div class="cards">
    <div class="card"><div class="label">Market size</div><div class="value">$4.2B</div><div class="note">Sample figure</div></div>
    <div class="card"><div class="label">Growth</div><div class="value">18%</div><div class="note">Sample figure</div></div>
    <div class="card"><div class="label">Players tracked</div><div class="value">4</div><div class="note">Sample figure</div></div>
  </div>
</section>`,
  table: `<section id="comparison">
  <h2>Comparison</h2>
  <table>
    <thead><tr><th>Company</th><th>Positioning</th><th class="num">Share</th><th class="num">Price</th></tr></thead>
    <tbody>
      <tr><td>Northwind</td><td>Enterprise suite</td><td class="num">34%</td><td class="num">$49</td></tr>
      <tr><td>Lumen</td><td>Developer first</td><td class="num">27%</td><td class="num">$29</td></tr>
      <tr><td>Harbor</td><td>Low-cost bundle</td><td class="num">21%</td><td class="num">$15</td></tr>
      <tr><td>Kite</td><td>Vertical specialist</td><td class="num">18%</td><td class="num">$39</td></tr>
    </tbody>
  </table>
</section>`,
  takeaways: `<section id="takeaways">
  <h2>Takeaways</h2>
  <ul class="takeaways">
    <li>Northwind leads on share but is the <strong>most expensive</strong> option in the set.</li>
    <li>Lumen is closing the gap by winning developer teams first.</li>
    <li>Price pressure from Harbor is the main risk to watch.</li>
  </ul>
</section>`,
  footer: `<footer>Sample content written by the Studio mock server. No real data.</footer>`,
};

const ACCENTS = ["#5b5bd6", "#0f8f6f", "#c2410c", "#0e7490", "#a21caf"];

/** The finished sample page, used by the build script (step by step) and by --seed (at once). */
function buildSteps(title, accent) {
  const parts = [SECTIONS.header(title)];
  const steps = [];
  steps.push({ path: "index.html", content: PAGE(title, parts, false), title: "Write index.html" });
  steps.push({ path: "styles.css", content: STYLES(accent), title: "Write styles.css", also: PAGE(title, parts, true) });
  for (const key of ["cards", "table", "takeaways"]) {
    parts.push(SECTIONS[key]);
    if (key === "takeaways") parts.push(SECTIONS.footer);
    steps.push({ path: "index.html", content: PAGE(title, parts, true), title: "Edit index.html" });
  }
  return steps;
}

function beginTurn(project, { text, selection = null, attachments = [], connection, model, queued_id = null }) {
  const turnId = `turn_${crypto.randomBytes(5).toString("hex")}`;
  project.turn = { id: turnId, cancel: null, stopped: false, pendingApproval: null };
  project.turnCount++;
  project.record.running_turn = turnId;
  if (typeof connection === "string") project.record.connection = connection;
  if (model !== undefined) project.record.model = model ?? null;
  emit(
    project,
    "turn_started",
    { text, selection, connection: project.record.connection, model: project.record.model, attachments, queued_id },
    turnId,
  );
  runTurn(project, turnId, text, selection, attachments);
  return turnId;
}

async function runTurn(project, turnId, text, selection, attachments = []) {
  const turn = project.turn;
  const started = Date.now();
  const e = (type, fields) => emit(project, type, fields, turnId);
  const sleep = (ms) =>
    new Promise((resolve, reject) => {
      if (turn.stopped) return reject(new Stopped());
      const timer = setTimeout(() => {
        turn.cancel = null;
        resolve();
      }, ms * SPEED);
      turn.cancel = () => {
        clearTimeout(timer);
        reject(new Stopped());
      };
    });
  let counter = 0;
  const newId = (prefix) => `${prefix}_${turnId.slice(-6)}_${++counter}`;

  const stream = async (type, textToSend) => {
    const message_id = newId("msg");
    const pieces = textToSend.match(/\s*\S+/g) ?? [];
    for (let i = 0; i < pieces.length; i += 3) {
      e(type, { message_id, text: pieces.slice(i, i + 3).join("") });
      await sleep(45);
    }
  };
  const say = (value) => stream("message_chunk", value);
  const think = (value) => stream("thought_chunk", value);

  const tool = async (kind, name, title, input, summary, options = {}) => {
    const tool_id = newId("tool");
    e("tool_call", { tool_id, name, kind, title, input, parent_tool_id: options.parent ?? null });
    if (options.inside) await options.inside(tool_id);
    await sleep(options.ms ?? 550);
    let paths = [];
    if (options.write) {
      for (const [filePath, content] of Object.entries(options.write)) {
        writeFile(project, filePath, content);
        paths.push(filePath);
      }
    }
    e("tool_result", { tool_id, status: options.error ? "error" : "ok", summary });
    if (paths.length) {
      await sleep(120);
      e("files_changed", { paths });
    }
    return tool_id;
  };
  const read = (filePath) =>
    tool("read", "Read", `Read ${filePath}`, { file_path: filePath }, `${project.files.get(filePath)?.data.length ?? 0} bytes`, { ms: 350 });
  const index = () => project.files.get("index.html")?.data.toString("utf8") ?? "";

  const plan = (entries, doneCount) =>
    e("plan", {
      entries: entries.map((content, i) => ({
        content,
        status: i < doneCount ? "completed" : i === doneCount ? "in_progress" : "pending",
      })),
    });

  const research = async (topic) => {
    await tool("agent", "Task", "Research the market", { description: "Research the market", prompt: `Find the main competitors for: ${topic}` }, "Found 4 companies and 2 sources (sample).", {
      ms: 300,
      inside: async (parent) => {
        await tool("search", "WebSearch", `Search “${topic.slice(0, 40)} competitors”`, { query: `${topic} competitors` }, "8 results", { parent, ms: 600 });
        await tool("fetch", "WebFetch", "Fetch example.com/market-report", { url: "https://example.com/market-report" }, "200 OK · 14 KB", { parent, ms: 700 });
      },
    });
  };

  let message = "Agent turn";
  let status = "completed";
  let error = null;

  const risky = async () => {
    const command = "sudo brew install imagemagick";
    if (project.record.approvals !== "allow") {
      const approval_id = newId("approval");
      turn.pendingApproval = approval_id;
      e("approval_request", {
        approval_id,
        tool: "Bash",
        title: "Run a shell command",
        detail: command,
        reason: "It uses sudo and installs software outside the project.",
      });
      const decision = await new Promise((resolve, reject) => {
        if (turn.stopped) return reject(new Stopped());
        project.approvalsWaiting.set(approval_id, resolve);
        turn.cancel = () => {
          project.approvalsWaiting.delete(approval_id);
          reject(new Stopped());
        };
      });
      turn.cancel = null;
      turn.pendingApproval = null;
      e("approval_resolved", { approval_id, decision, by: "user" });
      if (decision !== "allow") {
        await say("You denied that command, so I skipped it and changed nothing outside the project.");
        return;
      }
    }
    await tool("execute", "Bash", `Run ${command}`, { command }, "(mock) Nothing was installed; the mock server does not run commands.", { ms: 500 });
  };

  // "Ask me first" (or the words "ask me"): two stand-in questions on a card, then the answers echoed.
  const askFirst = async () => {
    const question_id = newId("question");
    const questions = [
      {
        id: "q1",
        question: "(mock) Who is this page for?",
        multiple: false,
        choices: [
          { label: "(mock) The team", description: "Stand-in first choice." },
          { label: "(mock) A customer", description: "Stand-in second choice." },
        ],
      },
      { id: "q2", question: "(mock) Anything it must include?", multiple: false, choices: [] },
    ];
    turn.pendingQuestion = question_id;
    e("question_request", { question_id, questions, why: "(mock) Stand-in questions; no model decided to ask them." });
    const answers = await new Promise((resolve, reject) => {
      if (turn.stopped) return reject(new Stopped());
      project.questionsWaiting.set(question_id, { questions, resolve });
      turn.cancel = () => {
        project.questionsWaiting.delete(question_id);
        reject(new Stopped());
      };
    });
    turn.cancel = null;
    turn.pendingQuestion = null;
    e("question_resolved", { question_id, by: "user", skipped: answers === null, answers: answers ?? [] });
    if (answers === null) return void (await say("You skipped the questions, so I decided myself. (Mock.)"));
    const given = answers.filter((a) => a.chosen.length || a.text).map((a) => [...a.chosen, a.text].filter(Boolean).join(" · "));
    await say(`Thanks. I will work from: ${given.join("; ")}. (Mock: the answers change nothing here.)`);
  };

  const backend = async () => {
    const run = /crash/i.test(text)
      ? "python crash_on_start.py --port $PORT"
      : "uv run --with fastapi --with uvicorn uvicorn server:app --host 127.0.0.1 --port $PORT";
    const studio = JSON.stringify({ run }, null, 2) + "\n";
    await tool("edit", "Write", "Write studio.json", { file_path: "studio.json", content: studio }, "Created studio.json", { write: { "studio.json": studio } });
    await tool("edit", "Write", "Write server.py", { file_path: "server.py" }, "Created server.py", { write: { "server.py": "from fastapi import FastAPI\n\napp = FastAPI()\n" } });
    await say("I added a small back end (`studio.json` + `server.py`). The studio starts it when this turn ends. (Mock: the app is simulated.)");
  };

  try {
    await sleep(350);
    if (attachments.length) {
      await say(`I can see ${attachments.length === 1 ? "the attached file" : `${attachments.length} attached files`}: ${attachments.map((item) => `\`${item}\``).join(", ")}.`);
    }
    if (project.record.turn_options?.ask || /\bask me\b/i.test(text)) await askFirst();
    if (/\brisky\b/i.test(text)) await risky();
    if (/\bbackend\b/i.test(text)) await backend();
    if (/\brender\b/i.test(text)) {
      if (!project.files.has("files/notes.docx")) writeFile(project, "files/notes.docx", MOCK_OFFICE_BYTES("Word file"));
      await tool(
        "read",
        "mcp__studio__render_document",
        "Render files/notes.docx for viewing",
        { path: "files/notes.docx" },
        JSON.stringify({ file: "files/notes.docx", kind: "document", page_count: 1, preview: "files/notes.docx.preview/index.html" }),
        { ms: 500, write: { "files/notes.docx.preview/index.html": mockPreviewHtml("files/notes.docx") } },
      );
    }
    if (/\bsave (this|it) as a template\b/i.test(text)) {
      const record = addTemplate({
        label: `(mock) ${project.record.name} look`,
        kind: "page",
        from_project: project.record.name,
        files: { "TEMPLATE.md": "# (mock) template saved by the scripted agent\n" },
        preview: mockPreviewSvg(project.record.name, "#c2410c"),
      });
      await tool("other", "mcp__studio__save_template", `Save the template “${record.label}”`, { name: record.label }, JSON.stringify(publicTemplate(record)), { ms: 400 });
    }
    if (/\bcheck\b/i.test(text)) {
      await tool(
        "read",
        "mcp__studio__check_page",
        "Check citations and numbers on index.html",
        { path: "index.html" },
        JSON.stringify({ page: "index.html", checked_at: now(), summary: { verified: 3, derived: 1, failed: 1, unmarked_numbers: 2, sources_cited: 2, sources_unused: 0 }, items: [], unmarked: [], sources: [] }),
        { ms: 400 },
      );
    }
    if (/\bfail/i.test(text)) {
      await think("I'll start by looking at the page as it is.");
      await read("index.html");
      await tool("execute", "Bash", "Run python build.py", { command: "python build.py" }, "python: can't open file 'build.py': [Errno 2] No such file or directory", { error: true });
      throw new Error("The model connection dropped (this is the mock server's scripted failure). Nothing was changed.");
    } else if (selection) {
      message = `Change the selected ${selection.tag}`;
      await think(`The user pointed at a ${selection.tag} element (${selection.selector}). I'll change only that element.`);
      await read("index.html");
      const html = index();
      const quoted = /["“]([^"”]+)["”]/.exec(text)?.[1];
      let next = html;
      if (quoted && selection.text && html.split(escapeHtml(selection.text)).length === 2) {
        next = html.replace(escapeHtml(selection.text), escapeHtml(quoted));
      } else if (selection.html && html.includes(selection.html)) {
        const open = selection.html.match(/^<[^>]+>/)?.[0] ?? "";
        const marked = / class="/.test(open) ? open.replace(/ class="/, ' class="is-highlighted ') : open.replace(/^<(\w+)/, '<$1 class="is-highlighted"');
        next = html.replace(selection.html, selection.html.replace(open, marked));
      }
      if (next === html) {
        await say(`I couldn't find that exact \`${selection.tag}\` in \`index.html\`, so I left the page as it is. (The mock server only matches simple elements.)`);
      } else {
        await tool("edit", "Edit", "Edit index.html", { file_path: "index.html", old_string: selection.html.slice(0, 200), new_string: "…" }, "1 replacement", { write: { "index.html": next } });
        await say(quoted ? `Done — the \`${selection.tag}\` now reads **${quoted}**. Nothing else on the page changed.` : `Done — I highlighted the selected \`${selection.tag}\`. Nothing else on the page changed.`);
      }
    } else if (project.turnCount === 1) {
      const title = pageTitle(text);
      message = `Build “${title}”`;
      const steps = buildSteps(title, ACCENTS[projects.size % ACCENTS.length]);
      const todo = ["Look at the current page", "Research the market", "Write the page structure", "Add the styles", "Add figures, table and takeaways"];
      plan(todo, 0);
      await think("The page is blank, so I can choose the structure freely. A short header, three headline figures, one comparison table and a few takeaways should fit on one screen and stay readable.");
      await read("index.html");
      plan(todo, 1);
      await research(title);
      plan(todo, 2);
      await say("I’ll build this as a single page: a short header, the key figures, a comparison table and the takeaways. Starting with the structure.");
      await tool("edit", "Write", steps[0].title, { file_path: "index.html", content: steps[0].content }, "Created the page skeleton", { write: { "index.html": steps[0].content } });
      plan(todo, 3);
      await tool("edit", "Write", steps[1].title, { file_path: "styles.css", content: steps[1].content }, "Created styles.css", { write: { "styles.css": steps[1].content, "index.html": steps[1].also } });
      plan(todo, 4);
      for (const step of steps.slice(2)) {
        await tool("edit", "Edit", step.title, { file_path: "index.html", old_string: "</main>", new_string: "…section…</main>" }, "1 replacement", { write: { "index.html": step.content } });
      }
      await tool("execute", "Bash", "Run wc -c index.html styles.css", { command: "wc -c index.html styles.css" }, `${project.files.get("index.html").data.length} index.html\n${project.files.get("styles.css").data.length} styles.css`, { ms: 400 });
      plan(todo, 5);
      await say(
        [
          "The page is ready. Here’s what’s on it:",
          "",
          "- **Header** with the title and a one-line summary",
          "- **Three headline figures** in cards",
          "- **Comparison table** across four companies",
          "- **Takeaways** at the bottom",
          "",
          "Styles live in `styles.css`. The figures are samples from the mock server. Tell me what to change, or switch to **Select** and click the part you mean.",
        ].join("\n"),
      );
    } else {
      message = pageTitle(text);
      await think("A follow-up request. I'll read the page first and make the smallest change that does it.");
      await read("index.html");
      if (/research/i.test(text)) {
        e("notice", { level: "warning", message: "Web search is simulated in the mock server; no pages are really fetched." });
        await research(text);
      }
      const note = `<section id="notes-${project.turnCount}">\n  <h2>Notes</h2>\n  <p>${escapeHtml(text)}</p>\n</section>`;
      const html = index();
      const next = html.includes("<footer>") ? html.replace("<footer>", `${note}\n<footer>`) : html.replace("</body>", `${note}\n</body>`);
      await tool("edit", "Edit", "Edit index.html", { file_path: "index.html", old_string: "<footer>", new_string: "<section>…</section><footer>" }, "1 replacement", { write: { "index.html": next } });
      await say("I added a **Notes** section near the bottom of the page with your request. (The mock server doesn’t understand instructions; it only shows that an edit happened.)");
    }
  } catch (cause) {
    if (cause instanceof Stopped) status = "stopped";
    else {
      status = "failed";
      error = cause.message;
    }
  }

  if (turn.pendingQuestion) {
    project.questionsWaiting.delete(turn.pendingQuestion);
    e("question_resolved", { question_id: turn.pendingQuestion, by: "turn_end", skipped: true, answers: [] });
    turn.pendingQuestion = null;
  }
  if (turn.pendingApproval) {
    project.approvalsWaiting.delete(turn.pendingApproval);
    e("approval_resolved", { approval_id: turn.pendingApproval, decision: "deny", by: "turn_end" });
    turn.pendingApproval = null;
  }
  const checkpoint = commit(project, "agent", message, turnId);
  libraryObservedAt = libraryObservedAt ?? now();
  const duration = Date.now() - started;
  project.record.session_id = project.record.session_id ?? `mock-session-${project.record.id}`;
  project.record.running_turn = null;
  project.turn = null;
  const input_tokens = 9200 + counter * 310;
  const output_tokens = 640 + counter * 95;
  e("turn_finished", {
    status,
    error,
    usage: {
      input_tokens,
      output_tokens,
      // What these tokens would cost at API list prices; the mock labels it an estimate.
      cost_usd: Math.round(((input_tokens * 15 + output_tokens * 75) / 1e6) * 10000) / 10000,
      cost_estimated: true,
      duration_ms: duration,
    },
    session_id: project.record.session_id,
  });
  if (checkpoint) syncApp(project, { restart: Boolean(project.app.run) });
  if (status === "stopped") {
    for (const item of project.queue.splice(0)) emit(project, "turn_unqueued", { queued_id: item.queued_id, reason: "stopped", text: item.text });
  } else if (project.queue.length) {
    beginTurn(project, project.queue.shift());
  }
}

// ---- preview + bridge ---------------------------------------------------------------------

const MIME = {
  ".html": "text/html; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".mjs": "text/javascript; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".svg": "image/svg+xml",
  ".png": "image/png",
  ".jpg": "image/jpeg",
  ".jpeg": "image/jpeg",
  ".gif": "image/gif",
  ".webp": "image/webp",
  ".ico": "image/x-icon",
  ".avif": "image/avif",
  ".mp4": "video/mp4",
  ".m4v": "video/mp4",
  ".webm": "video/webm",
  ".mov": "video/quicktime",
  ".mp3": "audio/mpeg",
  ".wav": "audio/wav",
  ".m4a": "audio/mp4",
  ".ogg": "audio/ogg",
  ".flac": "audio/flac",
  ".csv": "text/csv; charset=utf-8",
  ".txt": "text/plain; charset=utf-8",
  ".md": "text/markdown; charset=utf-8",
  ".woff2": "font/woff2",
  ".webmanifest": "application/manifest+json",
};

const EMPTY_PROJECT_PAGE = `<!doctype html><meta charset="utf-8"><title>Empty project</title>
<body style="font:15px system-ui;color:#666;display:grid;place-items:center;height:100vh;margin:0">
<p>This project has no index.html yet.</p></body>`;

// The mock's bridge. The real one is served by the studio server; this follows the
// same message contract (ready / set-mode / select / text-edit / navigate).
const BRIDGE_JS = String.raw`(() => {
  if (window.__studioBridge || window.parent === window) return;
  window.__studioBridge = true;
  const send = (type, fields) => window.parent.postMessage({ source: "studio-bridge", type, ...fields }, "*");
  const here = () => location.pathname;
  let mode = "browse";
  let editing = null;
  let before = "";

  const box = document.createElement("div");
  box.style.cssText = "position:fixed;z-index:2147483647;pointer-events:none;display:none;border:2px solid #5b5bd6;border-radius:4px;background:rgba(91,91,214,.08);transition:all 60ms ease-out";
  const tag = document.createElement("div");
  tag.style.cssText = "position:absolute;left:-2px;top:-22px;height:20px;padding:0 6px;border-radius:4px;background:#5b5bd6;color:#fff;font:600 11px/20px ui-monospace,monospace;white-space:nowrap";
  box.appendChild(tag);

  const isOurs = (el) => el === box || el === tag;
  const outline = (el, label) => {
    if (!el || isOurs(el)) return (box.style.display = "none");
    const r = el.getBoundingClientRect();
    Object.assign(box.style, { display: "block", left: r.left - 2 + "px", top: r.top - 2 + "px", width: r.width + 4 + "px", height: r.height + 4 + "px" });
    tag.textContent = label;
    tag.style.top = r.top < 26 ? "100%" : "-22px";
  };

  const selectorFor = (el) => {
    const parts = [];
    for (let node = el; node && node.nodeType === 1 && node !== document.body && node !== document.documentElement; node = node.parentElement) {
      if (node.id) {
        parts.unshift("#" + CSS.escape(node.id));
        break;
      }
      let part = node.tagName.toLowerCase();
      const same = Array.from(node.parentElement ? node.parentElement.children : []).filter((c) => c.tagName === node.tagName);
      if (same.length > 1) part += ":nth-of-type(" + (same.indexOf(node) + 1) + ")";
      parts.unshift(part);
    }
    return parts.join(" > ") || "body";
  };

  const textTarget = (el) => {
    for (let node = el; node && node !== document.body; node = node.parentElement) {
      if (Array.from(node.childNodes).some((c) => c.nodeType === 3 && c.textContent.trim())) return node;
    }
    return null;
  };

  const finishEdit = (commit) => {
    if (!editing) return;
    const el = editing;
    editing = null;
    el.removeAttribute("contenteditable");
    el.style.outline = "";
    const after = el.textContent;
    if (!commit) el.textContent = before;
    else if (after !== before) send("text-edit", { path: here(), selector: selectorFor(el), before_text: before, after_text: after });
  };

  document.addEventListener("mousemove", (event) => {
    if (mode === "select") outline(event.target, event.target.tagName.toLowerCase());
    else if (mode === "edit" && !editing) {
      const target = textTarget(event.target);
      outline(target, "edit text");
    }
  }, true);
  document.addEventListener("mouseleave", () => (box.style.display = "none"));

  const swallow = (event) => {
    if (mode === "browse") return;
    if (mode === "edit" && editing && editing.contains(event.target)) return;
    event.preventDefault();
    event.stopPropagation();
    event.stopImmediatePropagation();
  };
  for (const type of ["mousedown", "mouseup", "pointerdown", "pointerup", "dblclick", "submit"]) {
    document.addEventListener(type, (event) => {
      if (mode === "draw" && drawTool === "pan") return;
      swallow(event);
    }, true);
  }

  // ---- a minimal draw mode (mock): a click adds one mark of the chosen tool; "pan" lets
  // everything through. Real strokes, boxes and drags are bridge.js's job. ----
  let drawTool = "pen";
  let drawColor = "#ef4444";
  const marks = [];
  const dots = [];
  const sendDrawing = () => {
    send("drawing", {
      count: marks.length,
      drawing: marks.length
        ? {
            path: here(),
            viewport: { width: innerWidth, height: innerHeight },
            dark: matchMedia("(prefers-color-scheme: dark)").matches,
            page: { width: document.documentElement.scrollWidth, height: document.documentElement.scrollHeight },
            marks: marks.map((mark, index) => ({ ...mark, n: index + 1 })),
          }
        : null,
    });
  };
  const redrawDots = () => {
    dots.forEach((dot, index) => {
      dot.textContent = String(index + 1);
      dot.style.display = mode === "draw" ? "grid" : "none";
    });
  };
  const addMark = (event, el) => {
    const x = event.pageX;
    const y = event.pageY;
    const r = el.getBoundingClientRect();
    const rect = [Math.round(r.left + scrollX), Math.round(r.top + scrollY), Math.round(r.width), Math.round(r.height)];
    const anchor = { selector: selectorFor(el), tag: el.tagName.toLowerCase(), text: (el.innerText || "").trim().slice(0, 200), rect };
    const rel = [[rect[2] ? (x - rect[0]) / rect[2] : 0, rect[3] ? (y - rect[1]) / rect[3] : 0]];
    // One click stands in for each gesture: a dot, a note, a 120×60 box, or a move 80 px down.
    const mark = drawTool === "note"
      ? { kind: "note", text: "(mock note)", color: drawColor, points: [[x, y]], box: [x, y, 0, 0], rel, anchor, section: null }
      : drawTool === "box"
        ? { kind: "box", text: "(mock box)", color: drawColor, points: [[x, y], [x + 120, y + 60]], box: [x, y, 120, 60], rel, anchor, covers: [], section: null }
        : drawTool === "move"
          ? { kind: "move", color: drawColor, points: [[x, y], [x, y + 80]], box: [x, y, 0, 80], rel, anchor, section: null }
          : { kind: "stroke", shape: "dot", color: drawColor, points: [[x, y]], box: [x - 6, y - 6, 12, 12], rel, anchor, section: null, from: null, to: null };
    marks.push(mark);
    const dot = document.createElement("div");
    dot.style.cssText = "position:absolute;z-index:2147483646;width:22px;height:22px;margin:-11px 0 0 -11px;border-radius:50%;place-items:center;color:#fff;font:600 12px system-ui;pointer-events:none;background:" + drawColor + ";left:" + x + "px;top:" + y + "px";
    document.body.appendChild(dot);
    dots.push(dot);
    redrawDots();
    sendDrawing();
  };

  document.addEventListener("click", (event) => {
    if (mode === "browse") return;
    if (mode === "draw") {
      if (drawTool === "pan") return;
      swallow(event);
      addMark(event, event.target);
      return;
    }
    if (mode === "edit" && editing && editing.contains(event.target)) return;
    swallow(event);
    const el = event.target;
    if (mode === "select") {
      send("select", {
        path: here(),
        selector: selectorFor(el),
        tag: el.tagName.toLowerCase(),
        text: (el.innerText || el.textContent || "").trim().slice(0, 500),
        html: el.outerHTML.slice(0, 4000),
      });
    } else if (mode === "edit") {
      finishEdit(true);
      const target = textTarget(el);
      if (!target) return;
      editing = target;
      before = target.textContent;
      target.setAttribute("contenteditable", "plaintext-only");
      target.style.outline = "2px solid #5b5bd6";
      target.style.outlineOffset = "3px";
      box.style.display = "none";
      target.focus();
    }
  }, true);

  document.addEventListener("focusout", (event) => {
    if (editing && event.target === editing) finishEdit(true);
  }, true);
  document.addEventListener("keydown", (event) => {
    if (!editing) return;
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      editing.blur();
    } else if (event.key === "Escape") {
      finishEdit(false);
    }
  }, true);

  window.addEventListener("message", (event) => {
    const data = event.data;
    if (event.source !== window.parent || !data || data.source !== "studio-shell") return;
    if (data.type === "set-mode" && ["browse", "select", "edit", "draw"].includes(data.mode)) {
      finishEdit(true);
      mode = data.mode;
      box.style.display = "none";
      document.documentElement.style.cursor = mode === "select" || mode === "draw" ? "crosshair" : mode === "edit" ? "text" : "";
      redrawDots();
    } else if (data.type === "draw") {
      if (["pen", "box", "note", "move", "pan"].includes(data.tool)) drawTool = data.tool;
      if (typeof data.color === "string") drawColor = data.color;
    } else if (data.type === "draw-undo" && marks.length) {
      marks.pop();
      dots.pop().remove();
      redrawDots();
      sendDrawing();
    } else if (data.type === "draw-clear") {
      marks.length = 0;
      dots.splice(0).forEach((dot) => dot.remove());
      sendDrawing();
    }
  });

  window.addEventListener("hashchange", () => send("navigate", { path: here() }));
  window.addEventListener("popstate", () => send("navigate", { path: here() }));

  // Files dragged onto the page: the shell shows its drop target over the frame.
  document.addEventListener(
    "dragenter",
    (event) => {
      const types = event.dataTransfer ? Array.from(event.dataTransfer.types || []) : [];
      if (types.includes("Files")) send("file-drag", {});
    },
    true,
  );
  const ready = () => {
    document.body.appendChild(box);
    const off = document.querySelector('meta[name="studio-reload"]');
    send("ready", {
      path: here(),
      title: document.title,
      auto_reload: !(off && (off.getAttribute("content") || "").trim().toLowerCase() === "off"),
    });
  };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", ready);
  else ready();
})();
`;

// A project file, answering byte ranges (206) as the real file server does, so a video player can seek.
// `?download=1` asks for a download instead of showing the file.
function sendProjectFile(req, res, url, rawPath, body, type, headers) {
  const extra = { ...headers, "Content-Type": type, "Accept-Ranges": "bytes" };
  if (url.searchParams.get("download") === "1") {
    extra["Content-Disposition"] = `attachment; filename="${path.basename(rawPath).replace(/["\\\r\n]/g, "_")}"`;
  }
  const range = /^bytes=(\d*)-(\d*)$/.exec(req.headers.range ?? "");
  if (range && (range[1] || range[2])) {
    const size = body.length;
    let start = range[1] ? Number(range[1]) : Math.max(0, size - Number(range[2]));
    let end = range[1] && range[2] ? Math.min(Number(range[2]), size - 1) : size - 1;
    if (start >= size || start > end) {
      res.writeHead(416, { ...extra, "Content-Range": `bytes */${size}` });
      return res.end();
    }
    res.writeHead(206, { ...extra, "Content-Range": `bytes ${start}-${end}/${size}`, "Content-Length": end - start + 1 });
    return res.end(body.subarray(start, end + 1));
  }
  res.writeHead(200, { ...extra, "Content-Length": body.length });
  return res.end(body);
}

function servePreview(project, req, res) {
  const url = new URL(req.url, `http://${HOST}`);
  const headers = { "Cache-Control": "no-store" };
  if (url.pathname === "/__studio/bridge.js") {
    res.writeHead(200, { ...headers, "Content-Type": MIME[".js"] });
    return res.end(BRIDGE_JS);
  }
  // Workspace files as they are on disk, also while the (simulated) app owns the URL. No bridge injection.
  if (url.pathname.startsWith("/__studio/files/")) {
    let rawPath;
    try {
      rawPath = decodeURIComponent(url.pathname.slice("/__studio/files/".length));
    } catch {
      res.writeHead(400, headers);
      return res.end("Bad request");
    }
    const parts = rawPath.split("/");
    const file = parts.some((part) => !part || part === "." || part === ".." ) || parts[0] === ".git" ? null : project.files.get(rawPath);
    if (!file) {
      res.writeHead(404, { ...headers, "Content-Type": "text/plain" });
      return res.end("Not found");
    }
    return sendProjectFile(req, res, url, rawPath, file.data, MIME[path.extname(rawPath).toLowerCase()] ?? "application/octet-stream", headers);
  }
  let filePath;
  try {
    filePath = decodeURIComponent(url.pathname).replace(/^\/+/, "");
  } catch {
    res.writeHead(400, headers);
    return res.end("Bad request");
  }
  if (!filePath || filePath.endsWith("/")) filePath += "index.html";
  if (filePath.split("/").some((part) => part === ".." || part === ".git")) {
    res.writeHead(404, headers);
    return res.end("Not found");
  }
  const file = project.files.get(filePath);
  if (!file) {
    if (filePath === "index.html") {
      res.writeHead(200, { ...headers, "Content-Type": MIME[".html"] });
      return res.end(EMPTY_PROJECT_PAGE);
    }
    res.writeHead(404, { ...headers, "Content-Type": "text/plain" });
    return res.end("Not found");
  }
  const type = MIME[path.extname(filePath).toLowerCase()] ?? "application/octet-stream";
  let body = file.data;
  if (type.startsWith("text/html")) {
    const html = body.toString("utf8");
    const scriptTag = '<script src="/__studio/bridge.js"></script>';
    const at = html.toLowerCase().lastIndexOf("</body>");
    body = Buffer.from(at === -1 ? html + scriptTag : html.slice(0, at) + scriptTag + "\n" + html.slice(at));
    res.writeHead(200, { ...headers, "Content-Type": type });
    return res.end(body);
  }
  return sendProjectFile(req, res, url, filePath, body, type, headers);
}

// ---- context from this computer (a stand-in: reads the disk and asks the address for real) ----

const CONTEXT_KEY_FILE = /^(\.env(\..*)?|.*\.pem|.*\.key|id_rsa.*|id_ed25519.*|credentials\.json|\.npmrc|\.netrc)$/i;
const CONTEXT_SKIP = new Set(["node_modules", ".venv", "venv", "dist", "build", ".next", "__pycache__", ".cache", "target", ".git"]);

function contextValueKind(value) {
  if (value.startsWith("/") || value.startsWith("~")) return "path";
  if (value.includes("://")) return "address";
  if (/^(\[[0-9a-f:]+\]|[a-z0-9.-]+):\d+(\/.*)?$/i.test(value)) return "address";
  return null;
}

function folderFound(dir) {
  const names = fs.readdirSync(dir, { withFileTypes: true });
  const keyFiles = names.filter((entry) => entry.isFile() && CONTEXT_KEY_FILE.test(entry.name)).map((entry) => entry.name).sort();
  const shown = names.filter((entry) => entry.name !== ".git" && !keyFiles.includes(entry.name));
  const folders = shown.filter((entry) => entry.isDirectory()).map((entry) => `${entry.name}/`).sort();
  const files = shown.filter((entry) => !entry.isDirectory()).map((entry) => entry.name).sort();
  const top = [...folders, ...files];
  let git = null;
  try {
    const head = fs.readFileSync(path.join(dir, ".git", "HEAD"), "utf8").trim();
    const branch = head.startsWith("ref: refs/heads/") ? head.slice(16) : null;
    let sha = branch ? null : head;
    if (branch) {
      try {
        sha = fs.readFileSync(path.join(dir, ".git", "refs", "heads", branch), "utf8").trim();
      } catch {
        sha = null;
      }
    }
    // The mock does not run git, so the commit's subject is not known: null, not made up.
    git = { branch, head: sha, subject: null };
  } catch {
    git = null;
  }
  let framework = null;
  try {
    const pkg = JSON.parse(fs.readFileSync(path.join(dir, "package.json"), "utf8"));
    const deps = { ...pkg.dependencies, ...pkg.devDependencies };
    if (deps.next) framework = { id: "next", label: "Next.js" };
    else if (deps.vite) framework = { id: "vite", label: "Vite" };
    else framework = { id: "node", label: "Node" };
  } catch {
    if (fs.existsSync(path.join(dir, "pyproject.toml")) || fs.existsSync(path.join(dir, "requirements.txt"))) {
      framework = { id: "python", label: "Python" };
    }
  }
  // Counted by walking (the real server asks git); stops at 50,000 files or 2 seconds.
  let count = 0;
  let bytes = 0;
  let partial = false;
  const started = Date.now();
  const stack = [dir];
  while (stack.length) {
    if (count >= 50000) {
      partial = true;
      break;
    }
    if (Date.now() - started > 2000) {
      return { git, framework, files: null, bytes: null, partial: true, key_files: keyFiles, top: top.slice(0, 40), top_more: Math.max(0, top.length - 40) };
    }
    const current = stack.pop();
    let entries = [];
    try {
      entries = fs.readdirSync(current, { withFileTypes: true });
    } catch {
      continue;
    }
    for (const entry of entries) {
      if (CONTEXT_SKIP.has(entry.name)) continue;
      const full = path.join(current, entry.name);
      if (entry.isDirectory()) stack.push(full);
      else if (entry.isFile()) {
        count += 1;
        try {
          bytes += fs.statSync(full).size;
        } catch {
          /* gone since */
        }
      }
    }
  }
  return { git, framework, files: count, bytes, partial, key_files: keyFiles, top: top.slice(0, 40), top_more: Math.max(0, top.length - 40) };
}

async function addressFound(address) {
  const host = new URL(address).hostname;
  const thisComputer = ["localhost", "127.0.0.1", "[::1]", "::1"].includes(host);
  try {
    const answer = await fetch(address, { redirect: "follow", signal: AbortSignal.timeout(5000) });
    const text = (answer.headers.get("content-type") ?? "").includes("html") ? (await answer.text()).slice(0, 65536) : "";
    const title = /<title[^>]*>([^<]*)<\/title>/i.exec(text)?.[1]?.trim() || null;
    return { reachable: true, status: answer.status, title, this_computer: thisComputer };
  } catch {
    return { reachable: false, error: `Nothing answered at ${address}.`, status: null, title: null, this_computer: thisComputer };
  }
}

async function inspectContext(raw) {
  const value = typeof raw === "string" ? raw.trim() : "";
  const kind = contextValueKind(value);
  if (!kind) {
    throw new HttpError(400, "invalid_request", "Type a folder's full path (~/dev/my-app) or an address (http://localhost:3000).");
  }
  if (kind === "address") {
    let address = value.includes("://") ? value : `http://${value}`;
    const refuse = (sentence) => ({ kind: "address", path: null, address, label: address, refused: sentence, found: null, notes: [] });
    if (address.length > 2000) return refuse("The address is longer than 2,000 characters.");
    let url;
    try {
      url = new URL(address);
    } catch {
      return refuse("This is not an address the studio can read.");
    }
    if (url.protocol !== "http:" && url.protocol !== "https:") return refuse("Only http and https addresses can be context.");
    if (url.username || url.password) return refuse("Leave the user name and password out of the address: the studio never keeps one.");
    url.hash = "";
    address = url.toString();
    if (!value.includes("/", value.indexOf("://") + 3) && address.endsWith("/") && url.pathname === "/" && !url.search) {
      address = address.slice(0, -1);
    }
    const found = await addressFound(address);
    const notes = [];
    if (!found.reachable) notes.push("Nothing answered there now. It can still be added: the app may be started later.");
    if (!found.this_computer) notes.push("The agent reaches this address from the studio's computer, not from your device.");
    return { kind: "address", path: null, address, label: url.host, refused: null, found, notes };
  }
  const full = path.resolve(value.startsWith("~") ? path.join(os.homedir(), value.slice(1)) : value);
  const refuse = (sentence, k = "folder") => ({ kind: k, path: full, address: null, label: path.basename(full) || full, refused: sentence, found: null, notes: [] });
  let real;
  let stat;
  try {
    real = fs.realpathSync(full);
    stat = fs.statSync(real);
  } catch {
    return refuse("Nothing is at this path.");
  }
  const label = path.basename(real) || real;
  if (stat.isFile()) {
    if (CONTEXT_KEY_FILE.test(label)) {
      return { ...refuse("This is a key file. The studio does not hand key files to the agent as context.", "file"), path: real };
    }
    return { kind: "file", path: real, address: null, label, refused: null, found: { bytes: stat.size }, notes: [] };
  }
  if (!stat.isDirectory()) return { ...refuse("This is neither a regular file nor a folder.", "file"), path: real };
  const home = os.homedir();
  if (real === "/" || real === home || home.startsWith(`${real}/`)) {
    return { ...refuse(real === home ? "This is the home folder. Choose a folder inside it." : "This folder holds the home folder. Choose a folder inside it."), path: real };
  }
  let found;
  try {
    found = folderFound(real);
  } catch {
    return { ...refuse("The studio cannot read this folder."), path: real };
  }
  const notes = [];
  if (found.files !== null && found.files > 5000) {
    notes.push(`About ${found.files.toLocaleString("en")} files: the agent searches it and reads only what a question needs.`);
  }
  if (found.key_files.length) notes.push(`Key files at its top (${found.key_files.join(", ")}): the agent is told not to open them.`);
  return { kind: "folder", path: real, address: null, label, refused: null, found, notes };
}

function contextItemState(item) {
  if (item.kind === "address") return Promise.resolve(addressFound(item.address)).then((found) => found.reachable
    ? { ok: true, problem: null }
    : { ok: false, problem: `Nothing answered at ${item.address} just now.` });
  try {
    fs.accessSync(item.path, fs.constants.R_OK);
    return Promise.resolve({ ok: true, problem: null });
  } catch {
    return Promise.resolve({ ok: false, problem: `Nothing can be read at ${item.path} now.` });
  }
}

function contextLabelNote(body, { label, note }) {
  if (body.label !== undefined) {
    const value = String(body.label).trim();
    if (!value || value.length > 60) throw new HttpError(400, "invalid_request", "The label must be 1 to 60 characters.");
    label = value;
  }
  if (body.note !== undefined) {
    const value = String(body.note).trim();
    if (value.length > 500) throw new HttpError(400, "invalid_request", "The note can be at most 500 characters.");
    note = value;
  }
  return { label, note };
}

// ---- API ---------------------------------------------------------------------------------------

function readBody(req, limit = 30 * 1024 * 1024) {
  return new Promise((resolve, reject) => {
    const chunks = [];
    let size = 0;
    req.on("data", (chunk) => {
      size += chunk.length;
      if (size > limit) {
        reject(new HttpError(413, "too_large", "That upload is larger than the 25 MB limit."));
        req.destroy();
      } else chunks.push(chunk);
    });
    req.on("end", () => resolve(Buffer.concat(chunks)));
    req.on("error", reject);
  });
}

async function readJson(req) {
  const body = await readBody(req, 4 * 1024 * 1024);
  if (!body.length) return {};
  try {
    return JSON.parse(body.toString("utf8"));
  } catch {
    throw new HttpError(400, "bad_json", "The request body is not valid JSON.");
  }
}

function parseMultipart(body, contentType) {
  const boundary = /boundary=(?:"([^"]+)"|([^;]+))/.exec(contentType ?? "")?.slice(1).find(Boolean);
  if (!boundary) throw new HttpError(400, "bad_upload", "The upload is not multipart form data.");
  const fields = {};
  const marker = Buffer.from(`--${boundary}`);
  let at = body.indexOf(marker);
  while (at !== -1) {
    const next = body.indexOf(marker, at + marker.length);
    if (next === -1) break;
    const part = body.subarray(at + marker.length + 2, next - 2);
    const split = part.indexOf("\r\n\r\n");
    if (split !== -1) {
      const head = part.subarray(0, split).toString("utf8");
      const name = /name="([^"]*)"/.exec(head)?.[1];
      const filename = /filename="([^"]*)"/.exec(head)?.[1];
      if (name) fields[name] = { filename, data: part.subarray(split + 4) };
    }
    at = next;
  }
  return fields;
}

function json(res, status, body) {
  const data = body === undefined ? "" : JSON.stringify(body);
  res.writeHead(status, { "Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store" });
  res.end(data);
}

function requireIdle(project) {
  if (project.turn) throw new HttpError(409, "turn_running", "The agent is still working. Wait for it to finish or stop it first.");
}

function health() {
  return {
    ok: true,
    version: "0.0.0-mock",
    fake: true,
    // The mock "transcribes" by returning a fixed, clearly marked sentence; no audio is read.
    transcription: { available: true, reason: null },
    connections: [
      {
        id: "fake",
        label: "Scripted (mock server)",
        kind: "fake",
        available: true,
        reason: null,
        models: [
          { id: "scripted", label: "Scripted" },
          { id: "scripted-fast", label: "Scripted fast" },
        ],
        default_model: "scripted",
      },
      {
        id: "subscription",
        label: "Claude subscription",
        kind: "claude-subscription",
        available: false,
        reason: "Not available in the mock server.",
        models: [
          { id: "opus", label: "Opus" },
          { id: "sonnet", label: "Sonnet" },
        ],
        default_model: "opus",
      },
    ],
  };
}

async function handleApi(req, res, url) {
  const method = req.method ?? "GET";
  const segments = url.pathname.replace(/^\/api\/?/, "").split("/").filter(Boolean).map(decodeURIComponent);

  if (method !== "GET" && method !== "HEAD" && !ALLOWED_ORIGINS.has(req.headers.origin ?? "")) {
    throw new HttpError(403, "bad_origin", "This request did not come from the studio.");
  }

  if (segments[0] === "health" && method === "GET") return json(res, 200, health());

  if (segments[0] === "context" && segments[1] === "inspect" && segments.length === 2 && method === "POST") {
    const body = await readJson(req);
    return json(res, 200, await inspectContext(body.value));
  }

  if (segments[0] === "library" && segments.length === 1 && method === "GET") return json(res, 200, library());

  if (segments[0] === "library" && segments[1] === "skills" && segments[2] === "detail" && segments.length === 3 && method === "GET") {
    return json(res, 200, skillFull(indexedSkill(url.searchParams.get("id") ?? "")));
  }
  if (segments[0] === "library" && segments[1] === "skills" && segments[2] === "file" && segments.length === 3 && method === "GET") {
    const skill = indexedSkill(url.searchParams.get("id") ?? "");
    const file = mockSkillFolder(skill).find((item) => item.path === url.searchParams.get("path"));
    if (!file) throw new HttpError(404, "unknown_file", "That file is not part of the skill.");
    const info = skillFileInfo(file);
    if (url.searchParams.get("raw") === "1") {
      if (info.kind !== "picture") throw new HttpError(415, "not_shown", "Only a picture is served as itself.");
      res.writeHead(200, { "Content-Type": "image/png", "Cache-Control": "no-store" });
      return res.end(file.data);
    }
    if (info.kind === "other") throw new HttpError(415, "not_shown", "Export the skill to open this file.");
    if (info.kind === "picture") return json(res, 200, info);
    return json(res, 200, { ...info, text: file.text ?? "", cut: false });
  }
  if (segments[0] === "library" && segments[1] === "skills" && segments[2] === "ask" && segments.length === 3 && method === "POST") {
    const body = await readJson(req);
    const skill = indexedSkill(String(body.id ?? ""));
    const text = typeof body.text === "string" ? body.text.trim() : "";
    const folder = `/mock/skills/${skill.name}`;
    let name;
    let request;
    if (body.about === "explain") {
      if (text.length > 2000) throw new HttpError(400, "invalid_request", "text: at most 2,000 characters.");
      name = `About the skill: ${skill.name}`;
      request = `Explain the skill “${skill.name}” from my library (use the make-a-skill skill, "Explaining a skill"). Its folder is ${folder}. Read SKILL.md and every other file in it before you write.${text ? `\n\nWhat I want to know: ${text}` : ""}`;
    } else if (body.about === "change") {
      if (skill.source !== "yours") throw new HttpError(409, "not_yours", "Only your own skills can be changed. Make your own copy first.");
      if (!text) throw new HttpError(400, "invalid_request", "text: say what should change.");
      name = `Skill: ${skill.name}`;
      request = `Change my skill “${skill.name}” (use the make-a-skill skill, "Changing a skill"). Its folder is ${folder}.\n\n${text}`;
    } else {
      throw new HttpError(400, "invalid_request", "about: explain or change.");
    }
    const project = createProject(name);
    const turnId = beginTurn(project, { text: request, connection: project.record.connection, model: project.record.model });
    return json(res, 202, { project_id: project.record.id, turn_id: turnId });
  }

  if (segments[0] === "transcribe" && segments.length === 1 && method === "POST") {
    const fields = parseMultipart(await readBody(req), req.headers["content-type"]);
    if (!fields.file?.filename) throw new HttpError(400, "bad_upload", "No recording was sent.");
    if (fields.file.data.length > 25 * 1024 * 1024) throw new HttpError(413, "file_too_large", "The recording is larger than 25 MB.");
    await new Promise((resolve) => setTimeout(resolve, 700 * Math.max(SPEED, 0.3)));
    // An empty recording "hears" nothing, so the empty-text path can be tried too.
    const text = fields.file.data.length < 200 ? "" : "(mock) Make the heading shorter and move the chart up.";
    return json(res, 200, { text, seconds: 0.7 });
  }

  if (segments[0] === "skills") {
    if (segments[1] === "copy" && segments.length === 2 && method === "POST") {
      const body = await readJson(req);
      const from = indexedSkill(String(body.from ?? ""));
      const name = typeof body.name === "string" ? body.name.trim() : "";
      if (!name || name.length > 60 || !NAME_RE.test(name)) throw new HttpError(400, "invalid_request", "name: lower-case letters, digits, - and _, 1–60 characters.");
      const folder = mockSkillFolder(from);
      const scripts = folder.filter((file) => file.script).map((file) => file.path);
      const preview = {
        name,
        description: from.description,
        body_start: (folder.find((file) => file.path === "SKILL.md")?.text ?? "").slice(0, 1200),
        files: folder.map((file) => ({ path: file.path, size: skillFileSize(file) })),
        scripts,
        size: folder.reduce((sum, file) => sum + skillFileSize(file), 0),
        exists: yourSkills.has(name),
        taken_by_studio: STUDIO_SKILLS.some((skill) => skill.name === name),
      };
      if (preview.taken_by_studio) throw new HttpError(409, "name_taken_by_studio", `One of the studio’s own skills is called “${name}”. Choose another name.`, preview);
      if (preview.exists && !body.replace) throw new HttpError(409, "name_taken", `You already have a skill called “${name}”.`, preview);
      if (scripts.length && !body.allow_scripts) throw new HttpError(409, "scripts_need_confirmation", "This skill brings scripts: tick the box to accept them.", preview);
      const skill = { id: `yours:${name}`, name, label: name, description: from.description, source: "yours", default: true };
      yourSkills.set(name, skill);
      yourSkillFolders.set(name, folder.map((file) => (file.path === "SKILL.md" ? { ...file, text: file.text.replace(/^name: .*$/m, `name: ${name}`) } : { ...file })));
      return json(res, 201, skill);
    }
    if (segments.length === 2 && method === "GET") {
      const skill = yourSkills.get(segments[1]);
      if (!skill) throw new HttpError(404, "unknown_skill", "Only your own skills open here.");
      const full = skillFull(skill);
      return json(res, 200, { ...skill, body: full.body, files: full.files.filter((file) => file.path !== "SKILL.md").map(({ path, size }) => ({ path, size })) });
    }
    if (segments.length === 2 && method === "PUT") {
      const skill = yourSkills.get(segments[1]);
      if (!skill) throw new HttpError(404, "unknown_skill", "Only your own skills can be changed.");
      const body = await readJson(req);
      if (body.description === undefined && body.body === undefined) throw new HttpError(400, "invalid_request", "Nothing to change.");
      if (typeof body.description === "string") skill.description = body.description.trim();
      const full = skillFull(skill);
      const instructions = typeof body.body === "string" ? body.body : full.body;
      const rest = (yourSkillFolders.get(skill.name) ?? []).filter((file) => file.path !== "SKILL.md");
      yourSkillFolders.set(skill.name, [{ path: "SKILL.md", text: skillMarkdown(skill, instructions), script: false }, ...rest]);
      return json(res, 200, { ...skill, body: instructions, files: rest.map((file) => ({ path: file.path, size: skillFileSize(file) })) });
    }
    if (segments.length === 1 && method === "POST") {
      const body = await readJson(req);
      const name = typeof body.name === "string" ? body.name.trim() : "";
      if (!name || name.length > 60 || !NAME_RE.test(name)) throw new HttpError(400, "invalid_request", "name: lower-case letters, digits, - and _, 1–60 characters.");
      if (typeof body.body !== "string" || !body.body.trim()) throw new HttpError(400, "invalid_request", "body: the instructions are required.");
      if (yourSkills.has(name)) throw new HttpError(409, "name_taken", "A skill with that name already exists.");
      const skill = { id: `yours:${name}`, name, label: name, description: String(body.description ?? ""), source: "yours", default: true };
      yourSkills.set(name, skill);
      yourSkillFolders.set(name, [{ path: "SKILL.md", text: skillMarkdown(skill, body.body), script: false }]);
      return json(res, 201, skill);
    }
    if (segments.length === 2 && method === "DELETE") {
      if (!yourSkills.has(segments[1])) throw new HttpError(404, "unknown_skill", "Only skills you added can be deleted.");
      yourSkills.delete(segments[1]);
      yourSkillFolders.delete(segments[1]);
      for (const project of projects.values()) delete project.record.library.skills[`yours:${segments[1]}`];
      res.writeHead(204);
      return res.end();
    }
    throw new HttpError(404, "not_found", "No such endpoint.");
  }

  if (segments[0] === "mcp-servers" && segments[1] === "suggest" && segments.length === 2 && method === "POST") {
    const body = await readJson(req);
    const text = typeof body.text === "string" ? body.text.trim() : "";
    if (!text || text.length > 2000) throw new HttpError(400, "invalid_request", "text: 1 to 2,000 characters.");
    await new Promise((resolve) => setTimeout(resolve, 900 * Math.max(SPEED, 0.3)));
    const days = (n) => new Date(Date.now() - n * 864e5).toISOString();
    const first = allServers()[0];
    return json(res, 200, {
      have: first
        ? [{ id: first.id, name: first.name, label: first.label, source: first.source, origin: first.origin ?? null, description: first.description, state: first.state, fix: first.fix ?? null }]
        : [],
      suggestions: [
        {
          name: "mock-notion",
          label: "(mock) Notion",
          description: "(mock) Reads and searches the pages in a Notion workspace.",
          transport: "stdio",
          url: null,
          command: "npx",
          args: ["-y", "@mock/notion-mcp-server"],
          secrets: [{ name: "NOTION_TOKEN", where: "(mock) Notion → Settings → Connections → Develop or manage integrations." }],
          why: "(mock) You asked to read and search Notion pages.",
          sign_in: false,
          check: { kind: "npm", package: "@mock/notion-mcp-server", found: true, version: "1.4.2", description: "(mock)", published: days(21), first_published: days(700), downloads_week: 41200, repository: null },
        },
        {
          name: "mock-linear",
          label: "(mock) Linear",
          description: "(mock) Reads and updates Linear issues.",
          transport: "http",
          url: "https://mcp.mock-linear.example/mcp",
          command: null,
          args: [],
          secrets: [],
          why: "(mock) It signs in with your Linear account.",
          sign_in: true,
          check: { kind: "address", found: true, status: 401 },
        },
      ],
      left_out: [{ label: "(mock) notion-tools", reason: "not on npm" }],
      asked: { connection: "fake", model: "mock-small" },
      note: null,
    });
  }

  if (segments[0] === "mcp-servers") {
    if (segments.length === 1 && method === "POST") {
      const server = readServerFields(await readJson(req));
      server.id = `mcp_${crypto.randomBytes(4).toString("hex")}`;
      server.source = "yours";
      server.last_test = null;
      yourServers.set(server.id, server);
      return json(res, 201, server);
    }
    const server = allServers().find((item) => item.id === segments[1]);
    if (!server) throw new HttpError(404, "unknown_mcp_server", "That server does not exist.");
    if (segments[2] === "test" && segments.length === 3 && method === "POST") {
      await new Promise((resolve) => setTimeout(resolve, 600 * Math.max(SPEED, 0.2)));
      const result = scriptedMcpTest(server);
      if (server.source === "yours") server.last_test = { ...result, at: now() };
      return json(res, 200, result);
    }
    if (server.source !== "yours") throw new HttpError(400, "invalid_request", "Only servers you added can be changed.");
    if (segments.length === 2 && method === "PATCH") {
      const next = readServerFields(await readJson(req), server);
      yourServers.set(server.id, next);
      return json(res, 200, next);
    }
    if (segments.length === 2 && method === "DELETE") {
      yourServers.delete(server.id);
      for (const project of projects.values()) delete project.record.library.mcp_servers[server.id];
      res.writeHead(204);
      return res.end();
    }
    throw new HttpError(404, "not_found", "No such endpoint.");
  }

  if (segments[0] === "data-sources") {
    if (segments.length === 1 && method === "GET") return json(res, 200, [...dataSources.values()].map(publicSource));
    if (segments.length === 1 && method === "POST") {
      const source = readSourceFields(await readJson(req));
      source.id = `ds_${crypto.randomBytes(4).toString("hex")}`;
      source.created_at = now();
      source.last_test = null;
      dataSources.set(source.id, source);
      return json(res, 201, publicSource(source));
    }
    if (segments[1] === "test" && segments.length === 2 && method === "POST") {
      return json(res, 200, scriptedTest(readSourceFields(await readJson(req), null, { checkName: false })));
    }
    const source = dataSources.get(segments[1]);
    if (!source) throw new HttpError(404, "unknown_data_source", "That data source does not exist.");
    if (segments.length === 2 && method === "PATCH") {
      const next = readSourceFields(await readJson(req), source);
      dataSources.set(source.id, next);
      return json(res, 200, publicSource(next));
    }
    if (segments.length === 2 && method === "DELETE") {
      dataSources.delete(source.id);
      for (const project of projects.values()) {
        project.record.data_sources = project.record.data_sources.filter((item) => item !== source.id);
      }
      res.writeHead(204);
      return res.end();
    }
    if (segments[2] === "test" && segments.length === 3 && method === "POST") {
      await new Promise((resolve) => setTimeout(resolve, 400 * Math.max(SPEED, 0.2)));
      const result = scriptedTest(source);
      source.last_test = { ok: result.ok, at: now(), message: result.message };
      return json(res, 200, result);
    }
    throw new HttpError(404, "not_found", "No such endpoint.");
  }

  if (segments[0] === "templates" && segments[1] === "import" && segments.length === 2 && method === "POST") {
    const fields = parseMultipart(await readBody(req), req.headers["content-type"]);
    if (!fields.file?.filename || !/\.zip$/i.test(fields.file.filename)) {
      throw new HttpError(400, "invalid_template", "That file is not a studio template.");
    }
    // The mock cannot read the zip: a file name with "app" in it stands for a template with code.
    const hasCode = /app/i.test(fields.file.filename);
    const confirmed = fields.confirm?.data.toString("utf8") === "1";
    if (hasCode && !confirmed) {
      throw new HttpError(409, "needs_confirmation", "(mock) This template contains an app with code that runs on this machine when a project is started from it. Import it only if you trust where it came from.");
    }
    const label = `(mock) Imported ${fields.file.filename.replace(/\.studio-template\.zip$|\.zip$/i, "")}`;
    const record = addTemplate({
      label,
      kind: hasCode ? "app" : "page",
      imported: true,
      has_code: hasCode,
      files: { "TEMPLATE.md": `# ${label}\n` },
    });
    return json(res, 201, publicTemplate(record));
  }

  if (segments[0] === "templates" && segments.length >= 2 && segments[1] !== "import") {
    const record = yourTemplates.get(segments[1]);
    if (!record) {
      if (TEMPLATES.some((item) => item.id === segments[1])) throw new HttpError(400, "builtin_template", "Built-in templates can’t be changed.");
      throw new HttpError(404, "unknown_template", "That template does not exist.");
    }
    if (segments.length === 2 && method === "PATCH") {
      const body = await readJson(req);
      if ("label" in body) {
        if (typeof body.label !== "string" || !body.label.trim()) throw new HttpError(400, "invalid_template", "The name can’t be empty.");
        record.label = body.label.trim();
      }
      if ("description" in body) record.description = String(body.description ?? "");
      return json(res, 200, publicTemplate(record));
    }
    if (segments.length === 2 && method === "DELETE") {
      yourTemplates.delete(record.id);
      res.writeHead(204);
      return res.end();
    }
    if (segments[2] === "preview" && method === "GET") {
      if (!record.preview) throw new HttpError(404, "no_preview", "This template has no preview.");
      res.writeHead(200, { "Content-Type": "image/svg+xml", "Cache-Control": "no-store" });
      return res.end(record.preview);
    }
    if (segments[2] === "export" && method === "GET") {
      const body = zip(Object.entries(record.files).map(([path, text]) => [path, Buffer.from(text)]));
      res.writeHead(200, {
        "Content-Type": "application/zip",
        "Content-Disposition": `attachment; filename="${record.label.replace(/[^\w.-]+/g, "-")}.studio-template.zip"`,
        "Content-Length": body.length,
      });
      return res.end(body);
    }
    throw new HttpError(404, "not_found", "No such endpoint.");
  }

  if (segments[0] === "templates" && segments.length === 1 && method === "GET") {
    return json(res, 200, allTemplates());
  }

  if (segments[0] !== "projects") throw new HttpError(404, "not_found", "No such endpoint.");

  if (segments.length === 1) {
    if (method === "GET") {
      const list = [...projects.values()].map((project) => project.record);
      list.sort((a, b) => b.updated_at.localeCompare(a.updated_at));
      return json(res, 200, list);
    }
    if (method === "POST") {
      const body = await readJson(req);
      const template = typeof body.template === "string" ? body.template : "blank";
      if (!TEMPLATES.some((item) => item.id === template) && !yourTemplates.has(template)) {
        throw new HttpError(400, "unknown_template", "That template does not exist.");
      }
      return json(res, 201, createProject(typeof body.name === "string" ? body.name.trim() : "", { template }).record);
    }
  }

  const project = getProject(segments[1]);
  const rest = segments.slice(2).join("/");

  if (rest === "") {
    if (method === "GET") return json(res, 200, project.record);
    if (method === "PATCH") {
      const body = await readJson(req);
      if (typeof body.name === "string" && body.name.trim()) project.record.name = body.name.trim().slice(0, 120);
      if (typeof body.connection === "string") {
        const connection = health().connections.find((item) => item.id === body.connection);
        if (!connection) throw new HttpError(400, "unknown_connection", "That connection does not exist.");
        project.record.connection = connection.id;
      }
      if ("model" in body) project.record.model = body.model ?? null;
      if ("approvals" in body) {
        if (body.approvals !== "ask" && body.approvals !== "allow") {
          throw new HttpError(400, "bad_request", "approvals must be \"ask\" or \"allow\".");
        }
        project.record.approvals = body.approvals;
      }
      if (body.turn_options && typeof body.turn_options === "object") {
        const depth = ["quick", "standard", "deep"].includes(body.turn_options.depth) ? body.turn_options.depth : "standard";
        project.record.turn_options = { depth, verify: body.turn_options.verify === true, ask: body.turn_options.ask === true };
      }
      if ("instructions" in body) {
        if (body.instructions !== null && typeof body.instructions !== "string") {
          throw new HttpError(400, "invalid_request", "Instructions are plain text.");
        }
        const text = (body.instructions ?? "").replace(/\r\n?/g, "\n").split("\n").map((line) => line.replace(/[ \t]+$/, "")).join("\n").trim();
        if (Array.from(text).length > 4000) {
          throw new HttpError(400, "invalid_request", `Instructions can be 4,000 characters at most; this is ${Array.from(text).length.toLocaleString("en-US")}.`);
        }
        project.record.instructions = text;
      }
      project.record.updated_at = now();
      return json(res, 200, project.record);
    }
    if (method === "DELETE") {
      project.turn?.cancel?.();
      clearAppTimers(project);
      project.server.close();
      project.server.closeAllConnections?.();
      for (const listener of project.listeners) listener.end();
      projects.delete(project.record.id);
      res.writeHead(204);
      return res.end();
    }
  }

  if (rest === "files" && method === "GET") {
    const list = [...project.files].map(([filePath, file]) => ({ path: filePath, size: file.data.length, modified_at: file.modified_at }));
    list.sort((a, b) => a.path.localeCompare(b.path));
    return json(res, 200, list);
  }

  if (rest === "file" && method === "GET") {
    const filePath = checkPath(url.searchParams.get("path"));
    const file = project.files.get(filePath);
    if (!file) throw new HttpError(404, "file_not_found", "That file does not exist.");
    if (file.data.length > 1024 * 1024 || file.data.includes(0)) {
      throw new HttpError(415, "not_text", "That file is not text, or is larger than 1 MB.");
    }
    return json(res, 200, { path: filePath, content: file.data.toString("utf8"), hash: sha256(file.data) });
  }

  if (rest === "file" && method === "PUT") {
    requireIdle(project);
    const body = await readJson(req);
    const filePath = checkPath(body.path);
    if (typeof body.content !== "string") throw new HttpError(400, "bad_request", "The file content is missing.");
    const current = project.files.get(filePath);
    if (body.base_hash && current && sha256(current.data) !== body.base_hash) {
      throw new HttpError(409, "stale_file", "The file changed since you opened it.");
    }
    writeFile(project, filePath, body.content);
    emit(project, "files_changed", { paths: [filePath] });
    const checkpoint = commit(project, "user", `Edit ${filePath}`, null);
    if (filePath === "studio.json") syncApp(project);
    return json(res, 200, { path: filePath, hash: sha256(Buffer.from(body.content)), checkpoint });
  }

  if (rest === "upload" && method === "POST") {
    // Step 2: uploads are allowed during a turn; they are committed with the turn's checkpoint.
    const fields = parseMultipart(await readBody(req), req.headers["content-type"]);
    if (!fields.file?.filename) throw new HttpError(400, "bad_upload", "No file was sent.");
    if (fields.file.data.length > 25 * 1024 * 1024) throw new HttpError(413, "too_large", "That upload is larger than the 25 MB limit.");
    const dir = fields.dir ? fields.dir.data.toString("utf8").replace(/^\/+|\/+$/g, "") : "";
    const filePath = checkPath(dir ? `${dir}/${path.basename(fields.file.filename)}` : path.basename(fields.file.filename));
    writeFile(project, filePath, Buffer.from(fields.file.data));
    emit(project, "files_changed", { paths: [filePath] });
    const checkpoint = project.turn ? null : commit(project, "user", `Upload ${filePath}`, null);
    if (filePath === "studio.json" && !project.turn) syncApp(project);
    return json(res, 200, { path: filePath, size: fields.file.data.length, checkpoint });
  }

  if (rest === "dom-edits" && method === "POST") {
    requireIdle(project);
    const body = await readJson(req);
    let pagePath = String(body.path ?? "/").replace(/^\/+/, "");
    if (!pagePath || pagePath.endsWith("/")) pagePath += "index.html";
    const filePath = checkPath(pagePath);
    const file = project.files.get(filePath);
    if (!file) throw new HttpError(404, "file_not_found", "That page's file does not exist.");
    const source = file.data.toString("utf8");
    const beforeText = escapeHtml(String(body.before_text ?? "")).replace(/&quot;/g, '"');
    const afterText = escapeHtml(String(body.after_text ?? "")).replace(/&quot;/g, '"');
    const count = beforeText ? source.split(beforeText).length - 1 : 0;
    if (count === 0) throw new HttpError(409, "edit_not_found", "That text was not found in the page's source file.");
    if (count > 1) throw new HttpError(409, "ambiguous_edit", "That text appears more than once in the page's source file.");
    writeFile(project, filePath, source.replace(beforeText, () => afterText));
    emit(project, "files_changed", { paths: [filePath] });
    const checkpoint = commit(project, "user", `Edit text in ${filePath}`, null);
    return json(res, 200, { path: filePath, checkpoint });
  }

  if (rest === "versions" && method === "GET") {
    return json(
      res,
      200,
      project.versions
        .map(({ sha, kind, message, created_at, files }) => ({ sha, kind, message, created_at, files }))
        .reverse(),
    );
  }

  if (segments[2] === "versions" && segments[4] === "diff" && method === "GET") {
    return json(res, 200, versionDiff(project, segments[3]));
  }

  if (rest === "restore" && method === "POST") {
    requireIdle(project);
    const body = await readJson(req);
    const version = project.versions.find((item) => item.sha === body.sha);
    if (!version) throw new HttpError(404, "version_not_found", "That version does not exist.");
    const changed = new Set([...project.files.keys(), ...version.tree.keys()]);
    project.files = new Map([...version.tree].map(([filePath, data]) => [filePath, { data, modified_at: now() }]));
    emit(project, "files_changed", { paths: [...changed].sort() });
    const checkpoint = commit(project, "restore", `Restore “${version.message}”`, null);
    return json(res, 200, { checkpoint });
  }

  if (rest === "turns" && method === "POST") {
    const body = await readJson(req);
    const text = String(body.text ?? "").trim();
    if (!text) throw new HttpError(400, "empty_message", "Write a message first.");
    const attachments = Array.isArray(body.attachments) ? body.attachments.map((item) => checkPath(item)) : [];
    const missing = attachments.find((item) => !project.files.has(item));
    if (missing) throw new HttpError(400, "missing_attachment", `The attached file ${missing} is not in the project.`);
    const message = {
      text,
      selection: body.selection ?? null,
      attachments,
      connection: typeof body.connection === "string" ? body.connection : project.record.connection,
      model: "model" in body ? (body.model ?? null) : project.record.model,
    };
    if (project.turn) {
      if (!body.queue) requireIdle(project);
      const queued_id = `q_${crypto.randomBytes(4).toString("hex")}`;
      project.queue.push({ ...message, queued_id });
      emit(project, "turn_queued", { queued_id, ...message });
      return json(res, 202, { queued_id });
    }
    return json(res, 202, { turn_id: beginTurn(project, message) });
  }

  if (segments[2] === "queue" && segments.length === 4 && method === "DELETE") {
    const index = project.queue.findIndex((item) => item.queued_id === segments[3]);
    if (index !== -1) {
      const [item] = project.queue.splice(index, 1);
      emit(project, "turn_unqueued", { queued_id: segments[3], reason: "cancelled", text: item.text });
    }
    res.writeHead(204);
    return res.end();
  }

  if (segments[2] === "turns" && segments[4] === "approvals" && segments.length === 6 && method === "POST") {
    const body = await readJson(req);
    if (body.decision !== "allow" && body.decision !== "deny") {
      throw new HttpError(400, "bad_request", "decision must be \"allow\" or \"deny\".");
    }
    const resolve = project.turn?.id === segments[3] ? project.approvalsWaiting.get(segments[5]) : null;
    if (!resolve) throw new HttpError(404, "unknown_approval", "That approval is not waiting any more.");
    project.approvalsWaiting.delete(segments[5]);
    resolve(body.decision);
    res.writeHead(204);
    return res.end();
  }

  if (segments[2] === "turns" && segments[4] === "questions" && segments.length === 6 && method === "POST") {
    const body = await readJson(req);
    const waiting = project.turn?.id === segments[3] ? project.questionsWaiting.get(segments[5]) : null;
    if (!waiting) throw new HttpError(404, "unknown_question", "That question is no longer waiting for an answer.");
    let answers = null;
    if (body.skip !== true) {
      if (!Array.isArray(body.answers)) throw new HttpError(400, "invalid_answer", "answers must be a list of {id, chosen?, text?}.");
      answers = waiting.questions.map((question) => {
        const given = body.answers.find((item) => item && item.id === question.id) ?? {};
        const labels = question.choices.map((choice) => choice.label);
        const chosen = Array.isArray(given.chosen) ? given.chosen : [];
        if (chosen.some((label) => !labels.includes(label)) || (chosen.length > 1 && !question.multiple)) {
          throw new HttpError(400, "invalid_answer", "chosen must be a list of that question's own choices.");
        }
        return { id: question.id, chosen, text: typeof given.text === "string" ? given.text.trim().slice(0, 2000) : "" };
      });
      if (!answers.some((answer) => answer.chosen.length || answer.text)) {
        throw new HttpError(400, "invalid_answer", "Answer at least one question, or skip them.");
      }
    }
    project.questionsWaiting.delete(segments[5]);
    waiting.resolve(answers);
    res.writeHead(204);
    return res.end();
  }

  if (rest === "template" && method === "POST") {
    if (project.turn) throw new HttpError(409, "turn_running", "Wait for the agent to finish before saving a template.");
    const body = await readJson(req);
    const name = typeof body.name === "string" ? body.name.trim() : "";
    if (!name) throw new HttpError(400, "invalid_template", "Give the template a name.");
    let kind = body.kind ?? (project.app.run ? "app" : "page");
    const files = {};
    if (body.file) {
      if (!project.files.has(body.file)) throw new HttpError(400, "invalid_template", `There is no file ${body.file} in this project.`);
      kind = /\.pptx$/i.test(body.file) ? "deck" : /\.xlsx$/i.test(body.file) ? "workbook" : /\.docx$/i.test(body.file) ? "document" : null;
      if (!kind) throw new HttpError(400, "invalid_template", "Only .pptx, .xlsx and .docx files can be saved as templates.");
      files[body.file] = "(mock) file";
    } else {
      if (!["page", "app"].includes(kind)) throw new HttpError(400, "invalid_template", "That kind of template is not known.");
      for (const [path, file] of project.files) {
        if (/^(sources|checks|attachments|research|data)\//.test(path)) continue;
        files[path] = file.data.toString("utf8");
      }
    }
    files["TEMPLATE.md"] = `# ${name}\n\n${body.description ?? ""}\n`;
    const record = addTemplate({
      label: name,
      description: String(body.description ?? ""),
      kind,
      from_project: project.record.name,
      has_code: kind === "app",
      files,
      preview: mockPreviewSvg(name, "#5b5bd6"),
    });
    return json(res, 201, publicTemplate(record));
  }

  if (rest === "uses-template" && (method === "PUT" || method === "DELETE")) {
    if (project.turn) throw new HttpError(409, "turn_running", "Wait for the agent to finish before changing the template.");
    for (const path of [...project.files.keys()]) if (path.startsWith("template/")) project.files.delete(path);
    if (method === "DELETE") {
      project.record.uses_template = null;
    } else {
      const body = await readJson(req);
      const record = yourTemplates.get(body.template);
      if (!record) throw new HttpError(404, "unknown_template", "That template does not exist.");
      for (const [path, text] of Object.entries(record.files)) writeFile(project, `template/${path}`, text);
      project.record.uses_template = { id: record.id, label: record.label, kind: record.kind };
    }
    emit(project, "files_changed", { paths: ["template/TEMPLATE.md"] });
    return json(res, 200, project.record);
  }

  if (rest === "drawings" && method === "POST") {
    const body = await readJson(req);
    if (!Array.isArray(body.marks) || !body.marks.length) throw new HttpError(400, "invalid_drawing", "The drawing has no marks.");
    const ms = Date.now();
    const filePath = `attachments/drawing-${ms}.json`;
    writeFile(project, filePath, JSON.stringify({ ...body, images: [] }, null, 2));
    emit(project, "files_changed", { paths: [filePath] });
    // The mock has no browser, so it makes no region screenshots.
    return json(res, 200, {
      path: filePath,
      images: [],
      marks: body.marks.length,
      rendered: false,
      note: "(mock) No browser in the mock server, so no screenshots were made. The marks were saved.",
    });
  }

  if (rest === "library" && method === "GET") return json(res, 200, projectLibrary(project));
  if (rest === "library" && method === "PUT") {
    const body = await readJson(req);
    const next = { skills: { ...project.record.library.skills }, mcp_servers: { ...project.record.library.mcp_servers } };
    const skills = new Map(allSkills().map((skill) => [skill.id, skill]));
    const servers = new Set(allServers().map((server) => server.id));
    for (const [id, on] of Object.entries(body.skills ?? {})) {
      const skill = skills.get(id);
      if (!skill) throw new HttpError(400, "invalid_request", `Unknown skill ${id}.`);
      if (on === skill.default) delete next.skills[id];
      else next.skills[id] = Boolean(on);
    }
    for (const [id, on] of Object.entries(body.mcp_servers ?? {})) {
      if (!servers.has(id)) throw new HttpError(400, "invalid_request", `Unknown MCP server ${id}.`);
      if (on) next.mcp_servers[id] = true;
      else delete next.mcp_servers[id];
    }
    if (Object.values(next.mcp_servers).filter(Boolean).length > 12) {
      throw new HttpError(400, "invalid_request", "A project can use at most 12 MCP servers.");
    }
    project.record.library = next;
    await new Promise((resolve) => setTimeout(resolve, 150));
    return json(res, 200, projectLibrary(project));
  }

  if (rest === "context" && method === "GET") {
    const items = project.record.context;
    const states = await Promise.all(items.map((item) => contextItemState(item)));
    return json(res, 200, items.map((item, index) => ({ ...item, state: states[index] })));
  }
  if (rest === "context" && method === "POST") {
    const body = await readJson(req);
    const inspection = await inspectContext(body.value);
    if (inspection.refused) throw new HttpError(400, "context_refused", inspection.refused);
    const items = project.record.context;
    const where = inspection.path ?? inspection.address;
    if (items.some((item) => (item.path ?? item.address) === where)) {
      throw new HttpError(409, "already_added", "This project already has that as context.");
    }
    if (items.length >= 12) throw new HttpError(400, "too_many", "A project can hold at most 12 context items.");
    const { label, note } = contextLabelNote(body, { label: inspection.label.slice(0, 60), note: "" });
    // eslint-disable-next-line no-unused-vars
    const { top, top_more, ...found } = inspection.found ?? {};
    const item = {
      id: `cx_${crypto.randomBytes(4).toString("hex")}`,
      kind: inspection.kind,
      path: inspection.path,
      address: inspection.address,
      label,
      note,
      added_at: now(),
      found: inspection.found ? found : null,
    };
    items.push(item);
    return json(res, 201, item);
  }
  if (segments[2] === "context" && segments.length === 4 && (method === "PATCH" || method === "DELETE")) {
    const items = project.record.context;
    const item = items.find((entry) => entry.id === segments[3]);
    if (!item) throw new HttpError(404, "unknown_context", "That context item is not in this project.");
    if (method === "DELETE") {
      project.record.context = items.filter((entry) => entry !== item);
      res.writeHead(204);
      return res.end();
    }
    Object.assign(item, contextLabelNote(await readJson(req), item));
    return json(res, 200, item);
  }

  if (rest === "data-sources" && method === "GET") {
    return json(
      res,
      200,
      [...dataSources.values()].map((source) => ({ ...publicSource(source), attached: project.record.data_sources.includes(source.id) })),
    );
  }
  if (segments[2] === "data-sources" && segments.length === 4 && (method === "PUT" || method === "DELETE")) {
    if (!dataSources.has(segments[3])) throw new HttpError(404, "unknown_data_source", "That data source does not exist.");
    const list = project.record.data_sources.filter((item) => item !== segments[3]);
    if (method === "PUT") list.push(segments[3]);
    project.record.data_sources = list;
    res.writeHead(204);
    return res.end();
  }

  if (rest === "app" && method === "GET") return json(res, 200, appObject(project));
  if (segments[2] === "app" && ["start", "stop", "restart"].includes(segments[3]) && segments.length === 4 && method === "POST") {
    if (segments[3] === "stop") stopApp(project);
    else startApp(project);
    return json(res, 200, appObject(project));
  }
  if (rest === "app/logs" && method === "GET") {
    if (!project.app.run) throw new HttpError(409, "no_app", "This project has no app.");
    const after = Number(url.searchParams.get("after") ?? 0) || 0;
    const lines = project.app.lines.filter((line) => line.n > after).slice(0, 2000);
    const last = project.app.lines.length ? project.app.lines[project.app.lines.length - 1].n : 0;
    return json(res, 200, { lines, last });
  }

  if (rest === "download" && method === "GET") {
    const entries = [...project.files].map(([filePath, file]) => [filePath, file.data]);
    const body = zip(entries);
    const name = (project.record.name || "project").replace(/[^\w.-]+/g, "-").replace(/^-+|-+$/g, "") || "project";
    res.writeHead(200, {
      "Content-Type": "application/zip",
      "Content-Disposition": `attachment; filename="${name}.zip"`,
      "Content-Length": body.length,
      "Cache-Control": "no-store",
    });
    return res.end(body);
  }

  if (segments[2] === "turns" && segments[4] === "stop" && method === "POST") {
    if (project.turn?.id === segments[3]) {
      project.turn.stopped = true;
      project.turn.cancel?.();
    }
    return json(res, 202, { ok: true });
  }

  if (rest === "events" && method === "GET") {
    const after = Number(req.headers["last-event-id"] ?? url.searchParams.get("after") ?? 0) || 0;
    res.writeHead(200, {
      "Content-Type": "text/event-stream; charset=utf-8",
      "Cache-Control": "no-cache, no-transform",
      Connection: "keep-alive",
      "X-Accel-Buffering": "no",
    });
    res.write(": connected\n\n");
    for (const event of project.events) {
      if (event.seq > after) res.write(`id: ${event.seq}\ndata: ${JSON.stringify(event)}\n\n`);
    }
    project.listeners.add(res);
    const ping = setInterval(() => res.write("event: ping\ndata: {}\n\n"), 15_000);
    req.on("close", () => {
      clearInterval(ping);
      project.listeners.delete(res);
    });
    return;
  }

  throw new HttpError(404, "not_found", "No such endpoint.");
}

function serveStatic(req, res, url) {
  let filePath = path.join(DIST, decodeURIComponent(url.pathname));
  if (!filePath.startsWith(DIST)) filePath = path.join(DIST, "index.html");
  // Single-page app: any path that is not a built file gets index.html.
  if (!fs.existsSync(filePath) || fs.statSync(filePath).isDirectory()) filePath = path.join(DIST, "index.html");
  if (!fs.existsSync(filePath)) {
    res.writeHead(503, { "Content-Type": "text/plain" });
    return res.end("The web UI is not built. Run `npm run build` in studio2/web, or use `npm run dev` on port 5200.");
  }
  const type = MIME[path.extname(filePath).toLowerCase()] ?? "application/octet-stream";
  const immutable = url.pathname.startsWith("/assets/");
  res.writeHead(200, { "Content-Type": type, "Cache-Control": immutable ? "public, max-age=31536000, immutable" : "no-cache" });
  fs.createReadStream(filePath).pipe(res);
}

const server = http.createServer(async (req, res) => {
  const url = new URL(req.url ?? "/", `http://${HOST}:${API_PORT}`);
  if (!url.pathname.startsWith("/api/") && url.pathname !== "/api") return serveStatic(req, res, url);
  try {
    await handleApi(req, res, url);
  } catch (error) {
    if (res.headersSent) return res.end();
    if (error instanceof HttpError) {
      json(res, error.status, { error: { code: error.code, message: error.message, ...(error.details !== undefined ? { details: error.details } : {}) } });
    }
    else {
      console.error(error);
      json(res, 500, { error: { code: "internal_error", message: "The mock server hit an unexpected error." } });
    }
  }
});

// ---- optional sample projects (for looking at the home page) ------------------------------------------

// ---- office files (mock): bytes that are NOT real Office files, and a tiny stand-in viewer ----

const MOCK_OFFICE_BYTES = (name) => Buffer.from(`(mock) This is not a real ${name}; the mock server only stands in for one.\n`);

function mockPreviewHtml(file) {
  return `<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Preview of ${escapeHtml(file)}</title>
<style>
body{margin:0;font:15px system-ui,sans-serif;background:#2b2b30;color:#eee}
header{display:flex;align-items:center;gap:12px;padding:10px 14px;background:#1c1c20}
header b{flex:1}
a{color:#fff;background:#5b5bd6;padding:6px 12px;border-radius:7px;text-decoration:none}
.slide{margin:16px auto;width:min(92%,800px);aspect-ratio:16/9;background:#fff;color:#222;display:grid;place-items:center;border-radius:4px;font-size:22px}
</style></head><body>
<header><b>${escapeHtml(file)} (mock preview)</b><a href="../${escapeHtml(file.split("/").pop())}" download>Download</a></header>
<div class="slide">(mock) Slide 1 — stand-in for a rendered slide</div>
<div class="slide">(mock) Slide 2 — stand-in for a rendered slide</div>
</body></html>
`;
}

function addMockOffice(project) {
  writeFile(project, "files/sample-deck.pptx", MOCK_OFFICE_BYTES("PowerPoint file"));
  writeFile(project, "files/sample-deck.pptx.preview/index.html", mockPreviewHtml("files/sample-deck.pptx"));
  writeFile(project, "files/notes.docx", MOCK_OFFICE_BYTES("Word file"));
}

// ---- media files (mock): a real 3-second H.264 test clip (generated test pattern + tone), its poster, a sound ----

const FIXTURES = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "fixtures");

function addMockMedia(project) {
  for (const [name, target] of [
    ["demo.mp4", "media/demo.mp4"],
    ["demo.jpg", "media/demo.jpg"],
    ["tone.mp3", "media/tone.mp3"],
  ]) {
    try {
      writeFile(project, target, fs.readFileSync(path.join(FIXTURES, name)));
    } catch (error) {
      console.warn(`(mock) could not read fixture ${name}: ${error.message}`);
    }
  }
}

function seed() {
  const samples = [
    ["Coffee subscription market", 6 * 60_000],
    ["Q3 support dashboard", 3 * 3600_000],
    ["Launch plan: Atlas", 26 * 3600_000],
    ["Pricing page ideas", 3 * 86400_000],
    ["Hiring scorecard", 9 * 86400_000],
    ["Untitled", 16 * 86400_000],
  ];
  samples.forEach(([name, ago], index) => {
    const at = new Date(Date.now() - ago).toISOString();
    const project = createProject(name, { createdAt: at });
    if (name === "Untitled") return;
    for (const step of buildSteps(name, ACCENTS[index % ACCENTS.length])) {
      writeFile(project, step.path, step.content);
      if (step.also) writeFile(project, "index.html", step.also);
    }
    if (index === 0) {
      addMockOffice(project);
      addMockMedia(project);
    }
    commit(project, "agent", `Build “${name}”`, null, { silent: true, at });
  });
}

if (process.argv.includes("--seed")) seed();

server.listen(API_PORT, HOST, () => {
  console.log(`[mock] Studio 2 MOCK server (development tool, no model calls)`);
  console.log(`[mock] UI + API  http://${HOST}:${API_PORT}/`);
  console.log(`[mock] previews  http://${HOST}:${PREVIEW_BASE}/ and up`);
});

for (const signal of ["SIGINT", "SIGTERM"]) {
  process.on(signal, () => {
    for (const project of projects.values()) {
      project.server.close();
      project.server.closeAllConnections?.();
    }
    server.close();
    server.closeAllConnections?.();
    process.exit(0);
  });
}
