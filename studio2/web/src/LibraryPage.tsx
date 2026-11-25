// /library: the skills, connectors and templates the agent can use; everything else in one closed section.

import { useEffect, useMemo, useState } from "react";
import { DataSourcesManager, kindsOrKnown } from "./DataSources";
import { LibraryIndexView } from "./LibraryIndexView";
import { LibraryView, OWNER_ONLY_LINE, scrollSoon, useLibrary, type MoreBlock } from "./LibraryView";
import { LibraryPlacesBlock } from "./LibraryPlacesBlock";
import { ModelsView } from "./ModelsView";
import { ServiceKeys } from "./ServiceKeysBlock";
import { TemplatesManager } from "./TemplatesManager";
import type { Navigate } from "./router";
import type { Theme } from "./theme";
import { IconButton } from "./ui";

/** The Models block lists the connections with their checks, so the list leaves them out. */
const SHOWN_ABOVE = ["connections"] as const;

/** Links that land on something under "Everything else" open it for that visit. */
const MORE_HASHES = ["#models", "#places", "#service-keys", "#data-sources", "#everything"];

const MORE_LINE =
  "Models, keys and data sources, the studio’s own tools and helper agents, and every skill and connector found on this computer.";

interface LibraryPageProps {
  navigate: Navigate;
  theme: Theme;
  onToggleTheme: () => void;
  /** A shared studio: skills can be read, explained and exported, not changed. */
  shared?: boolean;
}

/** `/library#new-skill` (the New page dialog's "A skill") opens New skill at "Describe it". */
export const NEW_SKILL_HASH = "#new-skill";
/** `/library#new-connector` (the New page dialog's "A connector") opens Add a connector at "Describe what you need". */
export const NEW_CONNECTOR_HASH = "#new-connector";

function OwnerOnly() {
  return <p className="lib-empty">{OWNER_ONLY_LINE}</p>;
}

export function LibraryPage({ navigate, theme, onToggleTheme, shared = false }: LibraryPageProps) {
  const state = useLibrary();
  const [hash] = useState(() => (typeof window !== "undefined" ? window.location.hash : ""));
  const startNewSkill = hash === NEW_SKILL_HASH;
  const startNewConnector = hash === NEW_CONNECTOR_HASH;
  const loaded = state.library !== null;
  const kinds = state.library?.data_kinds;

  useEffect(() => {
    document.title = "Library · Studio";
    // Opened once: a reload or Back does not open the dialog again.
    if (window.location.hash === NEW_SKILL_HASH || window.location.hash === NEW_CONNECTOR_HASH) {
      window.history.replaceState(window.history.state, "", window.location.pathname + window.location.search);
    }
    return () => {
      document.title = "Studio";
    };
  }, []);

  // `/library#data-sources` (the Home "Data sources" entry) and the other section links land on
  // their section once the library is drawn (they are under "Everything else", opened for them).
  useEffect(() => {
    if (!loaded || !hash || hash === NEW_SKILL_HASH || hash === NEW_CONNECTOR_HASH) return;
    const timers = scrollSoon(hash.slice(1));
    return () => timers.forEach((timer) => window.clearTimeout(timer));
  }, [loaded, hash]);

  const moreBlocks = useMemo<MoreBlock[]>(
    () => [
      {
        id: "models",
        title: "Models",
        words: "model connection subscription gateway check",
        render: () => (shared ? <OwnerOnly /> : <ModelsView />),
      },
      {
        id: "places",
        title: "Where the studio looks",
        words: "folders files skills connectors settings",
        render: () => <LibraryPlacesBlock onChanged={() => window.location.reload()} />,
      },
      {
        id: "service-keys",
        title: "Keys for other services",
        words: "key api token search",
        render: () => (shared ? <OwnerOnly /> : <ServiceKeys />),
      },
      {
        id: "data-sources",
        title: "Data sources",
        words: "database databases connect postgres mysql",
        render: () => (shared ? <OwnerOnly /> : <DataSourcesManager kinds={kindsOrKnown(kinds)} />),
      },
    ],
    [kinds, shared],
  );

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
            <p className="home-count">The skills, connectors and templates the agent can use when it builds your pages.</p>
          </div>
        </div>
        <LibraryView
          state={state}
          omit={SHOWN_ABOVE}
          shared={shared}
          startNewSkill={startNewSkill}
          startNewConnector={startNewConnector}
          onOpenProject={(id) => navigate(`/p/${id}`)}
          yourTemplates={(query, heading) => <TemplatesManager query={query} heading={heading} guest={shared} />}
          moreBlocks={moreBlocks}
          moreLine={MORE_LINE}
          openMore={MORE_HASHES.includes(hash)}
          indexBlock={({ query, preferKind }) => (
            <LibraryIndexView
              initialIndex={state.library?.index ?? null}
              shared={shared}
              search={query}
              preferKind={preferKind}
              onOpenProject={(id) => navigate(`/p/${id}`)}
              onChanged={state.reload}
            />
          )}
        />
      </main>
    </div>
  );
}
