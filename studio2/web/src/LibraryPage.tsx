// /library: what this studio can do, and the person's data sources.

import { useEffect } from "react";
import { DataSourcesManager, kindsOrKnown } from "./DataSources";
import { LibraryIndexView } from "./LibraryIndexView";
import { LibraryView, useLibrary } from "./LibraryView";
import { ModelsView } from "./ModelsView";
import { TemplatesManager } from "./TemplatesManager";
import type { Navigate } from "./router";
import type { Theme } from "./theme";
import { IconButton } from "./ui";

/** The Models block above lists the connections with their checks, so the list below leaves them out. */
const SHOWN_ABOVE = ["connections"] as const;

interface LibraryPageProps {
  navigate: Navigate;
  theme: Theme;
  onToggleTheme: () => void;
}

export function LibraryPage({ navigate, theme, onToggleTheme }: LibraryPageProps) {
  const state = useLibrary();

  useEffect(() => {
    document.title = "Library · Studio";
    // `/library#data-sources` (the Home "Data sources" entry) lands on that section.
    if (window.location.hash === "#data-sources") {
      document.getElementById("data-sources")?.scrollIntoView({ block: "start" });
    }
    return () => {
      document.title = "Studio";
    };
  }, []);

  return (
    <div className="home library-page">
      <header className="home-bar">
        <span className="home-bar-start">
          <IconButton icon="arrow-left" label="All pages" tipAlign="start" onClick={() => navigate("/")} />
          <span className="wordmark">
            <span className="wordmark-mark" aria-hidden="true" />
            Studio
          </span>
        </span>
        <IconButton
          icon={theme === "dark" ? "sun" : "moon"}
          label={theme === "dark" ? "Switch to light theme" : "Switch to dark theme"}
          tip={theme === "dark" ? "Light theme" : "Dark theme"}
          tipAlign="end"
          onClick={onToggleTheme}
        />
      </header>
      <main className="home-main library-main">
        <div className="home-head">
          <div>
            <h1>Library</h1>
            <p className="home-count">What this studio can do, and the databases you have connected.</p>
          </div>
        </div>
        <section id="models" className="library-block" aria-labelledby="models-title">
          <h2 id="models-title">Models</h2>
          <ModelsView />
        </section>
        <section id="data-sources" className="library-block" aria-labelledby="data-sources-title">
          <h2 id="data-sources-title">Data sources</h2>
          <DataSourcesManager kinds={kindsOrKnown(state.library?.data_kinds)} />
        </section>
        <section id="templates" className="library-block" aria-labelledby="templates-title">
          <h2 id="templates-title">Your templates</h2>
          <TemplatesManager />
        </section>
        <section className="library-block" aria-labelledby="capabilities-title">
          <h2 id="capabilities-title">What the agent can use</h2>
          <LibraryView state={state} omit={SHOWN_ABOVE} />
        </section>
        <section id="everything" className="library-block" aria-labelledby="everything-title">
          <h2 id="everything-title">Everything on this computer</h2>
          <p className="lib-intro lix-lede">
            Before each request the studio picks what fits from these. Say <strong>Always</strong> or{" "}
            <strong>Never</strong> to decide for every project; a project can still decide for itself.
          </p>
          <LibraryIndexView initialIndex={state.library?.index ?? null} />
        </section>
      </main>
    </div>
  );
}
