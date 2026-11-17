// The studio needs signing in again while the file viewer holds unsaved edits (CONTRACT.md,
// "Access from other devices"): the tab stays, says so, and offers the sign-in page in a new tab.
// It checks the studio again every few seconds and when the tab comes back; once a check is
// answered, it is gone and Save works.

import { useEffect, useSyncExternalStore } from "react";
import { api } from "./api";
import { onSignInChange, signedInAgain, signInUrl, signInWaiting } from "./signIn";

const CHECK_MS = 4000;

export function SignInAgain() {
  const waiting = useSyncExternalStore(onSignInChange, signInWaiting);

  useEffect(() => {
    if (!waiting) return;
    document.documentElement.classList.add("signin-waiting");
    let busy = false;
    const check = () => {
      if (busy || document.visibilityState === "hidden") return;
      busy = true;
      // A 401 here keeps it waiting (sending to sign in does nothing while it waits); an answer
      // means the access cookie is good again.
      api.health().then(
        () => {
          busy = false;
          signedInAgain();
        },
        () => {
          busy = false;
        },
      );
    };
    const timer = window.setInterval(check, CHECK_MS);
    const onVisible = () => {
      if (document.visibilityState === "visible") check();
    };
    window.addEventListener("focus", check);
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      document.documentElement.classList.remove("signin-waiting");
      window.clearInterval(timer);
      window.removeEventListener("focus", check);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [waiting]);

  if (!waiting) return null;
  return (
    <div className="signin-again" role="alert">
      <p>
        <strong>This studio needs you to sign in again.</strong> Your unsaved edits are still here. Sign in in a
        new tab, then come back to this tab and press Save.
      </p>
      <a className="btn btn-primary" href={signInUrl(window.location.href)} target="_blank" rel="noopener">
        Sign in in a new tab
      </a>
    </div>
  );
}
