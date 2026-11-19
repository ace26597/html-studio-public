// Home: the list of pages and one prominent way to start a new one.

import type { Example } from "./examples";
import { ExamplesSection } from "./ExamplesSection";
import { useCallback, useEffect, useState } from "react";
import { api, errorMessage } from "./api";
import { appStateLabel, relativeTime } from "./format";
import { Icon, Spinner } from "./icons";
import { NEW_CONNECTOR_HASH, NEW_SKILL_HASH } from "./LibraryPage";
import { NewProjectDialog } from "./NewProjectDialog";
import type { Navigate } from "./router";
import type { Theme } from "./theme";
import { attachLabel, envSkippedNote } from "./attach";
import type { AttachRequest, Project } from "./types";
import { IconButton, Menu, useConfirm, useToast } from "./ui";

interface HomeProps {
  navigate: Navigate;
  theme: Theme;
  onToggleTheme: () => void;
  /** The studio serves the guide and the benchmarks (`Health.site`). */
  site?: boolean;
  /** A shared studio: no skills are added there, so the New page dialog does not offer one. */
  shared?: boolean;
}

/**
 * The guide and the benchmarks, when this studio serves them (CONTRACT.md, "The guide and the
 * benchmarks, inside the studio"). Ordinary links: they are pages of their own, not screens of the app.
 */
export function GuideLinks({ site }: { site?: boolean }) {
  if (!site) return null;
  return (
    <>
      <a className="home-nav-link" href="/site/" aria-label="How it works">
        <Icon name="info" size={15} /> <span className="home-nav-text">How it works</span>
      </a>
      <a className="home-nav-link" href="/site/benchmarks/" aria-label="Benchmarks">
        <Icon name="chart" size={15} /> <span className="home-nav-text">Benchmarks</span>
      </a>
    </>
  );
}

/**
 * A static miniature of the page. Scripts are off (empty sandbox) so a grid of
 * thumbnails stays cheap, and it only appears once the preview answers.
 */
function Thumbnail({ url }: { url: string }) {
  const [reachable, setReachable] = useState(false);
  useEffect(() => {
    const controller = new AbortController();
    fetch(url, { mode: "no-cors", signal: controller.signal }).then(
      () => setReachable(true),
      () => {},
    );
    return () => controller.abort();
  }, [url]);
  return (
    <div className="thumb" aria-hidden="true">
      {reachable && <iframe src={url} sandbox="" loading="lazy" tabIndex={-1} title="" scrolling="no" />}
    </div>
  );
}

function AppBadge({ state }: { state: string }) {
  // `Project.app` has no exit code, so a crash is named without one here.
  const { label, tone } = appStateLabel({ state, exitCode: null });
  return (
    <span className={`app-badge is-${tone}`} title={`This page has its own app: ${label.toLowerCase()}`}>
      <span className="app-dot" aria-hidden="true" />
      App · {label}
    </span>
  );
}

function ProjectCard({
  project,
  now,
  onOpen,
  onRename,
  onDelete,
}: {
  project: Project;
  now: number;
  onOpen: () => void;
  onRename: (name: string) => void;
  onDelete: () => void;
}) {
  const [renaming, setRenaming] = useState(false);
  const [name, setName] = useState(project.name);
  const [menuOpen, setMenuOpen] = useState(false);

  const commit = () => {
    setRenaming(false);
    const next = name.trim();
    if (next && next !== project.name) onRename(next);
    else setName(project.name);
  };

  return (
    <li className={`card${menuOpen ? " is-menu-open" : ""}`}>
      <a
        className="card-link"
        href={`/p/${project.id}`}
        aria-label={`Open ${project.name}`}
        onClick={(event) => {
          if (event.metaKey || event.ctrlKey || event.shiftKey || event.button !== 0) return;
          event.preventDefault();
          onOpen();
        }}
      >
        <Thumbnail url={project.preview_url} />
      </a>
      <div className="card-meta">
        <div className="card-text">
          {renaming ? (
            <input
              className="card-input"
              aria-label="Page name"
              value={name}
              autoFocus
              maxLength={120}
              onFocus={(event) => event.target.select()}
              onChange={(event) => setName(event.target.value)}
              onBlur={commit}
              onKeyDown={(event) => {
                if (event.key === "Enter") commit();
                if (event.key === "Escape") {
                  setName(project.name);
                  setRenaming(false);
                }
              }}
            />
          ) : (
            <span className="card-name" title={project.name}>
              {project.name}
            </span>
          )}
          {project.attach && (
            <span className="card-origin" title={project.attach.path}>
              {attachLabel(project.attach)}
            </span>
          )}
          <span className="card-time">
            {project.app?.configured && <AppBadge state={project.app.state} />}
            {project.running_turn ? (
              <span className="card-working">
                <span className="status-dot" aria-hidden="true" /> Working…
              </span>
            ) : (
              `Edited ${relativeTime(project.updated_at, now)}`
            )}
          </span>
        </div>
        <Menu
          label={`Actions for ${project.name}`}
          onOpenChange={setMenuOpen}
          entries={[
            {
              label: "Rename",
              icon: "pencil",
              onSelect: () => {
                setName(project.name);
                setRenaming(true);
              },
            },
            { label: "Delete", icon: "trash", danger: true, onSelect: onDelete },
          ]}
          trigger={(props) => (
            <button type="button" className="icon-btn card-more" aria-label={`Actions for ${project.name}`} {...props}>
              <Icon name="more" size={16} />
            </button>
          )}
        />
      </div>
    </li>
  );
}

export function Home({ navigate, theme, onToggleTheme, site, shared = false }: HomeProps) {
  const toast = useToast();
  const confirm = useConfirm();
  const [projects, setProjects] = useState<Project[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);
  const [now, setNow] = useState(() => Date.now());
  const [examples, setExamples] = useState<Example[]>([]);
  const [opening, setOpening] = useState<string | null>(null);

  useEffect(() => {
    // Examples are an extra: a studio without them, or one that cannot list them, shows none.
    api.listExamples().then(setExamples, () => setExamples([]));
  }, []);

  const openExample = async (example: Example) => {
    if (opening || creating) return;
    setOpening(example.id);
    try {
      const project = await api.createProject({ example: example.id });
      navigate(`/p/${project.id}`);
    } catch (cause) {
      toast({ message: errorMessage(cause), kind: "error" });
      setOpening(null);
    }
  };

  const load = useCallback(() => {
    api.listProjects().then(
      (list) => {
        setProjects(list);
        setError(null);
        setNow(Date.now());
      },
      (cause) => setError(errorMessage(cause)),
    );
  }, []);

  useEffect(() => {
    load();
    const timer = window.setInterval(() => setNow(Date.now()), 30_000);
    return () => window.clearInterval(timer);
  }, [load]);

  const create = async (template: string | null) => {
    if (creating) return;
    setCreating(true);
    setCreateError(null);
    try {
      const project = await api.createProject(template ? { template } : {});
      navigate(`/p/${project.id}`);
    } catch (cause) {
      setCreateError(errorMessage(cause));
      setCreating(false);
    }
  };

  const attachApp = async (request: AttachRequest) => {
    if (creating) return;
    setCreating(true);
    setCreateError(null);
    try {
      const project = await api.attachProject(request);
      // Ticked key files the copy did not take: said here, where the copy was made, and kept in
      // the project's "where it came from" line.
      const skipped = project.attach ? envSkippedNote(project.attach) : null;
      if (skipped) toast({ message: skipped, kind: "info", duration: 30000 });
      navigate(`/p/${project.id}`);
    } catch (cause) {
      setCreateError(errorMessage(cause));
      setCreating(false);
    }
  };

  const openDialog = () => {
    setCreateError(null);
    setDialogOpen(true);
  };

  const rename = (project: Project, name: string) => {
    setProjects((list) => list?.map((item) => (item.id === project.id ? { ...item, name } : item)) ?? list);
    api.patchProject(project.id, { name }).then(
      (updated) => setProjects((list) => list?.map((item) => (item.id === updated.id ? updated : item)) ?? list),
      (cause) => {
        toast({ message: errorMessage(cause), kind: "error" });
        load();
      },
    );
  };

  const remove = async (project: Project) => {
    const ok = await confirm({
      title: `Delete “${project.name}”?`,
      body: "The page and its history are removed from the studio.",
      confirmLabel: "Delete",
      danger: true,
    });
    if (!ok) return;
    try {
      await api.deleteProject(project.id);
      setProjects((list) => list?.filter((item) => item.id !== project.id) ?? list);
      toast({ message: `Deleted “${project.name}”` });
    } catch (cause) {
      toast({ message: errorMessage(cause), kind: "error" });
    }
  };

  const newButton = (
    <button type="button" className="btn btn-primary btn-large" onClick={openDialog} disabled={creating}>
      {creating ? <Spinner size={15} /> : <Icon name="plus" size={16} strokeWidth={2.2} />}
      New page
    </button>
  );

  return (
    <div className="home">
      <header className="home-bar">
        <span className="wordmark">
          <span className="wordmark-mark" aria-hidden="true" />
          Studio
        </span>
        <nav className="home-nav" aria-label="Studio">
          <a
            className="home-nav-link"
            href="/library"
            aria-label="Library"
            onClick={(event) => {
              if (event.metaKey || event.ctrlKey || event.shiftKey || event.button !== 0) return;
              event.preventDefault();
              navigate("/library");
            }}
          >
            <Icon name="book" size={15} /> <span className="home-nav-text">Library</span>
          </a>
          <a
            className="home-nav-link"
            href="/library#data-sources"
            aria-label="Data sources"
            onClick={(event) => {
              if (event.metaKey || event.ctrlKey || event.shiftKey || event.button !== 0) return;
              event.preventDefault();
              navigate("/library#data-sources");
            }}
          >
            <Icon name="database" size={15} /> <span className="home-nav-text">Data sources</span>
          </a>
          <a
            className="home-nav-link"
            href="/setup"
            aria-label="Setup"
            onClick={(event) => {
              if (event.metaKey || event.ctrlKey || event.shiftKey || event.button !== 0) return;
              event.preventDefault();
              navigate("/setup");
            }}
          >
            <Icon name="wrench" size={15} /> <span className="home-nav-text">Setup</span>
          </a>
          <GuideLinks site={site} />
        </nav>
        <IconButton
          icon={theme === "dark" ? "sun" : "moon"}
          label={theme === "dark" ? "Switch to light theme" : "Switch to dark theme"}
          tip={theme === "dark" ? "Light theme" : "Dark theme"}
          tipAlign="end"
          onClick={onToggleTheme}
        />
      </header>

      <main className="home-main">
        {projects === null && !error && (
          <div className="home-loading">
            <Spinner size={18} />
          </div>
        )}

        {error && projects === null && (
          <div className="home-empty">
            <h1>Your pages couldn’t be loaded</h1>
            <p>{error}</p>
            <button type="button" className="btn" onClick={load}>
              Try again
            </button>
          </div>
        )}

        {projects && projects.length === 0 && (
          <div className="home-empty">
            <div className="home-empty-art" aria-hidden="true">
              <span />
            </div>
            <h1>Start with a blank page</h1>
            <p>Say what you want it to become, and watch it take shape as the agent works.</p>
            {newButton}
          </div>
        )}

        {projects && projects.length > 0 && (
          <>
            <div className="home-head">
              <div>
                <h1>Pages</h1>
                <p className="home-count">
                  {projects.length} {projects.length === 1 ? "page" : "pages"}
                </p>
              </div>
              {newButton}
            </div>
            <ul className="grid">
              {projects.map((project) => (
                <ProjectCard
                  key={project.id}
                  project={project}
                  now={now}
                  onOpen={() => navigate(`/p/${project.id}`)}
                  onRename={(name) => rename(project, name)}
                  onDelete={() => void remove(project)}
                />
              ))}
            </ul>
          </>
        )}
        {projects && <ExamplesSection examples={examples} opening={opening} onOpen={(example) => void openExample(example)} />}
      </main>
      {dialogOpen && (
        <NewProjectDialog
          creating={creating}
          createError={createError}
          onCreate={(template) => void create(template)}
          onAttach={(request) => void attachApp(request)}
          onClose={() => setDialogOpen(false)}
          onSkill={
            shared
              ? undefined
              : () => {
                  setDialogOpen(false);
                  navigate(`/library${NEW_SKILL_HASH}`);
                }
          }
          onConnector={
            shared
              ? undefined
              : () => {
                  setDialogOpen(false);
                  navigate(`/library${NEW_CONNECTOR_HASH}`);
                }
          }
        />
      )}
    </div>
  );
}
