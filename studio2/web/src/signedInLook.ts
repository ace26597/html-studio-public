// The project's menu → "Let the agent use my sign-in" (CONTRACT.md, "Seeing an app signed in as
// the person"). The same entries in both of the project's menus.

import type { Project } from "./types";
import type { MenuEntry } from "./ui";

/** The server names the option with these same words when it tells the agent why it was not used. */
export const SIGNED_IN_LABEL = "Let the agent use my sign-in";

/** What the tick shows: the server's effective value; never on for a shared studio. */
export function signedInLooks(project: Pick<Project, "signed_in_looks" | "attach"> | null, shared: boolean): boolean {
  if (!project || shared) return false;
  return typeof project.signed_in_looks === "boolean" ? project.signed_in_looks : !project.attach;
}

/** A short section that says what it means (it wraps; a row's hint does not), then the tick. */
export function signedInEntries(
  project: Pick<Project, "signed_in_looks" | "attach"> | null,
  shared: boolean,
  onChange: (on: boolean) => void,
): MenuEntry[] {
  const on = signedInLooks(project, shared);
  return [
    {
      type: "section",
      label: "This app's sign-in",
      note: shared
        ? "On a shared studio the agent never uses a person's sign-in."
        : "When ticked, the agent sees this app signed in as you in this browser: it can then click and type in the app as you.",
    },
    {
      label: SIGNED_IN_LABEL,
      checked: on,
      toggle: true,
      disabled: shared || !project,
      onSelect: () => onChange(!on),
    },
  ];
}
