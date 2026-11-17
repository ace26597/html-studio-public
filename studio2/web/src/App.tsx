import { useCallback, useEffect, useState } from "react";
import { api } from "./api";
import { Home } from "./Home";
import { Icon, Spinner } from "./icons";
import { LibraryPage } from "./LibraryPage";
import { ProjectPage } from "./Project";
import { useRoute } from "./router";
import { SetupPage } from "./SetupPage";
import { SignInAgain } from "./SignInAgain";
import { goToSignIn, healthFailure, type HealthFailure } from "./signIn";
import { useTheme } from "./theme";
import type { Health } from "./types";
import { ConfirmProvider, ToastProvider } from "./ui";

const START_COMMAND = "studio start";

function ServerDown({ onRetry, checking }: { onRetry: () => void; checking: boolean }) {
  const [copied, setCopied] = useState(false);
  const copy = () => {
    navigator.clipboard?.writeText(START_COMMAND).then(
      () => {
        setCopied(true);
        window.setTimeout(() => setCopied(false), 1600);
      },
      () => {},
    );
  };
  return (
    <div className="fullpage">
      <div className="fullpage-card" role="alert">
        <h1>Studio server is not running</h1>
        <p>
          Start it from a terminal. This page connects by itself once it is up. (Without the <code>studio</code>{" "}
          command: <code>cd studio2 &amp;&amp; .venv/bin/python -m studio2_server</code>.)
        </p>
        <div className="command">
          <code>{START_COMMAND}</code>
          <button type="button" className="btn btn-small" onClick={copy}>
            {copied ? "Copied" : "Copy"}
          </button>
        </div>
        <div className="fullpage-actions">
          <button type="button" className="btn" onClick={onRetry} disabled={checking}>
            {checking ? <Spinner size={13} /> : <Icon name="refresh" size={14} />}
            Check again
          </button>
        </div>
      </div>
    </div>
  );
}

/** The studio answered, but not with its health: say what it said instead of "not running". */
function ServerError({ message, onRetry, checking }: { message: string; onRetry: () => void; checking: boolean }) {
  return (
    <div className="fullpage">
      <div className="fullpage-card" role="alert">
        <h1>The studio couldn’t be opened</h1>
        <p>{message}</p>
        <div className="fullpage-actions">
          <button type="button" className="btn" onClick={onRetry} disabled={checking}>
            {checking ? <Spinner size={13} /> : <Icon name="refresh" size={14} />}
            Check again
          </button>
        </div>
      </div>
    </div>
  );
}

type Failed = { failed: Exclude<HealthFailure, { kind: "sign-in" }> };

export function App() {
  const [route, navigate] = useRoute();
  const [theme, toggleTheme] = useTheme();
  const [health, setHealth] = useState<Health | Failed | null>(null);
  const [checking, setChecking] = useState(false);

  const check = useCallback(() => {
    setChecking(true);
    api.health().then(
      (result) => {
        setHealth(result);
        setChecking(false);
      },
      (error) => {
        const failure = healthFailure(error);
        // An expired or changed access key: the sign-in page, which brings the person back here.
        if (failure.kind === "sign-in") goToSignIn();
        else setHealth({ failed: failure });
        setChecking(false);
      },
    );
  }, []);

  useEffect(() => {
    check();
  }, [check]);

  // While the server is down (or answers with an error), keep looking for it.
  const failed = health !== null && "failed" in health;
  useEffect(() => {
    if (!failed) return;
    const timer = window.setInterval(check, 3000);
    return () => window.clearInterval(timer);
  }, [failed, check]);

  let page;
  if (health === null) {
    page = (
      <div className="fullpage">
        <Spinner size={18} />
      </div>
    );
  } else if ("failed" in health) {
    page =
      health.failed.kind === "down" ? (
        <ServerDown onRetry={check} checking={checking} />
      ) : (
        <ServerError message={health.failed.message} onRetry={check} checking={checking} />
      );
  } else if (route.name === "setup" || (route.name === "home" && health.setup?.needed)) {
    // Nothing is configured yet: the first-run screen stands in for the home page until it is
    // saved or skipped. `/setup` opens the same screen at any time.
    page = (
      <SetupPage
        firstRun={route.name !== "setup"}
        navigate={navigate}
        theme={theme}
        onToggleTheme={toggleTheme}
        onDone={check}
      />
    );
  } else if (route.name === "home") {
    page = <Home navigate={navigate} theme={theme} onToggleTheme={toggleTheme} />;
  } else if (route.name === "library") {
    page = <LibraryPage navigate={navigate} theme={theme} onToggleTheme={toggleTheme} />;
  } else if (route.name === "project") {
    page = (
      <ProjectPage
        key={route.id}
        id={route.id}
        health={health}
        navigate={navigate}
        theme={theme}
        onToggleTheme={toggleTheme}
      />
    );
  } else {
    page = (
      <div className="fullpage">
        <div className="fullpage-card">
          <h1>There’s nothing at this address</h1>
          <div className="fullpage-actions">
            <button type="button" className="btn btn-primary" onClick={() => navigate("/")}>
              Back to all pages
            </button>
          </div>
        </div>
      </div>
    );
  }

  return (
    <ToastProvider>
      <ConfirmProvider>
        {page}
        <SignInAgain />
      </ConfirmProvider>
    </ToastProvider>
  );
}
